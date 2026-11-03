"""PipelineViewService 테스트: 라운드 진행 상태(승인 대기·토큰), 격리 요약(diff·항목), 영수증 필터, 없는 id. 응답 형태는 라우터 golden 이 본다."""

import pytest

from app.core.exceptions import InvalidRequestError, NotFoundError
from app.models.enums import AgentSlot, ArbitrationOutcome, IssueSeverity, PostStatus
from app.services.evaluation import evaluate
from app.services.pipeline_views import PipelineViewService, build_escalation_summary
from tests.factories import (
    add_reviews,
    draft_output,
    make_arbitration,
    make_call,
    make_draft,
    reject,
    review_output,
    write_next_version,
)
from tests.fakes import PipelineHarness

pytestmark = pytest.mark.anyio

A, B, C = AgentSlot.A, AgentSlot.B, AgentSlot.C


def _views(harness: PipelineHarness) -> PipelineViewService:
    return PipelineViewService(
        round_service=harness.round_service,
        draft_repository=harness.drafts,
        agent_call_repository=harness.calls,
        settings=harness.settings,
    )


async def test_round_progress_shows_approval_wait_and_tokens_per_draft():
    harness = PipelineHarness({"WRITE_DRAFT": [draft_output()], "REVIEW_FULL": review_output()})
    await harness.seed_round()
    await harness.drive(A)
    views = _views(harness)

    progress = await views.get_round_progress("round-1")

    by_id = {draft.draft_id: draft for draft in progress.drafts}
    assert (by_id["round-1:A"].status, by_id["round-1:A"].awaits_approval) == (PostStatus.READY_TO_PUBLISH, True)
    assert by_id["round-1:A"].tokens > 0 and by_id["round-1:B"].tokens == 0
    assert (progress.status, progress.topic_attempts) == ("IN_PROGRESS", 1)
    detail = await views.get_round_detail("round-1")
    assert [draft.draft_id for draft in detail.drafts] == sorted(by_id)
    with pytest.raises(NotFoundError):
        await views.get_round_progress("nope")


def test_escalation_summary_has_diff_open_items_and_rejected_items_with_arbitration():
    draft = make_draft(A)
    write_next_version(draft)
    add_reviews(draft, {B: [IssueSeverity.CRITICAL]})
    draft.apply_evaluation(evaluate(draft, 2))
    [item] = draft.open_blocking_items(1)
    write_next_version(draft, reject(item))
    draft.versions[1].content_markdown += "\n고친 문단"
    add_reviews(draft, resolved={item.item_id: False})
    draft.apply_evaluation(evaluate(draft, 2))
    draft.add_arbitration(make_arbitration(draft, item, ArbitrationOutcome.UPHOLD_REVIEWER))
    draft.apply_evaluation(evaluate(draft, 2))

    summary = build_escalation_summary(draft)

    assert summary.final_version == 2 and "+고친 문단" in summary.diff
    assert [each.item_id for each in summary.open_blocking_items] == [item.item_id]
    [rejected] = summary.rejected_items
    assert (rejected.resolution, rejected.arbitration) == ("REJECTED_WITH_REASON", ArbitrationOutcome.UPHOLD_REVIEWER)


async def test_list_calls_filters_and_needs_a_filter():
    harness = PipelineHarness()
    await harness.seed_round()
    await harness.calls.add(make_call(call_id="c1", draft_id="round-1:A"))
    await harness.calls.add(make_call(call_id="c2", draft_id="round-1:B"))
    views = _views(harness)

    assert [call.call_id for call in await views.list_calls(draft_id="round-1:A")] == ["c1"]
    assert [call.call_id for call in await views.list_calls(round_id="round-1")] == ["c1", "c2"]
    with pytest.raises(InvalidRequestError):
        await views.list_calls()
    with pytest.raises(NotFoundError):
        await views.compute_metrics("nope")
    assert (await views.compute_metrics("round-1")).drafts == 3
