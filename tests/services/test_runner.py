"""PipelineRunner 테스트: 중복 코루틴 없음, 띄우지 않는 글, 내부 오류(시나리오 ⑫), 스캔마다 정리."""

import asyncio

import pytest

from app.models.enums import AgentSlot, EscalationReason, FailureCode, PostStatus, RoundAbortReason, RoundStatus
from tests.factories import draft_output, make_round, review_output
from tests.fakes import PipelineHarness

pytestmark = pytest.mark.anyio

A, B, C = AgentSlot.A, AgentSlot.B, AgentSlot.C


async def _settle(harness: PipelineHarness) -> None:
    while harness.runner.running:
        await asyncio.sleep(0.005)


async def test_scan_spawns_one_coroutine_per_target_and_drives_drafts_to_the_end():
    harness = PipelineHarness({"WRITE_DRAFT": [draft_output()] * 3, "REVIEW_FULL": review_output()}, delay_s=0.01)
    await harness.seed_round()

    await harness.runner.scan()
    first = set(harness.runner.running)
    await harness.runner.scan()

    assert first == {"round-1:A", "round-1:B", "round-1:C"} == set(harness.runner.running)
    await _settle(harness)
    assert {(await harness.draft(slot)).status for slot in AgentSlot} == {PostStatus.READY_TO_PUBLISH}
    assert harness.llm.kinds.count("WRITE_DRAFT") == 3


async def test_scan_skips_awaiting_approval_escalated_failed_and_not_in_progress_rounds():
    harness = PipelineHarness()
    await harness.seed_round()
    waiting = await harness.draft(A)
    waiting.status = PostStatus.READY_TO_PUBLISH
    await harness.drafts.save(waiting)
    escalated = await harness.draft(B)
    escalated.escalate(EscalationReason.MAX_ITERATIONS_REACHED)
    await harness.drafts.save(escalated)
    failed = await harness.draft(C)
    failed.mark_failed(FailureCode.BUDGET_EXCEEDED, "over")
    await harness.drafts.save(failed)
    await harness.rounds.add(make_round("round-2", round_seq=2, status=RoundStatus.ABORTED))

    await harness.runner.scan()

    assert harness.runner.running == []


async def test_scan_spawns_topic_selection_for_topic_selection_round():
    harness = PipelineHarness({"PROPOSE_TOPIC": [RuntimeError("stop")] * 3}, delay_s=0.01)
    await harness.rounds.add(make_round())

    await harness.runner.scan()

    assert harness.runner.running == ["round-1"]
    await _settle(harness)


async def test_unexpected_exception_marks_draft_failed_and_next_scan_does_not_respawn():  # ⑫
    harness = PipelineHarness({"WRITE_DRAFT": [RuntimeError("bug")]})
    await harness.seed_round()

    await harness.runner.scan()
    await _settle(harness)
    draft = await harness.draft(A)

    assert draft.status == PostStatus.FAILED
    assert draft.failure is not None
    assert (draft.failure.code, draft.failure.from_status) == (FailureCode.INTERNAL_ERROR, PostStatus.DRAFTING)
    assert "RuntimeError: bug" in draft.failure.message

    harness.llm.scripts["WRITE_DRAFT"] = [draft_output()]
    await harness.runner.scan()
    await _settle(harness)

    assert "round-1:A" not in harness.runner.running
    assert (await harness.draft(A)).status == PostStatus.FAILED


async def test_unexpected_exception_in_topic_selection_aborts_round():
    harness = PipelineHarness({"PROPOSE_TOPIC": [RuntimeError("bug")]})
    await harness.rounds.add(make_round())

    await harness.runner.scan()
    await _settle(harness)

    publishing_round = await harness.rounds.get("round-1")
    assert (publishing_round.status, publishing_round.abort_reason) == (
        RoundStatus.ABORTED,
        RoundAbortReason.INTERNAL_ERROR,
    )


async def test_scan_reconciles_before_spawning():
    harness = PipelineHarness()
    publishing_round = await harness.seed_round()
    publishing_round.abort(RoundAbortReason.MANUAL)
    await harness.rounds.save(publishing_round)  # 글·레지스트리 정리 전에 죽은 중단

    await harness.runner.scan()

    assert harness.runner.running == []
    assert {(await harness.draft(slot)).status for slot in AgentSlot} == {PostStatus.REJECTED}
    assert {entry.status for entry in await harness.topics.list_by_round("round-1")} == {"RELEASED"}


async def test_stop_cancels_running_coroutines():
    harness = PipelineHarness({"WRITE_DRAFT": [draft_output()]}, delay_s=1.0)
    await harness.seed_round()
    harness.runner.start()
    await asyncio.sleep(0.01)
    assert "round-1:A" in harness.runner.running

    await harness.runner.stop()

    assert harness.runner.running == []
    assert (await harness.draft(A)).status == PostStatus.DRAFTING  # 끊긴 호출은 다음 기동 때 다시 한다
