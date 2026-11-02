"""라운드 생성·완료·중단과 스캔마다 도는 정리(reconcile) (설계 04 문서 1절, 06 문서 3절).

여러 문서를 고치는 동작은 커밋 지점 쓰기 하나(라운드 상태)를 먼저 하고 나머지를 멱등하게 따라 쓴다.
중간에 멈추면 reconcile 이 마저 쓴다. 트랜잭션은 쓰지 않는다.
"""

import logging
from collections.abc import Mapping
from uuid import uuid4

from app.agents.personas import get_persona
from app.core.exceptions import ConcurrentUpdateError, InvalidTransitionError
from app.models.enums import AgentSlot, PostStatus, RoundAbortReason, RoundStatus, TopicRegistryStatus
from app.models.model_binding import ModelBinding
from app.models.post_draft import PostDraft, build_review_assignments
from app.models.publishing_round import PublishingRound
from app.models.topic import CategoryOption
from app.repositories.agent_round import PublishingRoundRepository
from app.repositories.post_draft import PostDraftRepository
from app.repositories.topic_registry import TopicRegistryRepository

logger = logging.getLogger(__name__)

_ACTIVE_DRAFT_STATUSES = tuple(
    status for status in PostStatus if status not in (PostStatus.PUBLISHED, PostStatus.REJECTED)
)


class RoundService:
    """라운드 수명 주기. 글 진행은 DraftPipelineService, 주제 선정은 TopicSelectionService 가 맡는다."""

    def __init__(
        self,
        round_repository: PublishingRoundRepository,
        draft_repository: PostDraftRepository,
        topic_registry_repository: TopicRegistryRepository,
    ) -> None:
        self._rounds = round_repository
        self._drafts = draft_repository
        self._topics = topic_registry_repository

    async def get(self, round_id: str) -> PublishingRound | None:
        return await self._rounds.get(round_id)

    async def create_round(
        self,
        *,
        theme_keyword: str | None,
        slot_bindings: Mapping[AgentSlot, ModelBinding],
        categories: list[CategoryOption],
    ) -> PublishingRound:
        """검사(바인딩 3개, 페르소나 존재, 렌즈 매트릭스)를 마친 라운드를 TOPIC_SELECTION 으로 만든다 (설계 03 문서 3절).

        다른 라운드가 주제 선정 중이면 InvalidTransitionError(409) 이고 문서는 남지 않는다. 카테고리 스냅샷은 호출자가 읽어 넘긴다.
        """
        bindings = dict(slot_bindings)
        if set(bindings) != set(AgentSlot):
            raise ValueError("slot_bindings must bind every slot (A, B, C)")
        primary_lenses = {slot: get_persona(binding.persona_id).primary_lens for slot, binding in bindings.items()}
        for slot in AgentSlot:
            build_review_assignments(slot, primary_lenses)  # 세 렌즈가 모두 들어가는지. 아니면 ValueError
        # round_seq 는 동률 판정(round_seq % 3)에만 쓰므로 기존 라운드 수 + 1 이면 된다. 동시 생성은 부분 unique 인덱스가 막는다.
        existing = 0
        for status in RoundStatus:
            existing += len(await self._rounds.list_by_status(status))
        publishing_round = PublishingRound(
            round_id=uuid4().hex,
            round_seq=existing + 1,
            theme_keyword=theme_keyword,
            slot_bindings=bindings,
            categories=categories,
        )
        await self._rounds.add(publishing_round)
        return publishing_round

    def build_draft(self, publishing_round: PublishingRound, slot: AgentSlot, token_limit: int) -> PostDraft:
        """확정된 주제로 글 문서를 만든다. ID 는 결정론적이라 다시 만들어도 같은 문서를 가리킨다."""
        attempt = publishing_round.latest_attempt
        if attempt is None or slot not in attempt.locked:
            raise ValueError(f"round {publishing_round.round_id} has no locked topic for slot {slot}")
        primary_lenses = {
            bound_slot: get_persona(binding.persona_id).primary_lens
            for bound_slot, binding in publishing_round.slot_bindings.items()
        }
        return PostDraft(
            draft_id=publishing_round.draft_id_for(slot),
            round_id=publishing_round.round_id,
            author_slot=slot,
            token_limit=token_limit,
            topic=attempt.locked[slot].proposal,
            reviewer_assignments=build_review_assignments(slot, primary_lenses),
        )

    async def close_if_complete(self, round_id: str) -> bool:
        """IN_PROGRESS 라운드의 글 3편이 모두 종결이면 COMPLETED 로 닫는다. 닫았으면 True.

        저장 충돌(중단이 먼저 ABORTED 를 썼다)이면 닫지 않고 False 다 (설계 07 문서 D-34).
        """
        publishing_round = await self._rounds.get(round_id)
        if publishing_round is None or publishing_round.status != RoundStatus.IN_PROGRESS:
            return False
        drafts = await self._drafts.list_by_round(round_id)
        if len(drafts) != len(AgentSlot) or not all(draft.is_terminal for draft in drafts):
            return False
        publishing_round.complete()
        try:
            await self._rounds.save(publishing_round)
        except ConcurrentUpdateError:
            return False
        return True

    async def abort_round(self, round_id: str, reason: RoundAbortReason = RoundAbortReason.MANUAL) -> None:
        """라운드를 ABORTED 로 먼저 쓰고 글·레지스트리를 정리한다 (설계 04 문서 1절 표). 종결 라운드면 InvalidTransitionError."""
        while True:
            publishing_round = await self._rounds.get(round_id)
            if publishing_round is None:
                raise InvalidTransitionError(f"round {round_id} does not exist")
            publishing_round.abort(reason)
            try:
                await self._rounds.save(publishing_round)
            except ConcurrentUpdateError:
                continue  # 코루틴의 저장과 겹쳤다. 다시 읽어 전제(종결 아님)가 맞으면 재적용한다
            break
        await self._clean_up(publishing_round)

    async def reconcile(self) -> None:
        """1절 표의 따라 쓰기를 마저 한다. 스캔마다 돈다. 되풀이해도 결과가 같다.

        순서: ABORTED 라운드에 남은 글을 거둔다 → RESERVED 레지스트리를 글의 종결 상태에 맞춘다 → IN_PROGRESS 라운드 완료 확인.
        """
        rounds_by_id: dict[str, PublishingRound | None] = {}
        for draft in await self._drafts.list_by_status(*_ACTIVE_DRAFT_STATUSES):
            if draft.round_id not in rounds_by_id:
                rounds_by_id[draft.round_id] = await self._rounds.get(draft.round_id)
            publishing_round = rounds_by_id[draft.round_id]
            if publishing_round is not None and publishing_round.status == RoundStatus.ABORTED:
                await self._reject_by_abort(draft, publishing_round.abort_reason or RoundAbortReason.MANUAL)
        for entry in await self._topics.list_active():
            if entry.status != TopicRegistryStatus.RESERVED:
                continue
            draft = await self._drafts.get(entry.draft_id)
            if draft is None:
                continue
            if draft.status == PostStatus.PUBLISHED and draft.publication is not None:
                await self._topics.set_status(
                    entry.entry_id, TopicRegistryStatus.PUBLISHED, draft.publication.published_at
                )
            elif draft.status == PostStatus.REJECTED:
                await self._topics.set_status(entry.entry_id, TopicRegistryStatus.RELEASED)
        for publishing_round in await self._rounds.list_by_status(RoundStatus.IN_PROGRESS):
            await self.close_if_complete(publishing_round.round_id)

    async def _clean_up(self, publishing_round: PublishingRound) -> None:
        reason = publishing_round.abort_reason or RoundAbortReason.MANUAL
        for draft in await self._drafts.list_by_round(publishing_round.round_id):
            if not draft.is_terminal:
                await self._reject_by_abort(draft, reason)
        for entry in await self._topics.list_by_round(publishing_round.round_id):
            if entry.status != TopicRegistryStatus.PUBLISHED:
                await self._topics.set_status(entry.entry_id, TopicRegistryStatus.RELEASED)

    async def _reject_by_abort(self, draft: PostDraft, reason: RoundAbortReason) -> None:
        draft.reject_by_abort(reason)
        try:
            await self._drafts.save(draft)
        except ConcurrentUpdateError:
            # 글 코루틴이 방금 저장했다. 다음 스캔의 정리가 다시 거둔다
            logger.info("draft %s changed while aborting; reconcile will retry", draft.draft_id)
