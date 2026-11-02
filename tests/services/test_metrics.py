"""compute_metrics 테스트 (설계 07 문서 4절 지표). 순수 함수라 DB 없이 factories 로 글을 만든다."""

from app.models.enums import (
    AgentSlot,
    ArbitrationOutcome,
    CallKind,
    EscalationReason,
    FailureCode,
    IssueSeverity,
    PostStatus,
    ReviewVerdict,
)
from app.services.evaluation import evaluate
from app.services.metrics import compute_metrics
from tests.factories import (
    MAX_ITERATIONS,
    add_reviews,
    make_arbitration,
    make_call,
    make_draft,
    reject,
    submit,
    write_next_version,
)

A, B, C = AgentSlot.A, AgentSlot.B, AgentSlot.C


def _fast_track():
    draft = make_draft(A, draft_id="round-1:A")
    write_next_version(draft)
    add_reviews(draft)
    draft.apply_evaluation(evaluate(draft, MAX_ITERATIONS))
    draft.status = PostStatus.PUBLISHED
    return draft


def _arbitrated_then_escalated():
    draft = make_draft(B, draft_id="round-1:B")
    draft.start_drafting(MAX_ITERATIONS)
    submit(draft)
    draft.fail_validation(["필수 앵커가 없다: tradeoff"], 2)  # v1 게이트 실패
    write_next_version(draft)  # v2 통과
    add_reviews(draft, {A: [IssueSeverity.CRITICAL]})
    draft.reviews[-1].reported_verdict = ReviewVerdict.APPROVE  # 유도 판정(REQUEST_CHANGES)과 다르다
    draft.apply_evaluation(evaluate(draft, MAX_ITERATIONS))
    [item] = draft.open_blocking_items(2)
    write_next_version(draft, reject(item))
    add_reviews(draft, resolved={item.item_id: False})
    draft.apply_evaluation(evaluate(draft, MAX_ITERATIONS))
    draft.add_arbitration(make_arbitration(draft, item, ArbitrationOutcome.UPHOLD_AUTHOR))
    draft.apply_evaluation(evaluate(draft, MAX_ITERATIONS))
    draft.escalate(EscalationReason.PUBLISH_FAILED)
    return draft


def test_metrics_count_ratios_reasons_mismatches_gate_failures_and_tokens():
    failed = make_draft(C, draft_id="round-1:C")
    failed.mark_failed(FailureCode.BUDGET_EXCEEDED, "over")
    calls = [
        make_call(call_id="c1", draft_id="round-1:A", prompt_tokens=1000, completion_tokens=200),
        make_call(call_id="c2", draft_id="round-1:A", kind=CallKind.REVIEW, prompt_tokens=500, completion_tokens=50),
        make_call(call_id="c3", draft_id="round-1:B", prompt_tokens=3000, completion_tokens=400),
        make_call(call_id="c4", draft_id=None, kind=CallKind.PROPOSE_TOPIC, prompt_tokens=100, completion_tokens=10),
    ]

    metrics = compute_metrics([_fast_track(), _arbitrated_then_escalated(), failed], calls)

    assert (metrics.drafts, metrics.status_counts) == (3, {"PUBLISHED": 1, "ESCALATED": 1, "FAILED": 1})
    assert (metrics.reached_ready, metrics.fast_track_ratio) == (2, 0.5)
    assert metrics.avg_iteration == 0.5
    assert (metrics.escalated_ratio, metrics.failed_ratio) == (0.333, 0.333)
    assert (metrics.escalation_reasons, metrics.failure_codes) == ({"PUBLISH_FAILED": 1}, {"BUDGET_EXCEEDED": 1})
    assert (metrics.arbitration_ratio, metrics.uphold_author_ratio) == (0.5, 1.0)
    by_slot = {row.slot: (row.full_reviews, row.mismatched, row.ratio) for row in metrics.verdict_mismatch}
    assert by_slot == {A: (1, 1, 1.0), B: (1, 0, 0.0), C: (2, 0, 0.0)}
    assert (metrics.gate_failure_ratio, metrics.gate_failures) == (0.25, {"필수 앵커가 없다": 1})
    assert metrics.rejected_with_reason_ratio == 1.0
    assert metrics.tokens_by_kind == {"PROPOSE_TOPIC": 110, "WRITE_DRAFT": 4600, "REVIEW": 550}
    assert (metrics.avg_tokens_per_draft, metrics.max_tokens_per_draft) == (2575.0, 3400)
    assert [(row.draft_id, row.total_tokens, row.calls) for row in metrics.draft_tokens] == [
        ("round-1:A", 1750, 2),
        ("round-1:B", 3400, 1),
        ("round-1:C", 0, 0),
    ]


def test_metrics_on_nothing_have_no_ratios():
    metrics = compute_metrics([], [])

    assert (metrics.drafts, metrics.fast_track_ratio, metrics.avg_iteration, metrics.avg_tokens_per_draft) == (
        0,
        None,
        None,
        None,
    )
    assert all(row.ratio is None for row in metrics.verdict_mismatch)
