"""발행 전 전체 읽기·수준 평가와 사실 확인 렌즈의 시나리오 테스트. 가짜 LLM 과 FakeAsyncDatabase 위의 실제 서비스로 돈다."""

import json

import pytest

from app.clients.llm.base import LLMCitation, LLMResponse, LLMUsage
from app.core.exceptions import LLMTransportError
from app.models.enums import (
    AgentSlot,
    ClaimStatus,
    EscalationDecisionKind,
    EscalationReason,
    FailureCode,
    IssueSeverity,
    PostReviewStage,
    PostStatus,
    ReviewLens,
    ReviewScope,
    RevisionCause,
)
from app.services.pipeline_views import build_escalation_summary
from tests.factories import (
    LENSES,
    VALID_BODY,
    arbitrate_output,
    assess_level_output,
    build_review_assignments,
    draft_output,
    fact_claim,
    fact_transform_output,
    final_read_finding,
    final_read_output,
    make_binding,
    make_round,
    resolution,
    review_output,
    revision_output,
    spot_check_output,
)
from tests.fakes import PipelineHarness, call_input_of

pytestmark = pytest.mark.anyio

A = AgentSlot.A
FACT = ReviewLens.FACT_STRUCTURE


def _fast_track(**overrides) -> dict:
    return {"WRITE_DRAFT": [draft_output()], "REVIEW_FULL": review_output()} | overrides


async def _run(**overrides):
    harness = PipelineHarness(_fast_track(**overrides))
    await harness.seed_round()
    return harness, await harness.drive(A)


# ---------- 호출별 모델 ----------


async def test_each_call_goes_to_the_route_selected_model():
    harness, draft = await _run()
    calls = await harness.calls.list_by_draft(draft.draft_id)

    providers = {
        (call.kind.value, call.lens.value if call.lens else None): call.model_binding.provider for call in calls
    }
    assert providers[("WRITE_DRAFT", None)] == "fake-a"
    assert providers[("REVIEW", "LOGIC_READABILITY")] == "fake-b"
    assert providers[("FACT_CHECK", "FACT_STRUCTURE")] == "fake-c"
    assert providers[("REVIEW", "CRITICAL_PRACTICAL")] == "fake-b"
    assert providers[("FINAL_READ", None)] == "fake-b"
    assert providers[("ASSESS_LEVEL", None)] == "fake-b"


async def test_old_round_without_routing_runs_on_its_slot_bindings():
    harness = PipelineHarness(_fast_track())
    await harness.seed_round()
    publishing_round = await harness.rounds.get("round-1")
    assert publishing_round is not None
    publishing_round.routing = None
    publishing_round.slot_bindings = {
        AgentSlot.A: make_binding(provider="fake-a", model_id="old-a", persona_id="architect"),
        AgentSlot.B: make_binding(provider="fake-b", model_id="old-b", persona_id="systems_engineer"),
        AgentSlot.C: make_binding(provider="fake-c", model_id="old-c", persona_id="troubleshooter"),
    }
    await harness.rounds.save(publishing_round)

    draft = await harness.drive(A)

    assert draft.status == PostStatus.READY_TO_PUBLISH
    assert draft.versions[0].model_id == "old-a"


async def test_old_draft_with_slot_reviewers_finishes_after_deploy():
    # 배포 전에 시작한 글: 슬롯 리뷰어 둘, 보조 렌즈, 라우팅 없는 라운드. FULL 을 받은 뒤 수정 → SPOT_CHECK → 발행 전 단계까지 간다
    harness = PipelineHarness(
        {
            "REVIEW_FULL": [review_output("CRITICAL"), review_output()],
            "REVISE_DRAFT": lambda call_input: revision_output(
                [resolution(item["item_id"]) for review in call_input["reviews"] for item in review["items"]]
            ),
            "SPOT_CHECK": spot_check_output(resolved=True),
        }
    )
    await harness.seed_round()
    publishing_round = await harness.rounds.get("round-1")
    assert publishing_round is not None
    publishing_round.routing = None
    publishing_round.slot_bindings = {
        AgentSlot.A: make_binding(provider="fake-a", model_id="old-a", persona_id="architect"),
        AgentSlot.B: make_binding(provider="fake-b", model_id="old-b", persona_id="systems_engineer"),
        AgentSlot.C: make_binding(provider="fake-c", model_id="old-c", persona_id="troubleshooter"),
    }
    await harness.rounds.save(publishing_round)
    draft = await harness.draft(A)
    draft.reviewer_assignments = build_review_assignments(A, LENSES)
    draft.status = PostStatus.DRAFTING
    await harness.drafts.save(draft)
    harness.llm.scripts["WRITE_DRAFT"] = [draft_output()]

    draft = await harness.drive(A)

    assert draft.status == PostStatus.READY_TO_PUBLISH
    assert "FACT_CHECK" not in harness.llm.all_kinds  # 옛 글은 슬롯 리뷰어라 검색하지 않는다
    assert draft.reviews[0].review_id.endswith(":B:FULL")
    assert {review.reviewer_slot for review in draft.reviews} == {AgentSlot.B, AgentSlot.C}
    assert draft.versions[0].model_id == "old-a"


async def test_round_without_routing_field_keeps_loading():
    stored = make_round().model_dump(by_alias=True)
    del stored["routing"], stored["evidence"]

    assert type(make_round()).model_validate(stored).routing is None


# ---------- 발행 전 단계 ----------


async def test_review_pass_goes_through_post_review_before_waiting_for_approval():
    harness, draft = await _run()

    assert draft.status == PostStatus.READY_TO_PUBLISH
    assert draft.post_review_stage == PostReviewStage.DONE
    assert harness.llm.all_kinds[-3:] == ["POLISH_TONE", "FINAL_READ", "ASSESS_LEVEL"]
    assert draft.level_assessment is not None and draft.level_assessment.level == 200
    assert draft.level_assessment.criteria_version == "v1"
    assert harness.publisher.payloads == []


async def test_blocking_final_read_escalates_without_assessment_and_shows_in_summary():
    finding = final_read_finding("CORE_MECHANISM_MISSING")
    harness, draft = await _run(FINAL_READ=[final_read_output(finding, final_read_finding("OBSERVATION"))])

    assert draft.status == PostStatus.ESCALATED
    assert draft.escalation_reason == EscalationReason.FINAL_READ_FLAGGED
    assert draft.level_assessment is None
    assert "ASSESS_LEVEL" not in harness.llm.all_kinds
    summary = build_escalation_summary(draft)
    assert summary.final_read is not None and len(summary.final_read.findings) == 2


async def test_final_read_flag_can_be_revised_by_a_human_without_another_final_read():
    harness, draft = await _run(
        FINAL_READ=[final_read_output(final_read_finding("BODY_CODE_CONTRADICTION"))],
        REVISE_DRAFT=[revision_output()],
    )
    await harness.decisions.resolve_escalation(
        draft.draft_id, EscalationDecisionKind.REVISE, decided_by="사람", instruction="코드를 맞춰라"
    )

    draft = await harness.drive(A)

    assert draft.status == PostStatus.READY_TO_PUBLISH
    assert draft.current_version.origin_cause == RevisionCause.HUMAN
    assert harness.llm.all_kinds.count("FINAL_READ") == 1


async def test_final_read_quote_that_is_not_in_the_body_is_a_contract_violation():
    harness, draft = await _run(
        FINAL_READ=[
            final_read_output(final_read_finding("OBSERVATION", quote="본문에 없는 말")),
            final_read_output(),
        ]
    )

    assert draft.status == PostStatus.READY_TO_PUBLISH
    assert harness.llm.all_kinds.count("FINAL_READ") == 2


async def test_final_read_retries_exhausted_fails_and_resume_does_not_repeat_finished_calls():
    harness, draft = await _run(FINAL_READ=[LLMTransportError("503"), LLMTransportError("503"), final_read_output()])

    assert draft.status == PostStatus.FAILED
    assert draft.failure is not None
    assert (draft.failure.code, draft.failure.from_status) == (
        FailureCode.LLM_RETRIES_EXHAUSTED,
        PostStatus.POST_REVIEW,
    )
    assert draft.post_review_stage == PostReviewStage.FINAL_READ

    draft.retry_from_failed()
    await harness.drafts.save(draft)
    draft = await harness.drive(A)

    assert draft.status == PostStatus.READY_TO_PUBLISH
    assert harness.llm.all_kinds.count("POLISH_TONE") == 1  # 다듬기를 되풀이하지 않는다


async def test_assessor_does_not_receive_the_target_level():
    harness, draft = await _run()
    [listed] = [each for each in await harness.calls.list_by_draft(draft.draft_id) if each.kind == "ASSESS_LEVEL"]
    call = await harness.calls.get(listed.call_id)  # 목록은 본문 없는 영수증이라 메시지는 get 으로 읽는다

    assert call is not None
    assert "target_level" not in call.messages[1].content
    assert "level_criteria" in call.messages[1].content


async def test_assessment_quote_must_be_in_the_body():
    harness, draft = await _run(ASSESS_LEVEL=[assess_level_output(quote="없는 인용"), assess_level_output()])

    assert draft.status == PostStatus.READY_TO_PUBLISH
    assert harness.llm.all_kinds.count("ASSESS_LEVEL") == 2


# ---------- 사실 확인 ----------


async def test_supported_claim_with_a_cited_url_makes_no_item():
    _, draft = await _run()

    [fact] = [review for review in draft.reviews if review.lens == FACT]
    assert fact.items == []
    assert [claim.status for claim in fact.claims] == [ClaimStatus.SUPPORTED]
    assert fact.model_id == "fake-c-1"


async def test_refuted_claim_is_critical_and_goes_to_revision():
    harness, draft = await _run(
        FACT_TRANSFORM=[fact_transform_output(fact_claim("REFUTED")), fact_transform_output()],
        REVISE_DRAFT=lambda call_input: revision_output(
            [resolution(item["item_id"]) for review in call_input["reviews"] for item in review["items"]],
            content=VALID_BODY.replace("본론", "고친 본론"),
        ),
    )

    assert draft.status == PostStatus.READY_TO_PUBLISH
    [fact_v1] = [r for r in draft.reviews_of(1) if r.lens == FACT]
    [item] = fact_v1.items
    assert (item.severity, item.from_fact_check, item.lens) == (IssueSeverity.CRITICAL, True, FACT)
    assert "REFUTED" in item.comment
    # 다음 버전의 SPOT_CHECK 는 바뀐 섹션을 다시 확인한다
    fact_check_inputs = [
        call_input_of(call.messages) for call in harness.llm.calls if "사실 확인 담당" in call.messages[0].content
    ]
    assert fact_check_inputs[0]["changed_section_ids"] == []
    assert fact_check_inputs[1]["changed_section_ids"] == ["body-1"]
    assert [each["item_id"] for each in fact_check_inputs[1]["my_previous_items"]] == [item.item_id]


async def test_unverifiable_claim_is_minor_and_listed_in_the_summary():
    _, draft = await _run(FACT_TRANSFORM=fact_transform_output(fact_claim("UNVERIFIABLE", source_url=None)))

    assert draft.status == PostStatus.READY_TO_PUBLISH  # 오탐률을 재기 전까지 막지 않는다
    [fact] = [review for review in draft.reviews if review.lens == FACT]
    assert [item.severity for item in fact.items] == [IssueSeverity.MINOR]
    assert [claim.status for claim in build_escalation_summary(draft).unverifiable_claims] == [ClaimStatus.UNVERIFIABLE]


@pytest.mark.parametrize("status", ["SUPPORTED", "REFUTED"])
async def test_url_not_in_the_search_citations_downgrades_to_unverifiable(status):
    # 변환 모델이 URL 을 지어내면 지운다. 근거 없는 판정으로 글을 깎지 않는다
    _, draft = await _run(FACT_TRANSFORM=fact_transform_output(fact_claim(status, source_url="https://made-up.test/x")))

    [fact] = [review for review in draft.reviews if review.lens == FACT]
    [claim] = fact.claims
    assert (claim.status, claim.source_url) == (ClaimStatus.UNVERIFIABLE, None)
    assert [item.severity for item in fact.items] == [IssueSeverity.MINOR]


async def test_redirect_citation_matches_by_host_in_title():
    search = LLMResponse(
        text="확인 결과",
        usage=LLMUsage(prompt_tokens=1, completion_tokens=1),
        citations=[LLMCitation(url="https://grounding.test/redirect/abc", title="www.mongodb.com")],
    )
    _, draft = await _run(
        FACT_CHECK=search,
        FACT_TRANSFORM=fact_transform_output(fact_claim("SUPPORTED", source_url="https://www.mongodb.com/docs/x")),
    )

    [fact] = [review for review in draft.reviews if review.lens == FACT]
    assert fact.claims[0].status == ClaimStatus.SUPPORTED


async def test_claims_without_any_citation_mean_the_search_did_not_run_and_fail_the_call():
    # 전부 UNVERIFIABLE 로 내리면 틀린 사실이 MINOR 로 통과한다
    unsearched = LLMResponse(text="기억으로 답했다", usage=LLMUsage(prompt_tokens=1, completion_tokens=1))
    _, draft = await _run(FACT_CHECK=unsearched, FACT_TRANSFORM=fact_transform_output(fact_claim("REFUTED")))

    assert draft.status == PostStatus.FAILED
    assert draft.failure is not None
    assert draft.failure.code == FailureCode.LLM_RETRIES_EXHAUSTED
    assert "cited nothing" in draft.failure.message


async def test_no_claims_and_no_citations_is_fine():
    unsearched = LLMResponse(text="확인할 주장이 없다", usage=LLMUsage(prompt_tokens=1, completion_tokens=1))
    _, draft = await _run(FACT_CHECK=unsearched, FACT_TRANSFORM=fact_transform_output())

    assert draft.status == PostStatus.READY_TO_PUBLISH


async def test_a_claim_already_checked_does_not_come_back_as_a_new_critical():
    # 작성자가 기각하고 중재에서 이긴 주장을 다음 SPOT_CHECK 가 다시 내도 새 지적이 되지 않는다
    def transform(call_input):
        return fact_transform_output(fact_claim("REFUTED"))(call_input)

    harness, draft = await _run(
        FACT_TRANSFORM=transform,
        REVISE_DRAFT=lambda call_input: revision_output(
            [resolution(item["item_id"]) for review in call_input["reviews"] for item in review["items"]],
            content=VALID_BODY.replace("본론", "고친 본론"),
        ),
    )

    assert draft.status == PostStatus.READY_TO_PUBLISH
    fact_inputs = [
        call_input_of(call.messages) for call in harness.llm.calls if "사실 확인 담당" in call.messages[0].content
    ]
    assert fact_inputs[0]["known_claims"] == []
    assert fact_inputs[1]["known_claims"] == [fact_claim("REFUTED")["claim"]]
    [spot] = [review for review in draft.reviews if review.lens == FACT and review.scope == ReviewScope.SPOT_CHECK]
    assert spot.items == [] and spot.claims == []


async def test_search_failure_is_a_call_failure_not_a_finding():
    harness, draft = await _run(FACT_CHECK=[LLMTransportError("search down"), LLMTransportError("search down")])

    assert draft.status == PostStatus.FAILED
    assert draft.failure is not None
    assert draft.failure.code == FailureCode.LLM_RETRIES_EXHAUSTED
    assert all(review.lens != FACT for review in draft.reviews)
    assert "FACT_TRANSFORM" not in harness.llm.all_kinds


async def test_search_call_has_web_search_and_transform_call_has_schema():
    harness, _ = await _run()

    search = next(call for call in harness.llm.calls if "사실 확인 담당" in call.messages[0].content)
    transform = next(call for call in harness.llm.calls if "변환기" in call.messages[0].content)
    assert (search.params.web_search, search.schema) == (True, None)
    assert (transform.params.web_search, transform.params.model_id) == (False, "fake-a-1")
    assert "claims" in transform.schema["properties"]


async def test_disputed_fact_item_gives_the_arbiter_search_evidence():
    def revise(call_input):
        return revision_output(
            [
                resolution(item["item_id"], "REJECTED_WITH_REASON")
                for review in call_input["reviews"]
                for item in review["items"]
                if item["severity"] == "CRITICAL"
            ],
            content=VALID_BODY.replace("본론", "유지한 본론"),
        )

    def transform(call_input):
        if call_input["scope"] == "FULL":
            return fact_transform_output(fact_claim("REFUTED"))(call_input)
        checks = [
            {"item_id": each, "resolved": False, "note": "여전히 틀렸다"} for each in call_input["expected_item_ids"]
        ]
        return json.dumps(
            {
                "item_checks": checks,
                "new_critical_items": [],
                "checklist": {"checked_claims": ["확인"], "checked_code_blocks": [], "unverifiable": []},
                "claims": [],
            }
        )

    harness, draft = await _run(
        FACT_TRANSFORM=transform,
        REVISE_DRAFT=revise,
        ARBITRATE_SEARCH=LLMResponse(
            text="문서에 따르면 작성자가 맞다",
            usage=LLMUsage(prompt_tokens=1, completion_tokens=1),
            citations=[LLMCitation(url="https://docs.example.com/a")],
        ),
        ARBITRATE=[arbitrate_output("UPHOLD_AUTHOR")],
    )

    assert draft.status == PostStatus.READY_TO_PUBLISH
    arbitrate = next(call for call in harness.llm.calls if "리뷰 분쟁을 판정" in call.messages[0].content)
    evidence = call_input_of(arbitrate.messages)["search_evidence"]
    assert "문서에 따르면 작성자가 맞다" in evidence and "https://docs.example.com/a" in evidence
    # 중재 검색은 중재자가 아니라 사실 확인 검색 모델이 한다 (D-92)
    fact_search = next(call for call in harness.llm.calls if "사실 확인 담당" in call.messages[0].content)
    dispute_search = next(call for call in harness.llm.calls if "분쟁의 근거를 찾는" in call.messages[0].content)
    assert dispute_search.params.web_search is True
    assert dispute_search.params.model_id == fact_search.params.model_id != arbitrate.params.model_id


async def test_spot_check_without_changes_or_items_skips_the_search():
    harness, draft = await _run(
        REVIEW_FULL=[review_output("CRITICAL"), review_output()],
        REVISE_DRAFT=lambda call_input: revision_output(
            [resolution(item["item_id"]) for review in call_input["reviews"] for item in review["items"]]
        ),
        SPOT_CHECK=spot_check_output(resolved=True),
    )

    assert draft.status == PostStatus.READY_TO_PUBLISH
    assert harness.llm.all_kinds.count("FACT_CHECK") == 1
    assert [r.scope for r in draft.reviews if r.lens == FACT] == [ReviewScope.FULL, ReviewScope.SPOT_CHECK]


# ---------- 범위 밖 판단 ----------


async def test_major_out_of_scope_goes_to_arbitration_and_uphold_author_closes_it():
    harness, draft = await _run(
        REVIEW_FULL=[review_output("MAJOR"), review_output()],
        REVISE_DRAFT=lambda call_input: revision_output(
            [
                resolution(item["item_id"], "OUT_OF_SCOPE", reason="배포 전환은 이 글의 범위 밖이다")
                for review in call_input["reviews"]
                for item in review["items"]
            ]
        ),
        SPOT_CHECK=spot_check_output(resolved=False),
        ARBITRATE=[arbitrate_output("UPHOLD_AUTHOR")],
    )

    assert draft.status == PostStatus.READY_TO_PUBLISH
    arbitrate = next(call for call in harness.llm.calls if "리뷰 분쟁을 판정" in call.messages[0].content)
    call_input = call_input_of(arbitrate.messages)
    assert call_input["dispute_kind"] == "OUT_OF_SCOPE"
    assert call_input["core_mechanism"] and call_input["out_of_scope"]
    assert call_input["scope_declaration"] == "트레이드오프"
    assert "search_evidence" in call_input and call_input["search_evidence"] is None
    assert "ARBITRATE_SEARCH" not in harness.llm.all_kinds


async def test_out_of_scope_on_a_minor_item_fails_the_gate():
    harness, draft = await _run(
        REVIEW_FULL=[review_output("CRITICAL", "MINOR"), review_output()],
        REVISE_DRAFT=[
            lambda call_input: revision_output(
                [
                    resolution(item["item_id"], "OUT_OF_SCOPE", reason="범위 밖")
                    if item["severity"] == "MINOR"
                    else resolution(item["item_id"])
                    for review in call_input["reviews"]
                    for item in review["items"]
                ]
            ),
            lambda call_input: revision_output(
                [
                    resolution(item["item_id"])
                    for review in call_input["reviews"]
                    for item in review["items"]
                    if item["severity"] == "CRITICAL"
                ]
            ),
        ],
        SPOT_CHECK=spot_check_output(resolved=True),
    )

    assert draft.status == PostStatus.READY_TO_PUBLISH
    assert any("비차단 항목은" in error for error in draft.versions[1].validation_errors)


# ---------- 잘림 ----------


async def test_truncated_write_retries_with_a_higher_limit_not_the_same_one():
    truncated = LLMResponse(
        text='{"title": "제',
        usage=LLMUsage(prompt_tokens=1, completion_tokens=1),
        stop_reason="max_tokens",
        truncated=True,
    )
    harness, draft = await _run(WRITE_DRAFT=[truncated, draft_output()])

    assert draft.status == PostStatus.READY_TO_PUBLISH
    first, second = [call for call in await harness.calls.list_by_draft(draft.draft_id) if call.kind == "WRITE_DRAFT"]
    assert first.error is not None and first.error.startswith("truncated:")
    assert (first.model_binding.max_output_tokens, second.model_binding.max_output_tokens) == (1000, 2000)
    assert len(second.messages) == len(first.messages)  # 계약 위반 안내를 붙이지 않는다


async def test_truncated_until_the_attempts_run_out_is_its_own_failure_code():
    truncated = LLMResponse(text="", usage=LLMUsage(), stop_reason="MAX_TOKENS", truncated=True)
    _, draft = await _run(WRITE_DRAFT=[truncated, truncated])

    assert draft.status == PostStatus.FAILED
    assert draft.failure is not None and draft.failure.code == FailureCode.OUTPUT_TRUNCATED


# ---------- 수준별 구조 ----------


async def test_level_100_post_is_told_it_does_not_need_a_tradeoff_section():
    harness = PipelineHarness(
        _fast_track(WRITE_DRAFT=[draft_output(VALID_BODY.replace("<!-- sec:tradeoff -->\n트레이드오프\n\n", ""))])
    )
    await harness.seed_round()
    draft = await harness.draft(A)
    draft.topic.target_level = 100
    await harness.drafts.save(draft)

    draft = await harness.drive(A)

    assert draft.status == PostStatus.READY_TO_PUBLISH
    write_call = next(call for call in harness.llm.calls if "글 한 편을 한국어로 쓴다" in call.messages[0].content)
    assert "tradeoff" not in call_input_of(write_call.messages)["structure_rules"]["required_sections"]


async def test_evidence_reaches_the_writer():
    harness = PipelineHarness(_fast_track())
    await harness.seed_round()
    publishing_round = await harness.rounds.get("round-1")
    assert publishing_round is not None
    publishing_round.evidence = "git log: 2026-10-30 revision 충돌 3건"
    await harness.rounds.save(publishing_round)

    await harness.drive(A)

    write_call = next(call for call in harness.llm.calls if "글 한 편을 한국어로 쓴다" in call.messages[0].content)
    assert call_input_of(write_call.messages)["evidence"] == "git log: 2026-10-30 revision 충돌 3건"
