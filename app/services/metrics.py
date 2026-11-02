"""운영 지표 (설계 07 문서 4절). 글·영수증 목록에서 계산하는 순수 함수다. 프롬프트 튜닝과 Q-07(토큰 상한) 결정의 입력이다."""

from collections import Counter, defaultdict

from pydantic import BaseModel, Field

from app.models.agent_call import AgentCall
from app.models.enums import (
    AgentSlot,
    ArbitrationOutcome,
    CallKind,
    EscalationReason,
    PostStatus,
    ResolutionStatus,
    ReviewScope,
)
from app.models.post_draft import PostDraft

_PUBLISH_STAGE = frozenset({EscalationReason.PUBLISH_FAILED, EscalationReason.PUBLISH_UNCERTAIN})


class SlotVerdictMismatch(BaseModel):
    """FULL 리뷰에서 리뷰어가 보고한 판정과 항목으로 유도한 판정이 다른 비율 (07 문서 D-03)."""

    slot: AgentSlot
    full_reviews: int
    mismatched: int
    ratio: float | None


class DraftTokens(BaseModel):
    draft_id: str
    status: PostStatus
    iteration: int
    prompt_tokens: int
    completion_tokens: int
    total_tokens: int
    calls: int


class PipelineMetrics(BaseModel):
    drafts: int
    status_counts: dict[str, int]
    reached_ready: int = Field(description="READY_TO_PUBLISH 에 닿은 글(발행됨·발행 단계 격리 포함)")
    fast_track_ratio: float | None = Field(
        description="닿은 글 중 v1 이 그대로 발행본인 비율. 게이트 실패 뒤 v2 로 간 글은 아니다"
    )
    avg_iteration: float | None
    escalated_ratio: float | None
    failed_ratio: float | None
    escalation_reasons: dict[str, int]
    failure_codes: dict[str, int]
    arbitration_ratio: float | None = Field(description="리뷰를 받은 글 중 중재가 있었던 비율")
    uphold_author_ratio: float | None = Field(description="중재 중 UPHOLD_AUTHOR 비율")
    verdict_mismatch: list[SlotVerdictMismatch]
    gate_failure_ratio: float | None = Field(
        description="게이트를 거친 버전(통과했거나 validation_errors 가 있음) 중 떨어진 비율. 아직 VALIDATING 인 버전은 세지 않는다"
    )
    gate_failures: dict[str, int] = Field(description="게이트 실패 사유 종류별 건수 (메시지의 ':' 앞부분)")
    rejected_with_reason_ratio: float | None = Field(description="해소 중 REJECTED_WITH_REASON 비율")
    tokens_by_kind: dict[str, int]
    avg_tokens_per_draft: float | None
    max_tokens_per_draft: int | None
    draft_tokens: list[DraftTokens]


def compute_metrics(drafts: list[PostDraft], calls: list[AgentCall]) -> PipelineMetrics:
    reached = [draft for draft in drafts if _reached_ready(draft)]
    escalated = [draft for draft in drafts if draft.status == PostStatus.ESCALATED]
    failed = [draft for draft in drafts if draft.status == PostStatus.FAILED]
    reviewed = [draft for draft in drafts if draft.reviews]
    arbitrations = [arbitration for draft in drafts for arbitration in draft.arbitrations]
    versions = [version for draft in drafts for version in draft.versions]
    gated = [version for version in versions if version.passed_validation or version.validation_errors]
    resolutions = [resolution for version in versions for resolution in version.resolutions]

    mismatch: list[SlotVerdictMismatch] = []
    for slot in AgentSlot:
        full = [
            review
            for draft in drafts
            for review in draft.reviews
            if review.reviewer_slot == slot and review.scope == ReviewScope.FULL
        ]
        mismatched = sum(1 for review in full if review.reported_verdict != review.derived_verdict)
        mismatch.append(
            SlotVerdictMismatch(
                slot=slot, full_reviews=len(full), mismatched=mismatched, ratio=_ratio(mismatched, full)
            )
        )

    gate_failures = Counter(error.split(":")[0].strip() for version in versions for error in version.validation_errors)

    usage: dict[str, DraftTokens] = {}
    for draft in drafts:
        usage[draft.draft_id] = DraftTokens(
            draft_id=draft.draft_id,
            status=draft.status,
            iteration=draft.iteration,
            prompt_tokens=0,
            completion_tokens=0,
            total_tokens=0,
            calls=0,
        )
    by_kind: dict[str, int] = defaultdict(int)
    for call in calls:
        by_kind[call.kind.value] += call.usage.total_tokens
        if call.draft_id in usage:
            row = usage[call.draft_id]
            row.prompt_tokens += call.usage.prompt_tokens
            row.completion_tokens += call.usage.completion_tokens
            row.total_tokens += call.usage.total_tokens
            row.calls += 1
    totals = [row.total_tokens for row in usage.values() if row.calls]

    return PipelineMetrics(
        drafts=len(drafts),
        status_counts=dict(Counter(draft.status.value for draft in drafts)),
        reached_ready=len(reached),
        fast_track_ratio=_ratio(sum(1 for draft in reached if _is_fast_track(draft)), reached),
        avg_iteration=_mean([draft.iteration for draft in drafts if draft.versions]),
        escalated_ratio=_ratio(len(escalated), drafts),
        failed_ratio=_ratio(len(failed), drafts),
        escalation_reasons=dict(Counter(str(draft.escalation_reason) for draft in escalated)),
        failure_codes=dict(Counter(draft.failure.code.value for draft in failed if draft.failure)),
        arbitration_ratio=_ratio(sum(1 for draft in reviewed if draft.arbitrations), reviewed),
        uphold_author_ratio=_ratio(
            sum(1 for arbitration in arbitrations if arbitration.outcome == ArbitrationOutcome.UPHOLD_AUTHOR),
            arbitrations,
        ),
        verdict_mismatch=mismatch,
        gate_failure_ratio=_ratio(sum(1 for version in gated if not version.passed_validation), gated),
        gate_failures=dict(gate_failures),
        rejected_with_reason_ratio=_ratio(
            sum(1 for resolution in resolutions if resolution.status == ResolutionStatus.REJECTED_WITH_REASON),
            resolutions,
        ),
        tokens_by_kind={kind.value: by_kind[kind.value] for kind in CallKind if by_kind[kind.value]},
        avg_tokens_per_draft=_mean(totals),
        max_tokens_per_draft=max(totals) if totals else None,
        draft_tokens=list(usage.values()),
    )


def _reached_ready(draft: PostDraft) -> bool:
    if draft.status in (PostStatus.READY_TO_PUBLISH, PostStatus.PUBLISHED):
        return True
    return draft.status == PostStatus.ESCALATED and draft.escalation_reason in _PUBLISH_STAGE


def _is_fast_track(draft: PostDraft) -> bool:
    version = draft.publishable_version
    return version is not None and version.version_number == 1


def _ratio(count: int, population: list | int) -> float | None:
    size = population if isinstance(population, int) else len(population)
    return round(count / size, 3) if size else None


def _mean(values: list[int]) -> float | None:
    return round(sum(values) / len(values), 2) if values else None
