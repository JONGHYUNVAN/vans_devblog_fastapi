"""조회 전용 서비스: 라우터·CLI 가 보여 주는 라운드·글·영수증·지표를 읽어 응답 스키마로 바꾼다 (설계 06 문서 3절).

엔티티 → 응답 변환 함수(build_round_public·build_draft_public·build_call_public·…)도 여기 둔다.
schemas 는 models 를 변환하지 않고(CONVENTIONS 1절), 라우터·CLI 는 리포지토리를 직접 쓰지 않는다.
상태를 바꾸는 요청은 RoundService·DraftDecisionService 가 맡는다.
"""

import difflib

from app.core.exceptions import InvalidRequestError, NotFoundError
from app.models.agent_call import AgentCall
from app.models.enums import ClaimStatus, ResolutionStatus, RevisionCause, RoundStatus
from app.models.post_draft import DraftVersion, PostDraft, ReviewItem
from app.models.publishing_round import PublishingRound
from app.repositories.agent_call import AgentCallRepository
from app.repositories.post_draft import PostDraftRepository
from app.schemas.agent_pipeline import (
    CallPublic,
    DraftHistory,
    DraftProgress,
    DraftPublic,
    EscalationSummary,
    ItemSummary,
    RoundDetail,
    RoundProgress,
    RoundPublic,
)
from app.services.metrics import PipelineMetrics, compute_metrics
from app.services.pipeline_versions import PipelineVersionService
from app.services.round import RoundService


class PipelineViewService:
    """읽기만 한다. 없는 id 는 NotFoundError(404)."""

    def __init__(
        self,
        *,
        round_service: RoundService,
        draft_repository: PostDraftRepository,
        agent_call_repository: AgentCallRepository,
        version_service: PipelineVersionService,
    ) -> None:
        self._rounds = round_service
        self._drafts = draft_repository
        self._calls = agent_call_repository
        self._versions = version_service

    async def list_rounds(self, status: RoundStatus | None = None) -> list[RoundPublic]:
        """라운드 요약 목록. status 가 있으면 그 상태만."""
        return [build_round_public(each) for each in await self._rounds.list_rounds(status)]

    async def get_round_detail(self, round_id: str) -> RoundDetail:
        """라운드와 글 3편 요약."""
        publishing_round = await self._round(round_id)
        return build_round_detail(publishing_round, await self._drafts.list_by_round(round_id))

    async def get_round_progress(self, round_id: str) -> RoundProgress:
        """`agents status`·`--wait` 용: 러너가 멈춘 이유(승인 대기·격리·실패)와 글당 토큰."""
        publishing_round = await self._round(round_id)
        # 승인 토글은 라운드 버전의 조절 값이다. 상태 화면은 버전이 없어도 보여야 하므로 기본값으로 대신한다
        requires_approval = (await self._versions.parameters_or_default(publishing_round)).publish.requires_approval
        drafts = []
        for draft in await self._drafts.list_by_round(round_id):
            usage = await self._calls.sum_usage(draft.draft_id)
            drafts.append(
                DraftProgress(
                    draft_id=draft.draft_id,
                    status=draft.status,
                    awaits_approval=draft.awaits_approval(requires_approval),
                    iteration=draft.iteration,
                    versions=len(draft.versions),
                    title=draft.current_version.title if draft.versions else None,
                    escalation_reason=draft.escalation_reason,
                    failure=draft.failure.code if draft.failure else None,
                    external_post_id=draft.external_post_id,
                    tokens=usage.total_tokens,
                )
            )
        return RoundProgress(
            round_id=round_id,
            status=publishing_round.status,
            abort_reason=publishing_round.abort_reason,
            topic_attempts=len(publishing_round.topic_attempts),
            drafts=drafts,
        )

    async def get_draft_history(self, draft_id: str) -> DraftHistory:
        """글 전체 이력(도메인 문서 그대로). 없으면 NotFoundError."""
        return build_draft_history(await self._draft(draft_id))

    async def get_escalation_summary(self, draft_id: str) -> EscalationSummary:
        """사람용 격리 요약(최종 본문·diff·미결 차단·기각 항목). ESCALATED 가 아니어도 읽을 수 있다."""
        return build_escalation_summary(await self._draft(draft_id))

    async def list_calls(self, *, draft_id: str | None = None, round_id: str | None = None) -> list[CallPublic]:
        """draft_id 또는 round_id 로 거른 영수증. 둘 다 없으면 InvalidRequestError(400)."""
        if draft_id:
            rows = await self._calls.list_by_draft(draft_id)
        elif round_id:
            rows = await self._calls.list_by_round(round_id)
        else:
            raise InvalidRequestError("draft_id or round_id is required")
        return [build_call_public(call) for call in rows]

    async def compute_metrics(self, round_id: str | None = None) -> PipelineMetrics:
        """07 문서 4절 지표. round_id 가 없으면 모든 라운드."""
        if round_id:
            await self._round(round_id)
            round_ids = [round_id]
        else:
            round_ids = [each.round_id for each in await self._rounds.list_rounds()]
        drafts = [draft for each in round_ids for draft in await self._drafts.list_by_round(each)]
        calls = [call for each in round_ids for call in await self._calls.list_by_round(each)]
        return compute_metrics(drafts, calls)

    async def _round(self, round_id: str) -> PublishingRound:
        publishing_round = await self._rounds.get(round_id)
        if publishing_round is None:
            raise NotFoundError(f"round {round_id} does not exist")
        return publishing_round

    async def _draft(self, draft_id: str) -> PostDraft:
        draft = await self._drafts.get(draft_id)
        if draft is None:
            raise NotFoundError(f"draft {draft_id} does not exist")
        return draft


# ---------- 엔티티 → 응답 ----------


def build_round_public(publishing_round: PublishingRound) -> RoundPublic:
    """라운드 → 요약 응답. 글은 id 목록만 넣는다(글 문서는 따로 읽는다)."""
    return RoundPublic(
        round_id=publishing_round.round_id,
        round_seq=publishing_round.round_seq,
        status=publishing_round.status,
        theme_keyword=publishing_round.theme_keyword,
        slot_bindings=publishing_round.slot_bindings,
        pipeline_version_id=publishing_round.pipeline_version_id,
        draft_ids=publishing_round.draft_ids,
        topic_attempts=len(publishing_round.topic_attempts),
        abort_reason=publishing_round.abort_reason,
        created_at=publishing_round.created_at,
        updated_at=publishing_round.updated_at,
    )


def build_round_detail(publishing_round: PublishingRound, drafts: list[PostDraft]) -> RoundDetail:
    """라운드 + 글 목록 → 상세 응답. 요약 필드를 그대로 잇고 카테고리·주제 시도·글 요약을 더한다."""
    return RoundDetail(
        **build_round_public(publishing_round).model_dump(),
        categories=publishing_round.categories,
        latest_attempt=publishing_round.latest_attempt,
        drafts=[build_draft_public(draft) for draft in drafts],
    )


def build_draft_public(draft: PostDraft) -> DraftPublic:
    """글 요약. 결정 응답과 CLI 도 같이 쓴다."""
    return DraftPublic(
        draft_id=draft.draft_id,
        round_id=draft.round_id,
        author_slot=draft.author_slot,
        status=draft.status,
        iteration=draft.iteration,
        version_count=len(draft.versions),
        title=draft.current_version.title if draft.versions else None,
        topic_title=draft.topic.title,
        revision_cause=draft.revision_cause,
        escalation_reason=draft.escalation_reason,
        failure=draft.failure,
        publish_attempts=draft.publish_attempts,
        approved_by=draft.approved_by,
        external_post_id=draft.external_post_id,
        token_limit=draft.token_limit,
        updated_at=draft.updated_at,
    )


def build_draft_history(draft: PostDraft) -> DraftHistory:
    """도메인 문서를 그대로 내보낸다 (_id 대신 draft_id)."""
    return DraftHistory.model_validate(draft)


def _unified_diff(before: DraftVersion, after: DraftVersion) -> str:
    return "".join(
        difflib.unified_diff(
            before.content_markdown.splitlines(keepends=True),
            after.content_markdown.splitlines(keepends=True),
            fromfile=f"v{before.version_number}",
            tofile=f"v{after.version_number}",
        )
    )


def build_escalation_summary(draft: PostDraft) -> EscalationSummary:
    """최종 본문과 직전 버전과의 diff, 미해소 차단 항목, 기각 항목, 발행 전 단계의 관찰. ESCALATED 가 아니어도 만든다.

    diff 는 작성자가 쓴 버전끼리 비교한다. 다듬기가 바꾼 것은 polish_diff 로 따로 보인다.
    """
    version = draft.publishable_version or (draft.current_version if draft.versions else None)
    authored = draft.authored_publishable_version or version
    previous = next(
        (each for each in reversed(draft.versions) if authored and each.version_number < authored.version_number),
        None,
    )
    diff = _unified_diff(previous, authored) if authored is not None and previous is not None else ""
    polish_diff = ""
    if version is not None and authored is not None and version.origin_cause == RevisionCause.POLISH:
        polish_diff = _unified_diff(authored, version)
    open_items = draft.open_blocking_items(draft.last_reviewed_version) if draft.last_reviewed_version else []
    resolutions = {
        resolution.item_id: resolution for each in draft.versions for resolution in each.resolutions
    }  # 마지막 버전의 해소가 남는다
    arbitrations = {arbitration.item_id: arbitration for arbitration in draft.arbitrations}

    def summarize(item: ReviewItem) -> ItemSummary:
        resolution = resolutions.get(item.item_id)
        arbitration = arbitrations.get(item.item_id)
        return ItemSummary(
            item_id=item.item_id,
            reviewer_slot=item.reviewer_slot,
            lens=item.lens,
            section_id=item.section_id,
            severity=item.severity,
            comment=item.comment,
            suggested_fix=item.suggested_fix,
            resolution=resolution.status if resolution else None,
            resolution_reason=resolution.reason if resolution else None,
            arbitration=arbitration.outcome if arbitration else None,
        )

    rejected = [
        item
        for review in draft.reviews
        for item in review.items
        if item.item_id in resolutions
        and resolutions[item.item_id].status in (ResolutionStatus.REJECTED_WITH_REASON, ResolutionStatus.OUT_OF_SCOPE)
    ]
    return EscalationSummary(
        draft_id=draft.draft_id,
        status=draft.status,
        escalation_reason=draft.escalation_reason,
        failure=draft.failure,
        publication_intent=draft.publication_intent,
        human_instruction=draft.human_instruction,
        final_version=version.version_number if version else None,
        title=version.title if version else None,
        summary=version.summary if version else None,
        content_markdown=version.content_markdown if version else None,
        diff=diff,
        open_blocking_items=[summarize(item) for item in open_items],
        rejected_items=[summarize(item) for item in rejected],
        final_read=draft.final_read,
        unverifiable_claims=[
            claim for review in draft.reviews for claim in review.claims if claim.status == ClaimStatus.UNVERIFIABLE
        ],
        polish=draft.polish,
        polish_diff=polish_diff,
        level_assessment=draft.level_assessment,
    )


def build_call_public(call: AgentCall) -> CallPublic:
    """영수증 요약. 메시지 본문은 뺀다."""
    return CallPublic(
        call_id=call.call_id,
        round_id=call.round_id,
        draft_id=call.draft_id,
        kind=call.kind,
        slot=call.slot,
        provider=call.model_binding.provider,
        model_id=call.model_binding.model_id,
        target_version=call.target_version,
        topic_attempt=call.topic_attempt,
        item_id=call.item_id,
        attempt_number=call.attempt_number,
        output_parsed_ok=call.output_parsed_ok,
        error=call.error,
        prompt_tokens=call.usage.prompt_tokens,
        completion_tokens=call.usage.completion_tokens,
        latency_ms=call.latency_ms,
        started_at=call.started_at,
        finished_at=call.finished_at,
    )
