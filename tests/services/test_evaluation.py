"""평가 엔진 테스트. 순수 함수라 도메인 모델만으로 돈다."""

import pytest

from app.models.enums import (
    AgentSlot,
    ArbitrationOutcome,
    EscalationReason,
    IssueSeverity,
    PostStatus,
    ReviewScope,
    ReviewVerdict,
)
from app.models.post_draft import ItemCheck
from app.services.evaluation import derive_verdict, evaluate
from tests.factories import (
    MAX_ITERATIONS,
    accept,
    add_reviews,
    make_arbitration,
    make_draft,
    make_review,
    reject,
    write_next_version,
)

B, C = AgentSlot.B, AgentSlot.C
CRITICAL, MAJOR, MINOR = IssueSeverity.CRITICAL, IssueSeverity.MAJOR, IssueSeverity.MINOR


def _in_review():
    draft = make_draft()
    write_next_version(draft)
    return draft


def _in_spot_check(*, rejected: bool):
    draft = _in_review()
    add_reviews(draft, {B: [CRITICAL]})
    draft.apply_evaluation(evaluate(draft, MAX_ITERATIONS))
    items = draft.open_blocking_items(1)
    write_next_version(draft, reject(*items) if rejected else accept(*items))
    return draft, items


# ── derive_verdict ─────────────────────────────────────────────────


def test_derive_verdict_full_approves_non_blocking_items():
    draft = _in_review()
    review = make_review(draft, B, [MINOR, IssueSeverity.NITPICK])

    assert derive_verdict(ReviewScope.FULL, review.items, []) == ReviewVerdict.APPROVE


def test_derive_verdict_full_requests_changes_for_major():
    draft = _in_review()
    review = make_review(draft, B, [MAJOR])

    assert derive_verdict(ReviewScope.FULL, review.items, []) == ReviewVerdict.REQUEST_CHANGES


def test_derive_verdict_spot_check_approves_when_everything_resolved():
    checks = [ItemCheck(item_id="x", resolved=True)]

    assert derive_verdict(ReviewScope.SPOT_CHECK, [], checks) == ReviewVerdict.APPROVE


def test_derive_verdict_spot_check_requests_changes_for_unresolved_item():
    checks = [ItemCheck(item_id="x", resolved=False, note="그대로다")]

    assert derive_verdict(ReviewScope.SPOT_CHECK, [], checks) == ReviewVerdict.REQUEST_CHANGES


# ── evaluate ───────────────────────────────────────────────────────


def test_evaluate_fast_tracks_full_review_without_blocking_items():
    draft = _in_review()
    add_reviews(draft, {C: [MINOR]})

    assert evaluate(draft, MAX_ITERATIONS).next_status == PostStatus.READY_TO_PUBLISH


def test_evaluate_full_review_with_one_blocking_item_revises():
    draft = _in_review()
    add_reviews(draft, {C: [MAJOR]})

    assert evaluate(draft, MAX_ITERATIONS).next_status == PostStatus.REVISING


def test_evaluate_ignores_reported_verdict():
    # 07 문서 D-03: 리뷰어가 APPROVE 라고 해도 차단 항목이 있으면 수정이다
    draft = _in_review()
    add_reviews(draft, {B: [CRITICAL]})

    assert draft.reviews[0].reported_verdict == ReviewVerdict.APPROVE
    assert evaluate(draft, MAX_ITERATIONS).next_status == PostStatus.REVISING


def test_evaluate_spot_check_resolved_is_ready():
    draft, _ = _in_spot_check(rejected=False)
    add_reviews(draft)

    assert evaluate(draft, MAX_ITERATIONS).next_status == PostStatus.READY_TO_PUBLISH


def test_evaluate_spot_check_unresolved_revises_then_escalates_at_max():
    draft, items = _in_spot_check(rejected=False)
    add_reviews(draft, resolved={items[0].item_id: False})

    assert evaluate(draft, MAX_ITERATIONS).next_status == PostStatus.REVISING
    outcome = evaluate(draft, max_iterations=1)
    assert outcome.next_status == PostStatus.ESCALATED
    assert outcome.escalation_reason == EscalationReason.MAX_ITERATIONS_REACHED


def test_evaluate_spot_check_deadlock_goes_to_arbitration():
    draft, items = _in_spot_check(rejected=True)
    add_reviews(draft, resolved={items[0].item_id: False})

    assert evaluate(draft, max_iterations=1).next_status == PostStatus.IN_ARBITRATION


@pytest.mark.parametrize(
    ("outcome", "expected"),
    [
        (ArbitrationOutcome.UPHOLD_AUTHOR, PostStatus.READY_TO_PUBLISH),
        (ArbitrationOutcome.UPHOLD_REVIEWER, PostStatus.REVISING),
    ],
)
def test_evaluate_arbitration_result(outcome, expected):
    draft, items = _in_spot_check(rejected=True)
    add_reviews(draft, resolved={items[0].item_id: False})
    draft.apply_evaluation(evaluate(draft, MAX_ITERATIONS))
    draft.add_arbitration(make_arbitration(draft, items[0], outcome))

    assert evaluate(draft, MAX_ITERATIONS).next_status == expected


def test_evaluate_raises_when_reviews_are_missing():
    draft = _in_review()
    draft.add_review(make_review(draft, B))

    with pytest.raises(ValueError, match="waiting for reviews"):
        evaluate(draft, MAX_ITERATIONS)


def test_evaluate_raises_when_nothing_to_evaluate():
    with pytest.raises(ValueError, match="nothing to evaluate"):
        evaluate(make_draft(), MAX_ITERATIONS)
