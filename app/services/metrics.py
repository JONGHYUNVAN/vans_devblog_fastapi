"""운영 지표 (설계 07 문서 4절). 글·영수증 목록에서 계산하는 순수 함수다. 프롬프트 튜닝과 Q-07(토큰 상한) 결정의 입력이다."""

import re
from collections import Counter, defaultdict

from pydantic import BaseModel, Field

from app.models.agent_call import AgentCall
from app.models.enums import (
    ArbitrationOutcome,
    CallKind,
    EscalationReason,
    PostStatus,
    ResolutionStatus,
    ReviewLens,
    ReviewScope,
)
from app.models.post_draft import PostDraft
from app.models.tiptap import parse_markdown, top_level_paragraphs

_PUBLISH_STAGE = frozenset({EscalationReason.PUBLISH_FAILED, EscalationReason.PUBLISH_UNCERTAIN})
_SENTENCE_END = re.compile(r"[.!?…](?=\s|$)")
_DENSE_INLINE_CODE = 4  # 문단 하나에 이만큼 식별자가 몰리면 설명 없이 선언만 한 문단일 가능성이 크다
_BASIC_LEVELS = (100, 200)  # 초기 측정은 100·200 과 300 이상의 구분만 본다


class LensVerdictMismatch(BaseModel):
    """FULL 리뷰에서 리뷰어가 보고한 판정과 항목으로 유도한 판정이 다른 비율 (07 문서 D-03)."""

    lens: ReviewLens
    full_reviews: int
    mismatched: int
    ratio: float | None


class VersionDensity(BaseModel):
    """글 하나의 밀도. 문장 수는 문장 부호로 어림잡는다. 처음에는 막지 않고 재기만 한다."""

    chars: int
    code_chars: int
    paragraphs: int
    sentences_per_paragraph: float | None
    inline_code_per_paragraph: float | None
    dense_paragraphs: int = Field(description="인라인 코드가 4개 이상인 문단 수")


class DraftVersionDensity(VersionDensity):
    draft_id: str
    version_number: int
    growth_ratio: float | None = Field(description="직전 버전 대비 길이 증가율")


class LevelAgreement(BaseModel):
    """평가자 수준과 사용자 라벨의 일치도."""

    pairs: int
    exact_ratio: float | None
    basic_vs_advanced_ratio: float | None = Field(description="100·200 과 300 이상을 같은 쪽으로 가른 비율")


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
    verdict_mismatch: list[LensVerdictMismatch]
    gate_failure_ratio: float | None = Field(
        description="게이트를 거친 버전(통과했거나 validation_errors 가 있음) 중 떨어진 비율. 아직 VALIDATING 인 버전은 세지 않는다"
    )
    gate_failures: dict[str, int] = Field(description="게이트 실패 사유 종류별 건수 (메시지의 ':' 앞부분)")
    rejected_with_reason_ratio: float | None = Field(
        description="해소 중 유지한(REJECTED_WITH_REASON·OUT_OF_SCOPE) 비율"
    )
    out_of_scope_ratio: float | None = Field(
        description="해소 중 OUT_OF_SCOPE 비율. 0 이면 작성자 방어권이 작동하지 않는다"
    )
    density: list[DraftVersionDensity]
    claim_statuses: dict[str, int] = Field(
        description="사실 확인 주장의 판정별 건수. 오탐률을 재 UNVERIFIABLE 승격을 정한다"
    )
    polish_applied: int
    polish_discard_reasons: dict[str, int]
    level_agreement: LevelAgreement
    truncated_calls: int
    tokens_by_kind: dict[str, int]
    tokens_by_model: dict[str, int]
    tokens_by_lens: dict[str, int]
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

    mismatch: list[LensVerdictMismatch] = []
    for lens in ReviewLens:
        full = [
            review
            for draft in drafts
            for review in draft.reviews
            if review.lens == lens and review.scope == ReviewScope.FULL
        ]
        mismatched = sum(1 for review in full if review.reported_verdict != review.derived_verdict)
        mismatch.append(
            LensVerdictMismatch(
                lens=lens, full_reviews=len(full), mismatched=mismatched, ratio=_ratio(mismatched, full)
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
    by_model: dict[str, int] = defaultdict(int)
    by_lens: dict[str, int] = defaultdict(int)
    for call in calls:
        by_kind[call.kind.value] += call.usage.total_tokens
        by_model[f"{call.model_binding.provider}/{call.model_binding.model_id}"] += call.usage.total_tokens
        if call.lens is not None:
            by_lens[call.lens.value] += call.usage.total_tokens
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
            sum(
                1
                for resolution in resolutions
                if resolution.status in (ResolutionStatus.REJECTED_WITH_REASON, ResolutionStatus.OUT_OF_SCOPE)
            ),
            resolutions,
        ),
        out_of_scope_ratio=_ratio(
            sum(1 for resolution in resolutions if resolution.status == ResolutionStatus.OUT_OF_SCOPE), resolutions
        ),
        density=[row for draft in drafts for row in _density_rows(draft)],
        claim_statuses={
            status.value: count
            for status, count in Counter(
                claim.status for draft in drafts for review in draft.reviews for claim in review.claims
            ).items()
        },
        polish_applied=sum(draft.polish.applied for draft in drafts if draft.polish),
        polish_discard_reasons=dict(
            Counter(discarded.reason for draft in drafts if draft.polish for discarded in draft.polish.discarded)
        ),
        level_agreement=_level_agreement(drafts),
        truncated_calls=sum(1 for call in calls if (call.error or "").startswith("truncated:")),
        tokens_by_kind={kind.value: by_kind[kind.value] for kind in CallKind if by_kind[kind.value]},
        tokens_by_model=dict(by_model),
        tokens_by_lens=dict(by_lens),
        avg_tokens_per_draft=_mean(totals),
        max_tokens_per_draft=max(totals) if totals else None,
        draft_tokens=list(usage.values()),
    )


def _reached_ready(draft: PostDraft) -> bool:
    if draft.status in (PostStatus.READY_TO_PUBLISH, PostStatus.PUBLISHED):
        return True
    return draft.status == PostStatus.ESCALATED and draft.escalation_reason in _PUBLISH_STAGE


def _is_fast_track(draft: PostDraft) -> bool:
    """v1 이 수정 없이 발행본이 됐는지. 다듬은 버전은 수정이 아니다."""
    version = draft.authored_publishable_version
    return version is not None and version.version_number == 1


def density_of(markdown: str) -> VersionDensity:
    """앵커·코드를 뺀 최상위 문단의 밀도. 프롬프트 효과를 비교하는 기준이다."""
    sentences: list[int] = []
    inline_codes: list[int] = []
    for _, _, inline in top_level_paragraphs(markdown):
        children = inline.children or []
        prose = "".join(child.content for child in children if child.type == "text")
        sentences.append(max(1, len(_SENTENCE_END.findall(prose))))
        inline_codes.append(sum(1 for child in children if child.type == "code_inline"))
    return VersionDensity(
        chars=len(markdown),
        code_chars=sum(len(token.content) for token in parse_markdown(markdown) if token.type == "fence"),
        paragraphs=len(sentences),
        sentences_per_paragraph=_mean(sentences),
        inline_code_per_paragraph=_mean(inline_codes),
        dense_paragraphs=sum(1 for count in inline_codes if count >= _DENSE_INLINE_CODE),
    )


def _density_rows(draft: PostDraft) -> list[DraftVersionDensity]:
    rows = []
    previous_length: int | None = None
    for version in draft.versions:
        length = len(version.content_markdown)
        rows.append(
            DraftVersionDensity(
                **density_of(version.content_markdown).model_dump(),
                draft_id=draft.draft_id,
                version_number=version.version_number,
                growth_ratio=round(length / previous_length - 1, 3) if previous_length else None,
            )
        )
        previous_length = length
    return rows


def _level_agreement(drafts: list[PostDraft]) -> LevelAgreement:
    pairs = [
        (draft.level_assessment.level, draft.user_level)
        for draft in drafts
        if draft.level_assessment is not None and draft.user_level is not None
    ]
    return LevelAgreement(
        pairs=len(pairs),
        exact_ratio=_ratio(sum(1 for assessed, labeled in pairs if assessed == labeled), pairs),
        basic_vs_advanced_ratio=_ratio(
            sum(1 for assessed, labeled in pairs if (assessed in _BASIC_LEVELS) == (labeled in _BASIC_LEVELS)), pairs
        ),
    )


def _ratio(count: int, population: list | int) -> float | None:
    size = population if isinstance(population, int) else len(population)
    return round(count / size, 3) if size else None


def _mean(values: list[int]) -> float | None:
    return round(sum(values) / len(values), 2) if values else None
