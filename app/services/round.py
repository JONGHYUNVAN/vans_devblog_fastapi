"""라운드 생성·완료·중단과 스캔마다 도는 정리(reconcile) (설계 04 문서 1절, 06 문서 3절).

여러 문서를 고치는 동작은 커밋 지점 쓰기 하나(라운드 상태)를 먼저 하고 나머지를 멱등하게 따라 쓴다.
중간에 멈추면 reconcile 이 마저 쓴다. 트랜잭션은 쓰지 않는다.
라운드는 생성 때 파이프라인 버전(프롬프트·라우트·페르소나·조절 값)을 고정한다(09 문서 D-73). 라우트의 유효 스냅샷은 그 버전의 routes 에서 만든다.
"""

import logging
from collections.abc import Collection, Iterable, Mapping
from typing import Any
from uuid import uuid4

from app.agents.personas import get_persona
from app.agents.prompts.registry import validate_prompts
from app.clients.publisher.base import PostCategory, Publisher
from app.core.exceptions import ConcurrentUpdateError, InvalidTransitionError
from app.models.enums import AgentSlot, PostStatus, RoundAbortReason, RoundStatus, TopicRegistryStatus
from app.models.model_binding import CallModel, ModelBinding
from app.models.pipeline import PipelineVersion
from app.models.post_draft import PostDraft, build_lens_assignments
from app.models.publishing_round import PublishingRound
from app.models.routing import RoutingSnapshot, build_snapshot, select_writer, validate_snapshot
from app.models.topic import CategoryOption
from app.repositories.agent_round import PublishingRoundRepository
from app.repositories.model_route import ModelRouteRepository
from app.repositories.post_draft import PostDraftRepository
from app.repositories.topic_registry import TopicRegistryRepository
from app.services.derived_records import DerivedRecordsWriter
from app.services.pipeline_versions import PipelineVersionService

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
        publisher: Publisher | None = None,
        version_service: PipelineVersionService | None = None,
        route_repository: ModelRouteRepository | None = None,
        providers: Iterable[str] = (),
        main_categories: Iterable[str] = (),
        derived_writer: DerivedRecordsWriter | None = None,
    ) -> None:
        self._rounds = round_repository
        self._drafts = draft_repository
        self._topics = topic_registry_repository
        self._publisher = publisher
        self._versions = version_service  # 라운드 생성이 고정할 파이프라인 버전
        self._routes = route_repository  # 옛 agent_routes. bootstrap 의 입력과 seed-routes 만 쓴다(3단계에서 삭제)
        self._derived = (
            derived_writer  # 정리가 PUBLISHED 글의 파생 문서(post_glossary·post_provenance)를 마저 쓴다 (설계 08 문서)
        )
        self._providers = list(providers)  # API 키가 있는 벤더. 라운드 스냅샷에서 키 없는 후보를 뺀다
        self._main_categories = frozenset(main_categories)  # 블로그에 화면이 있는 메인 카테고리 값 (D-60)

    async def get(self, round_id: str) -> PublishingRound | None:
        return await self._rounds.get(round_id)

    async def list_rounds(self, status: RoundStatus | None = None) -> list[PublishingRound]:
        """최근 생성 순. status 가 없으면 전부."""
        statuses = [status] if status is not None else list(RoundStatus)
        rounds = [publishing_round for each in statuses for publishing_round in await self._rounds.list_by_status(each)]
        return sorted(rounds, key=lambda publishing_round: publishing_round.created_at, reverse=True)

    async def start_round(
        self,
        *,
        theme_keyword: str | None,
        slot_bindings: Mapping[str, Any] | None = None,
        evidence: str | None = None,
        pipeline_version_id: str | None = None,
    ) -> PublishingRound:
        """Post BE 활성 카테고리를 읽어 스냅샷을 만들고 라운드를 만든다 (설계 06 문서 3절 라우터 표).

        스냅샷에는 블로그에 화면이 있는 메인 아래 서브만 넣는다(D-60). 조회 실패(PublishFailedError)나 남는 서브가 없으면 라운드를 만들지 않는다.
        """
        if self._publisher is None:
            raise ValueError("no Post BE publisher is configured (POST_BE_URL)")
        categories = category_options(await self._publisher.list_categories(), self._main_categories)
        if not categories:
            raise ValueError("Post BE has no active sub category under an allowed active main category")
        return await self.create_round(
            theme_keyword=theme_keyword,
            slot_bindings=slot_bindings,
            categories=categories,
            evidence=evidence,
            pipeline_version_id=pipeline_version_id,
        )

    async def create_round(
        self,
        *,
        theme_keyword: str | None,
        categories: list[CategoryOption],
        slot_bindings: Mapping[str, Any] | None = None,
        evidence: str | None = None,
        pipeline_version_id: str | None = None,
    ) -> PublishingRound:
        """파이프라인 버전(기본은 최신)을 고정하고, 그 라우트로 끝까지 고를 수 있는지 검사한 라운드를 TOPIC_SELECTION 으로 만든다.

        slot_bindings 는 슬롯의 페르소나·작성 모델을 이 라운드만 바꾸는 A/B 값이다. 라우트가 빠졌거나 지원하지 않는 조합이면 ValueError.
        pipeline_version_id 는 최신이 아닌 버전으로 돌릴 때(차기 프롬프트 시험, Q-42) 준다. 버전이 하나도 없으면 ValueError(400).
        다른 라운드가 주제 선정 중이면 InvalidTransitionError(409) 이고 문서는 남지 않는다. 카테고리 스냅샷은 호출자가 읽어 넘긴다.
        """
        version = await self._pipeline_version(pipeline_version_id)
        routing = self._routing_snapshot(version, slot_bindings)
        bindings = {
            slot: _binding_of(select_writer(routing, persona_id), persona_id)
            for slot, persona_id in routing.slot_personas.items()
        }
        # round_seq 는 동률 판정(round_seq % 3)에만 쓰므로 기존 라운드 수 + 1 이면 된다. 동시 생성은 부분 unique 인덱스가 막는다.
        existing = 0
        for status in RoundStatus:
            existing += len(await self._rounds.list_by_status(status))
        publishing_round = PublishingRound(
            round_id=uuid4().hex,
            round_seq=existing + 1,
            theme_keyword=theme_keyword,
            slot_bindings=bindings,
            routing=routing,
            pipeline_version_id=version.version_id,
            evidence=evidence,
            categories=categories,
        )
        await self._rounds.add(publishing_round)
        return publishing_round

    async def _pipeline_version(self, pipeline_version_id: str | None) -> PipelineVersion:
        if self._versions is None:
            raise ValueError("no pipeline version service is configured")
        if pipeline_version_id is None:
            return await self._versions.require_latest()
        return await self._versions.get(pipeline_version_id)

    def _routing_snapshot(self, version: PipelineVersion, slot_bindings: Mapping[str, Any] | None) -> RoutingSnapshot:
        """버전의 routes 에서 키 없는 벤더 후보를 빼고 A/B 를 얹은 유효 스냅샷. 끝까지 고를 수 있고 템플릿이 다 있어야 한다."""
        overrides = {
            AgentSlot(slot): ModelBinding.model_validate(binding) for slot, binding in (slot_bindings or {}).items()
        }
        for slot, binding in overrides.items():
            if binding.provider not in self._providers:
                raise ValueError(f"slot {slot} binds provider {binding.provider} which has no API key")
        snapshot = build_snapshot(
            version.route_documents(),
            self._providers,
            writer_overrides={slot: (binding.persona_id, _call_model(binding)) for slot, binding in overrides.items()},
        )
        personas = [
            get_persona(persona_id, version.personas).persona_id for persona_id in snapshot.slot_personas.values()
        ]
        if len(set(personas)) != len(personas):
            raise ValueError("every slot needs a distinct persona")
        validate_snapshot(snapshot)
        validate_prompts(snapshot, version.prompts)
        return snapshot

    async def seed_routes(self, documents: list[Mapping[str, Any]]) -> None:
        """옛 agent_routes 에 라우트 설정을 넣는다. 라운드는 이제 버전 문서의 routes 를 쓰므로 `agents pipeline edit-routes` 가 그 자리다.

        bootstrap --routes mongo 의 입력으로만 남는다(3단계에서 삭제). 깨진 설정이 들어가지 않게 벤더 키와 무관하게 먼저 검사한다.
        """
        if self._routes is None:
            raise ValueError("no model route repository is configured")
        declared = {candidate["provider"] for document in documents for candidate in document.get("candidates", [])}
        validate_snapshot(build_snapshot(documents, declared))
        await self._routes.replace_all(documents)

    async def list_route_documents(self) -> list[dict[str, Any]]:
        if self._routes is None:
            raise ValueError("no model route repository is configured")
        return await self._routes.list_documents()

    def build_draft(self, publishing_round: PublishingRound, slot: AgentSlot, token_limit: int) -> PostDraft:
        """확정된 주제로 글 문서를 만든다. ID 는 결정론적이라 다시 만들어도 같은 문서를 가리킨다."""
        attempt = publishing_round.latest_attempt
        if attempt is None or slot not in attempt.locked:
            raise ValueError(f"round {publishing_round.round_id} has no locked topic for slot {slot}")
        return PostDraft(
            draft_id=publishing_round.draft_id_for(slot),
            round_id=publishing_round.round_id,
            author_slot=slot,
            token_limit=token_limit,
            topic=attempt.locked[slot].proposal,
            reviewer_assignments=build_lens_assignments(),
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

    async def abort_round(self, round_id: str, reason: RoundAbortReason = RoundAbortReason.MANUAL) -> PublishingRound:
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
        return publishing_round

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
        if self._derived is not None:
            await self._derived.write_missing()

    async def _clean_up(self, publishing_round: PublishingRound) -> None:
        reason = publishing_round.abort_reason or RoundAbortReason.MANUAL
        for draft in await self._drafts.list_by_round(publishing_round.round_id):
            if not draft.is_terminal:
                await self._reject_by_abort(draft, reason)
        for entry in await self._topics.list_by_round(publishing_round.round_id):
            # 조건부 갱신: 목록을 읽은 뒤 발행이 끼어들어 PUBLISHED 가 된 항목은 건드리지 않는다
            await self._topics.release_unless_published(entry.entry_id)

    async def _reject_by_abort(self, draft: PostDraft, reason: RoundAbortReason) -> None:
        draft.reject_by_abort(reason)
        try:
            await self._drafts.save(draft)
        except ConcurrentUpdateError:
            # 글 코루틴이 방금 저장했다. 다음 스캔의 정리가 다시 거둔다
            logger.info("draft %s changed while aborting; reconcile will retry", draft.draft_id)


def _call_model(binding: ModelBinding) -> CallModel:
    return CallModel(provider=binding.provider, model_id=binding.model_id, max_output_tokens=binding.max_output_tokens)


def _binding_of(writer: CallModel, persona_id: str) -> ModelBinding:
    return ModelBinding(
        provider=writer.provider,
        model_id=writer.model_id,
        persona_id=persona_id,
        max_output_tokens=writer.max_output_tokens,
    )


def category_options(categories: list[PostCategory], main_values: Collection[str]) -> list[CategoryOption]:
    """평평한 카테고리 목록을 (활성 메인, 활성 서브) 쌍으로 묶는다. 메인은 main_values 에 있는 값만 남기고 응답 순서를 지킨다."""
    mains = {
        category.id: category
        for category in categories
        if category.parent_id is None and category.is_active and category.value in main_values
    }
    return [
        CategoryOption(
            main_category_id=category.parent_id,
            main_label=mains[category.parent_id].label,
            sub_category_id=category.id,
            sub_label=category.label,
        )
        for category in categories
        if category.parent_id in mains and category.is_active
    ]
