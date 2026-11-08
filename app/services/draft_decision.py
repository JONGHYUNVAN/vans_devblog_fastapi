"""사람 결정: 승인, 격리 해소, FAILED 재개, FAILED 폐기 (설계 06 문서 3절 draft_decision, 03 문서 23~26a행).

글을 읽어 도메인 메서드를 부르고 revision 조건으로 저장한다. 충돌이면 다시 읽어 전제 상태가 여전히 맞을 때만 재적용하고,
아니면 도메인 메서드가 InvalidTransitionError(409) 를 던진다. 진행은 하지 않는다. 다음 스캔이 글 코루틴을 띄운다.
"""

import logging
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime

from pydantic import TypeAdapter

from app.core.config import Settings
from app.core.exceptions import ConcurrentUpdateError, InvalidRequestError, NotFoundError
from app.models.enums import EscalationDecisionKind, PostLevel, PostStatus, TopicRegistryStatus
from app.models.post_draft import PostDraft, Publication
from app.repositories.agent_call import AgentCallRepository
from app.repositories.post_draft import PostDraftRepository
from app.repositories.topic_registry import TopicRegistryRepository
from app.services.derived_records import DerivedRecordsWriter
from app.services.pipeline_versions import PipelineVersionService
from app.services.publishing import build_publish_note
from app.services.round import RoundService

logger = logging.getLogger(__name__)


class DraftDecisionService:
    """사람 결정을 글에 적용하고 저장한다. 라우터(agent_rounds)와 CLI(approve·resolve·retry·discard·label-level)가 같이 쓴다.

    결정은 코루틴이 돌지 않는 상태(승인 대기·ESCALATED·FAILED)에만 적용된다. 돌고 있는 글에 쓰면 호출 결과 반영과 같은 문서를 두고 경쟁하기 때문이다(04 문서 1절). 전제 상태가 아니면 InvalidTransitionError(409), 입력 오류는 InvalidRequestError(400), 없는 글은 NotFoundError(404).
    """

    def __init__(
        self,
        *,
        draft_repository: PostDraftRepository,
        agent_call_repository: AgentCallRepository,
        topic_registry_repository: TopicRegistryRepository,
        round_service: RoundService,
        settings: Settings,
        version_service: PipelineVersionService,
        derived_writer: DerivedRecordsWriter | None = None,
    ) -> None:
        self._drafts = draft_repository
        self._calls = agent_call_repository
        self._topics = topic_registry_repository
        self._rounds = round_service
        self._settings = settings
        self._versions = version_service  # 승인 토글·머리말 문구는 라운드 버전의 조절 값이다
        self._derived = derived_writer

    async def approve(self, draft_id: str, *, approved_by: str) -> PostDraft:
        """발행 승인. 그 버전에 대한 것이다. 다음 스캔이 글 코루틴을 띄워 발행한다. 토글이 꺼져 있거나 이미 승인됐으면 409."""

        async def mutate(draft: PostDraft) -> None:
            publishing_round = await self._rounds.get(draft.round_id)
            if publishing_round is None:
                raise NotFoundError(f"round {draft.round_id} does not exist")
            # 토글은 라운드가 고정한 버전의 값이다. 최신 버전이 토글을 껐어도 켜진 버전으로 만든 라운드는 승인을 요구한다(D-73)
            parameters = await self._versions.parameters_for(publishing_round)
            draft.record_approval(approved_by, parameters.publish.requires_approval)

        return await self._apply(draft_id, mutate)

    async def resolve_escalation(
        self,
        draft_id: str,
        decision: EscalationDecisionKind,
        *,
        decided_by: str,
        instruction: str | None = None,
        external_post_id: str | None = None,
    ) -> PostDraft:
        """ESCALATED 해소. CONFIRM_PUBLISHED 는 사람이 블로그에서 찾은 게시글 id 로 Publication 을 만든다(Post BE 를 부르지 않는다)."""

        async def mutate(draft: PostDraft) -> None:
            publication = None
            if decision == EscalationDecisionKind.CONFIRM_PUBLISHED:
                if not external_post_id:
                    raise InvalidRequestError("CONFIRM_PUBLISHED needs the external_post_id found on the blog")
                publication = await self._confirmed_publication(draft, external_post_id)
            draft.resolve_escalation(decision, decided_by=decided_by, instruction=instruction, publication=publication)

        return await self._apply(draft_id, mutate)

    async def retry(self, draft_id: str, *, token_limit: int | None = None) -> PostDraft:
        """FAILED 재개. token_limit 은 어느 사유든 받을 수 있고 현재 사용량보다 커야 한다."""

        async def mutate(draft: PostDraft) -> None:
            if token_limit is not None:
                used = (await self._calls.sum_usage(draft.draft_id)).total_tokens
                if token_limit <= used:
                    raise InvalidRequestError(f"token_limit {token_limit} must exceed the {used} tokens already used")
            draft.retry_from_failed(token_limit)

        return await self._apply(draft_id, mutate)

    async def discard(self, draft_id: str, *, decided_by: str) -> PostDraft:
        """FAILED 글 하나만 폐기한다."""

        async def mutate(draft: PostDraft) -> None:
            draft.discard(decided_by)

        return await self._apply(draft_id, mutate)

    async def label_level(self, draft_id: str, level: int) -> PostDraft:
        """사용자가 매긴 수준. 평가자와의 일치도를 재는 데 쓰고 흐름은 바꾸지 않는다.

        저장 때는 검증되지 않아, 틀린 값이 들어가면 글을 읽을 때마다 실패해 러너 스캔이 멈춘다. 그래서 여기서 막는다.
        """

        async def mutate(draft: PostDraft) -> None:
            draft.user_level = TypeAdapter(PostLevel).validate_python(level)

        return await self._apply(draft_id, mutate)

    async def _apply(self, draft_id: str, mutate: Callable[[PostDraft], Awaitable[None]]) -> PostDraft:
        # 읽기 → 도메인 전이 → revision 조건 저장. 결정 대상 글에는 러너가 태스크를 띄우지 않으므로 호출 결과 반영과는 겹치지 않지만,
        # 라운드 중단(reconcile)과는 겹칠 수 있다. 충돌이면 다시 읽어 전제 상태가 여전히 맞을 때만 재적용된다(아니면 mutate 가 409)
        while True:
            draft = await self._drafts.get(draft_id)
            if draft is None:
                raise NotFoundError(f"draft {draft_id} does not exist")
            try:
                await mutate(draft)
            except ValueError as exc:  # 도메인 입력 검사(지시문 없음 등)
                raise InvalidRequestError(str(exc)) from exc
            try:
                await self._drafts.save(draft)
            except ConcurrentUpdateError:
                continue  # 다시 읽어 전제 상태가 맞으면 재적용한다. 아니면 mutate 가 409 를 던진다
            break
        if draft.is_terminal:
            await self._follow_up(draft)
        return draft

    async def _follow_up(self, draft: PostDraft) -> None:
        """종결 결정 뒤 레지스트리를 따라 쓰고 라운드 완료를 확인한다 (04 문서 1절 표)."""
        if draft.status == PostStatus.PUBLISHED and draft.publication is not None:
            await self._topics.set_status(draft.draft_id, TopicRegistryStatus.PUBLISHED, draft.publication.published_at)
            if self._derived is not None:
                try:
                    await self._derived.write(draft, built_by="publish")
                except Exception:  # 결정은 끝났다. 파생 문서는 정리가 마저 쓴다 (설계 08 문서 2.2절)
                    logger.exception("draft %s derived records deferred to reconcile", draft.draft_id)
        else:
            await self._topics.set_status(draft.draft_id, TopicRegistryStatus.RELEASED)
        await self._rounds.close_if_complete(draft.round_id)

    async def _confirmed_publication(self, draft: PostDraft, external_post_id: str) -> Publication:
        # 사람이 블로그에서 찾은 게시글이라 Post BE 를 다시 부르지 않는다. 노트는 발행 때와 같은 규칙으로 다시 만들어 기록만 남긴다.
        # 버전이 없어도 결정은 막지 않으므로 parameters_or_default 다
        intent = draft.publication_intent
        version = draft.publishable_version
        if intent is None and version is None:
            raise InvalidRequestError(f"draft {draft.draft_id} has no version that could have been published")
        publishing_round = await self._rounds.get(draft.round_id)
        note = ""
        if publishing_round is not None:
            parameters = await self._versions.parameters_or_default(publishing_round)
            note = build_publish_note(draft, publishing_round, parameters.publish.note)
        return Publication(
            published_at=datetime.now(UTC),
            final_version=intent.final_version if intent is not None else version.version_number,  # type: ignore[union-attr]
            external_post_id=external_post_id,
            peer_review_note=note,
            approved_by=draft.approved_by,
        )
