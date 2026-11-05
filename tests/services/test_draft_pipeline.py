"""DraftPipelineService 시나리오 ①~⑧·⑪ (설계 06 문서 6절). 가짜 LLM 과 FakeAsyncDatabase 위의 실제 리포지토리로 돈다."""

import asyncio

import pytest

from app.core.exceptions import LLMTransportError
from app.models.enums import (
    AgentSlot,
    ArbitrationOutcome,
    EscalationDecisionKind,
    EscalationReason,
    FailureCode,
    PostStatus,
    ReviewLens,
    ReviewScope,
    RevisionCause,
)
from tests.factories import (
    VALID_BODY,
    arbitrate_output,
    draft_output,
    resolution,
    review_output,
    revision_output,
    spot_check_output,
)
from tests.fakes import PipelineHarness, call_input_of

pytestmark = pytest.mark.anyio

A, B, C = AgentSlot.A, AgentSlot.B, AgentSlot.C


def _ids(draft, version_number: int) -> list[str]:
    return [item.item_id for item in draft.open_blocking_items(version_number)]


async def test_fast_track_reaches_ready_to_publish_and_waits_for_approval():  # ①
    harness = PipelineHarness({"WRITE_DRAFT": [draft_output()], "REVIEW_FULL": review_output()})
    await harness.seed_round()

    draft = await harness.drive(A)

    assert draft.status == PostStatus.READY_TO_PUBLISH
    assert harness.llm.kinds == ["WRITE_DRAFT", "REVIEW_FULL", "REVIEW_FULL"]
    assert (draft.iteration, draft.last_reviewed_version, draft.approved_by) == (0, 1, None)
    assert {review.lens for review in draft.reviews} == set(ReviewLens)
    assert draft.current_version.call_id == (await harness.calls.list_by_draft(draft.draft_id))[0].call_id
    assert harness.publisher.payloads == []


async def test_one_revision_then_approval_publishes():  # ②
    harness = PipelineHarness(
        {
            "WRITE_DRAFT": [draft_output()],
            "REVIEW_FULL": [review_output("CRITICAL"), review_output()],  # B 가 먼저 불린다 (배정 순서)
            "REVISE_DRAFT": lambda call_input: revision_output(
                [resolution(item["item_id"]) for review in call_input["reviews"] for item in review["items"]]
            ),
            "SPOT_CHECK": spot_check_output(resolved=True),
        }
    )
    await harness.seed_round()

    draft = await harness.drive(A)

    assert draft.status == PostStatus.READY_TO_PUBLISH
    assert (draft.iteration, len(draft.versions), draft.last_reviewed_version) == (1, 2, 2)
    assert harness.llm.kinds == ["WRITE_DRAFT", "REVIEW_FULL", "REVIEW_FULL", "REVISE_DRAFT", "SPOT_CHECK"]
    spots = {review.lens: review for review in draft.reviews if review.scope == ReviewScope.SPOT_CHECK}
    # 비판 렌즈는 미결 항목이 없어 SPOT_CHECK 를 받지 않는다. 사실 확인 렌즈는 항상 받지만 바뀐 섹션이 없으면 호출하지 않는다
    assert set(spots) == {ReviewLens.LOGIC_READABILITY, ReviewLens.FACT_STRUCTURE}
    assert spots[ReviewLens.FACT_STRUCTURE].call_id == ""
    assert draft.versions[1].origin_cause == RevisionCause.REVIEW

    draft.record_approval("사람", requires_approval=True)
    await harness.drafts.save(draft)
    draft = await harness.drive(A)

    assert draft.status == PostStatus.PUBLISHED
    assert [payload.title for payload in harness.publisher.payloads] == [draft.current_version.title]
    assert draft.publication is not None and draft.publication.approved_by == "사람"


async def test_two_deadlocks_arbitrate_each_item_and_uphold_author_publishes():  # ③
    harness = PipelineHarness(
        {
            "WRITE_DRAFT": [draft_output()],
            "REVIEW_FULL": [review_output("CRITICAL"), review_output("CRITICAL")],
            "REVISE_DRAFT": lambda call_input: revision_output(
                [
                    resolution(item["item_id"], "REJECTED_WITH_REASON")
                    for review in call_input["reviews"]
                    for item in review["items"]
                ]
            ),
            "SPOT_CHECK": spot_check_output(resolved=False),
            "ARBITRATE": [arbitrate_output("UPHOLD_AUTHOR"), arbitrate_output("UPHOLD_AUTHOR")],
        }
    )
    await harness.seed_round()

    draft = await harness.drive(A)

    assert draft.status == PostStatus.READY_TO_PUBLISH
    assert harness.llm.kinds.count("ARBITRATE") == 2
    arbitrations = {arbitration.item_id: arbitration for arbitration in draft.arbitrations}
    blocking = [item for review in draft.reviews_of(1) for item in review.items if item.severity.is_blocking]
    assert set(arbitrations) == {item.item_id for item in blocking}
    receipts = [call for call in await harness.calls.list_by_draft(draft.draft_id) if call.kind == "ARBITRATE"]
    assert {call.item_id for call in receipts} == set(arbitrations)
    # 작성 벤더(fake-a)도, 그 지적을 낸 리뷰 벤더(fake-b)도 아닌 벤더가 판정한다
    assert {call.model_binding.provider for call in receipts} == {"fake-c"}
    assert all(arbitration.outcome == ArbitrationOutcome.UPHOLD_AUTHOR for arbitration in draft.arbitrations)
    assert draft.open_blocking_items(2) == []


async def test_uphold_reviewer_item_stays_open_and_rejecting_it_again_fails_the_gate():  # ④
    revisions = iter(["reject", "reject", "accept"])

    def revise(call_input):
        mode = next(revisions)
        status = "ACCEPTED" if mode == "accept" else "REJECTED_WITH_REASON"
        ids = [item["item_id"] for review in call_input["reviews"] for item in review["items"]]
        return revision_output([resolution(item_id, status) for item_id in ids], title=f"v{mode}")

    harness = PipelineHarness(
        {
            "WRITE_DRAFT": [draft_output()],
            "REVIEW_FULL": [review_output("CRITICAL"), review_output()],
            "REVISE_DRAFT": revise,
            "SPOT_CHECK": [spot_check_output(resolved=False), spot_check_output(resolved=True)],
            "ARBITRATE": [arbitrate_output("UPHOLD_REVIEWER")],
        }
    )
    await harness.seed_round()

    draft = await harness.drive(A)

    assert draft.status == PostStatus.READY_TO_PUBLISH
    [item] = draft.open_blocking_items(1)
    assert _ids(draft, 2) == [item.item_id]  # UPHOLD_REVIEWER 라 v2 뒤에도 남는다
    v3 = draft.versions[2]
    assert v3.passed_validation is False
    assert v3.validation_errors == [f"중재에서 리뷰어가 맞다고 판정된 항목은 수용해야 한다: {item.item_id}"]
    assert (draft.iteration, draft.validation_retries, len(draft.versions)) == (2, 0, 4)
    revise_inputs = [
        call_input_of(call.messages) for call in harness.llm.calls if "수정본을 낸다" in call.messages[0].content
    ]
    assert revise_inputs[2]["validation_errors"] == v3.validation_errors
    assert [arbitration["must_accept"] for arbitration in revise_inputs[2]["arbitration_results"]] == [True]


async def test_gate_failure_keeps_iteration_and_rewritten_v1_gets_full_review():  # ⑤
    harness = PipelineHarness(
        {
            "WRITE_DRAFT": [draft_output(content=VALID_BODY.replace("<!-- sec:tradeoff -->", ""))],
            "REVISE_DRAFT": [revision_output()],
            "REVIEW_FULL": review_output(),
        }
    )
    await harness.seed_round()

    draft = await harness.drive(A)

    assert draft.status == PostStatus.READY_TO_PUBLISH
    assert harness.llm.kinds == ["WRITE_DRAFT", "REVISE_DRAFT", "REVIEW_FULL", "REVIEW_FULL"]
    assert (draft.iteration, draft.validation_retries) == (0, 0)
    assert draft.versions[0].validation_errors == ["필수 앵커가 없다: tradeoff"]
    assert (draft.versions[1].origin_cause, draft.versions[1].passed_validation) == (None, True)
    revise_input = call_input_of(harness.llm.calls[1].messages)
    assert (revise_input["validation_errors"], revise_input["reviews"]) == (["필수 앵커가 없다: tradeoff"], [])


async def test_human_revise_skips_review_and_clears_previous_approval():  # ⑥
    harness = PipelineHarness(
        {"WRITE_DRAFT": [draft_output()], "REVIEW_FULL": review_output(), "REVISE_DRAFT": [revision_output()]}
    )
    await harness.seed_round()
    draft = await harness.drive(A)
    draft.record_approval("사람", requires_approval=True)
    draft.escalate(EscalationReason.PUBLISH_FAILED)
    draft.resolve_escalation(EscalationDecisionKind.REVISE, decided_by="사람", instruction="결론을 줄여라")
    await harness.drafts.save(draft)

    draft = await harness.drive(A)

    assert draft.status == PostStatus.READY_TO_PUBLISH
    assert harness.llm.kinds[-1] == "REVISE_DRAFT" and harness.llm.kinds.count("REVIEW_FULL") == 2
    assert call_input_of(harness.llm.calls[-1].messages)["human_instruction"] == "결론을 줄여라"
    assert (draft.versions[-1].origin_cause, draft.iteration) == (RevisionCause.HUMAN, 0)
    assert draft.approved_by is None and draft.awaits_approval(True)


async def test_save_conflict_discards_the_result_and_rereads():  # ⑦
    harness = PipelineHarness()
    await harness.seed_round()

    def abort_while_writing(call_input):
        # 호출 중에 라운드 중단이 글을 REJECTED 로 썼다 (revision 이 올라간다)
        doc = harness.database.agent_drafts.docs[0]
        doc["status"] = PostStatus.REJECTED.value
        doc["abort_reason"] = "MANUAL"
        doc["revision"] += 1
        return draft_output()

    harness.llm.scripts["WRITE_DRAFT"] = abort_while_writing

    draft = await harness.drive(A)

    assert (draft.status, draft.versions) == (PostStatus.REJECTED, [])
    assert harness.llm.kinds == ["WRITE_DRAFT"]
    assert len(await harness.calls.list_by_draft(draft.draft_id)) == 1  # 버린 호출의 비용은 남는다


async def test_interrupted_step_resumes_from_last_saved_state_with_the_same_call():  # ⑧
    harness = PipelineHarness(
        {
            "WRITE_DRAFT": [draft_output()],
            "REVIEW_FULL": [review_output("CRITICAL"), review_output()],
            "REVISE_DRAFT": lambda call_input: revision_output(
                [resolution(item["item_id"]) for review in call_input["reviews"] for item in review["items"]]
            ),
            "SPOT_CHECK": spot_check_output(resolved=True),
        },
        delay_s=0.02,
    )
    await harness.seed_round()
    draft_id = (await harness.draft(A)).draft_id

    task = asyncio.create_task(harness.draft_pipeline.drive(draft_id))
    await asyncio.sleep(0.01)  # WRITE_DRAFT 호출 중
    task.cancel()
    await asyncio.gather(task, return_exceptions=True)
    draft = await harness.draft(A)
    assert (draft.status, draft.versions) == (PostStatus.DRAFTING, [])

    task = asyncio.create_task(harness.draft_pipeline.drive(draft_id))
    while (await harness.draft(A)).status != PostStatus.DRAFTING or not (await harness.draft(A)).versions:
        await asyncio.sleep(0.005)  # v1 리뷰 뒤 REVISING → DRAFTING 으로 저장된 뒤 REVISE_DRAFT 호출 중
    task.cancel()
    await asyncio.gather(task, return_exceptions=True)
    draft = await harness.draft(A)
    assert (draft.status, draft.revision_cause, draft.iteration) == (PostStatus.DRAFTING, RevisionCause.REVIEW, 1)

    draft = await harness.drive(A)

    assert draft.status == PostStatus.READY_TO_PUBLISH
    # 끊긴 호출(응답 전 취소)은 영수증도 응답도 없다. 다시 돌리면 그 호출만 다시 한다
    assert harness.llm.kinds == ["WRITE_DRAFT", "REVIEW_FULL", "REVIEW_FULL", "REVISE_DRAFT", "SPOT_CHECK"]
    finished_stages = 6  # 사실 확인 2(검색·변환), 다듬기 2, 전체 읽기, 수준 평가
    assert len(harness.llm.calls) == 7 + finished_stages
    assert len(await harness.calls.list_by_draft(draft_id)) == 5 + finished_stages
    writes = [call for call in harness.llm.calls if "글 한 편을 한국어로 쓴다" in call.messages[0].content]
    revises = [call for call in harness.llm.calls if "수정본을 낸다" in call.messages[0].content]
    assert len(writes) == 2 and writes[0].messages[1].content == writes[1].messages[1].content
    assert len(revises) == 2 and revises[0].messages[1].content == revises[1].messages[1].content


async def test_one_review_exhausting_retries_keeps_the_other_and_resume_calls_only_the_missing_one():  # ⑪
    harness = PipelineHarness(
        {
            "WRITE_DRAFT": [draft_output()],
            "REVIEW_FULL": [review_output(), LLMTransportError("503"), LLMTransportError("503"), review_output()],
        }
    )
    await harness.seed_round()

    draft = await harness.drive(A)

    assert draft.status == PostStatus.FAILED
    assert draft.failure is not None
    assert (draft.failure.code, draft.failure.from_status) == (FailureCode.LLM_RETRIES_EXHAUSTED, PostStatus.IN_REVIEW)
    assert [review.lens for review in draft.reviews] == [ReviewLens.LOGIC_READABILITY, ReviewLens.FACT_STRUCTURE]

    draft.retry_from_failed()
    await harness.drafts.save(draft)
    draft = await harness.drive(A)

    assert draft.status == PostStatus.READY_TO_PUBLISH
    assert harness.llm.kinds.count("REVIEW_FULL") == 4
    assert harness.llm.all_kinds.count("FACT_CHECK") == 1  # 끝낸 사실 확인은 되풀이하지 않는다
    assert [review.lens for review in draft.reviews] == list(ReviewLens)


async def test_unexpected_exception_in_one_review_keeps_the_other_and_fails_with_internal_error():
    """병렬 단계의 예상하지 못한 예외(예: 바인딩이 키 없는 벤더를 가리킴)도 성공한 형제를 저장한다 (04 문서 6절·9절)."""
    harness = PipelineHarness(
        {
            "WRITE_DRAFT": [draft_output()],
            "REVIEW_FULL": [review_output(), KeyError("no-such-provider"), review_output()],
        }
    )
    await harness.seed_round()

    draft = await harness.drive(A)

    assert draft.status == PostStatus.FAILED
    assert draft.failure is not None
    assert (draft.failure.code, draft.failure.from_status) == (FailureCode.INTERNAL_ERROR, PostStatus.IN_REVIEW)
    assert "KeyError: 'no-such-provider'" in draft.failure.message
    assert [review.lens for review in draft.reviews] == [ReviewLens.LOGIC_READABILITY, ReviewLens.FACT_STRUCTURE]

    draft.retry_from_failed()
    await harness.drafts.save(draft)
    draft = await harness.drive(A)

    assert draft.status == PostStatus.READY_TO_PUBLISH
    assert harness.llm.kinds.count("REVIEW_FULL") == 3
    assert [review.lens for review in draft.reviews] == list(ReviewLens)


async def test_budget_exceeded_marks_failed_without_calling():
    harness = PipelineHarness({"WRITE_DRAFT": [draft_output()]})
    await harness.seed_round()
    draft = await harness.draft(A)
    draft.token_limit = 1
    await harness.drafts.save(draft)

    draft = await harness.drive(A)

    assert draft.status == PostStatus.FAILED
    assert draft.failure is not None
    assert (draft.failure.code, draft.failure.from_status) == (FailureCode.BUDGET_EXCEEDED, PostStatus.DRAFTING)
    assert harness.llm.kinds == []


async def test_drive_stops_when_round_is_not_in_progress():
    harness = PipelineHarness({"WRITE_DRAFT": [draft_output()]})
    publishing_round = await harness.seed_round()
    await harness.round_service.abort_round(publishing_round.round_id)

    draft = await harness.drive(A)

    assert draft.status == PostStatus.REJECTED
    assert harness.llm.kinds == []
