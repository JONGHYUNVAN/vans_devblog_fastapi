"""리뷰·중재 결과로 다음 상태를 정하는 평가 엔진. I/O 없는 순수 함수다 (설계 04 문서 4~5절).

판정은 severity 집계로 결정론적으로 도출한다. LLM 의 reported_verdict 는 기록만 한다(설계 07 문서 D-03).
"""

from collections.abc import Sequence

from app.models.enums import EscalationReason, IssueSeverity, PostStatus, ReviewScope, ReviewVerdict
from app.models.post_draft import EvaluationOutcome, ItemCheck, PostDraft, ReviewItem


def derive_verdict(scope: ReviewScope, items: Sequence[ReviewItem], item_checks: Sequence[ItemCheck]) -> ReviewVerdict:
    """리뷰 한 건의 derived_verdict.

    FULL 은 차단 등급 항목이 있으면, SPOT_CHECK 는 미해소 항목이나 신규 CRITICAL 이 있으면 REQUEST_CHANGES 다.
    """
    if scope == ReviewScope.FULL:
        has_blocking = any(item.severity.is_blocking for item in items)
        return ReviewVerdict.REQUEST_CHANGES if has_blocking else ReviewVerdict.APPROVE
    has_open = any(not check.resolved for check in item_checks) or any(
        item.severity == IssueSeverity.CRITICAL for item in items
    )
    return ReviewVerdict.REQUEST_CHANGES if has_open else ReviewVerdict.APPROVE


def evaluate(draft: PostDraft, max_iterations: int) -> EvaluationOutcome:
    """리뷰나 중재가 모두 도착한 글의 다음 상태.

    FULL 과 SPOT_CHECK 는 버전 번호가 아니라 글 상태(IN_REVIEW·IN_SPOT_CHECK)로 가른다. 응답이 덜 모였거나
    평가할 상태가 아니면 ValueError.
    """
    match draft.status:
        case PostStatus.IN_REVIEW | PostStatus.IN_SPOT_CHECK:
            if not draft.is_review_complete():
                raise ValueError(f"draft {draft.draft_id} is still waiting for reviews")
        case PostStatus.IN_ARBITRATION:
            if not draft.arbitration_complete():
                raise ValueError(f"draft {draft.draft_id} is still waiting for arbitrations")
        case _:
            raise ValueError(f"draft {draft.draft_id} is {draft.status}, nothing to evaluate")

    unresolved = draft.open_blocking_items(draft.current_version.version_number)
    if not unresolved:
        # FULL 에서 MINOR·NITPICK 만 남으면 수정 없이 발행 전 단계로 간다. 요약 노트에 기록으로만 남는다.
        return EvaluationOutcome(next_status=PostStatus.POST_REVIEW)
    if draft.status == PostStatus.IN_REVIEW:
        # FULL 은 iteration 0 에서만 일어난다. 상한 검사는 REVISING 을 나갈 때 한다(설계 03 문서 14행).
        return EvaluationOutcome(next_status=PostStatus.REVISING)
    if draft.status == PostStatus.IN_SPOT_CHECK and draft.pending_arbitration_items():
        # 교착 중재는 iteration 과 무관하게 수정·격리보다 먼저다(설계 03 문서 16행).
        return EvaluationOutcome(next_status=PostStatus.IN_ARBITRATION)
    if draft.iteration < max_iterations:
        return EvaluationOutcome(next_status=PostStatus.REVISING)
    return EvaluationOutcome(
        next_status=PostStatus.ESCALATED, escalation_reason=EscalationReason.MAX_ITERATIONS_REACHED
    )
