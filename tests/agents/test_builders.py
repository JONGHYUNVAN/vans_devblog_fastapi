"""빌더 테스트. 도메인 상태가 프롬프트 입력으로 어떻게 옮겨지는지(블라인드, 이월 항목, 중재, 게이트 오류)를 확인한다."""

import json

from app.agents.builders import (
    build_arbitrate_input,
    build_propose_topic_input,
    build_review_input,
    build_revise_draft_input,
    build_spot_check_input,
    build_write_draft_input,
    render_messages,
)
from app.agents.contracts import StructureRules
from app.agents.personas import PERSONAS
from app.models.enums import AgentSlot, ArbitrationOutcome, IssueSeverity, PostStatus, ReviewLens, RevisionCause
from app.models.post_draft import PostDraft
from app.models.publishing_round import LockedTopic, TopicAttempt
from app.services.evaluation import evaluate
from tests.factories import (
    MAX_ITERATIONS,
    MAX_VALIDATION_RETRIES,
    accept,
    add_reviews,
    make_arbitration,
    make_draft,
    make_registry_entry,
    make_round,
    make_topic,
    reject,
    write_next_version,
)

A, B, C = AgentSlot.A, AgentSlot.B, AgentSlot.C
RULES = StructureRules(min_chars=10, max_chars=1000)
ROUND = make_round(
    slot_bindings={
        A: make_round().slot_bindings[A].model_copy(update={"persona_id": "architect"}),
        B: make_round().slot_bindings[B].model_copy(update={"persona_id": "systems_engineer"}),
        C: make_round().slot_bindings[C].model_copy(update={"persona_id": "troubleshooter"}),
    }
)


def _apply(draft: PostDraft) -> None:
    draft.apply_evaluation(evaluate(draft, MAX_ITERATIONS))


def _revising_after_full(*severities: IssueSeverity):
    draft = make_draft()
    write_next_version(draft)
    add_reviews(draft, {B: severities})
    _apply(draft)
    return draft, draft.open_blocking_items(1)


def test_propose_topic_input_adds_other_slots_locked_topics_to_avoid_list():
    publishing_round = ROUND.model_copy(deep=True)
    locked = LockedTopic(slot=B, source_attempt=1, proposal_index=0, proposal=make_topic())
    publishing_round.topic_attempts = [TopicAttempt(attempt_number=1, locked={B: locked})]
    publishing_round.theme_keyword = "mongodb"

    result = build_propose_topic_input(
        publishing_round,
        A,
        attempt_number=2,
        avoid_entries=[make_registry_entry(title="예전 글")],
        conflict_feedback="겹쳤다",
    )

    assert result.persona == PERSONAS["architect"]
    assert [topic.title for topic in result.avoid_topics] == ["예전 글", make_topic().title]
    assert (result.theme_keyword, result.attempt_number, result.conflict_feedback) == ("mongodb", 2, "겹쳤다")
    assert result.categories == publishing_round.categories


def test_write_draft_input_uses_author_persona_and_rules():
    draft = make_draft(B)

    result = build_write_draft_input(draft, ROUND, RULES)

    assert result.persona.persona_id == "systems_engineer"
    assert result.topic == draft.topic
    assert result.structure_rules.required_sections == ["intro", "body-1", "tradeoff", "conclusion"]


def test_revise_input_after_full_review_carries_blocking_and_minor_items_without_model_names():
    draft, blocking = _revising_after_full(IssueSeverity.CRITICAL, IssueSeverity.MINOR)
    draft.start_drafting(MAX_ITERATIONS)

    result = build_revise_draft_input(draft, ROUND, RULES, max_iterations=MAX_ITERATIONS)

    assert result.previous.content_markdown == draft.versions[0].content_markdown
    assert [review.reviewer_label for review in result.reviews] == ["리뷰어 1", "리뷰어 2"]
    review_b = next(review for review in result.reviews if review.lens == ReviewLens.FACT_STRUCTURE)
    assert [item.severity for item in review_b.items] == [IssueSeverity.CRITICAL, IssueSeverity.MINOR]
    assert review_b.items[0].item_id == blocking[0].item_id
    assert (result.arbitration_results, result.validation_errors, result.human_instruction) == ([], [], None)
    assert (result.iteration, result.max_iterations) == (1, MAX_ITERATIONS)
    dumped = result.model_dump_json()
    assert "fake-1" not in dumped and "A" not in json.loads(dumped)["reviews"][0]["reviewer_label"]


def test_revise_input_after_spot_check_carries_unresolved_notes_and_arbitration():
    draft, items = _revising_after_full(IssueSeverity.CRITICAL)
    write_next_version(draft, reject(*items))
    add_reviews(draft, resolved={items[0].item_id: False})
    _apply(draft)
    assert draft.status == PostStatus.IN_ARBITRATION
    draft.add_arbitration(make_arbitration(draft, items[0], ArbitrationOutcome.UPHOLD_REVIEWER))
    _apply(draft)
    draft.start_drafting(MAX_ITERATIONS)

    result = build_revise_draft_input(draft, ROUND, RULES, max_iterations=MAX_ITERATIONS)

    [review] = result.reviews
    assert review.unresolved_notes == {items[0].item_id: "아직 고쳐지지 않았다"}
    assert [item.item_id for item in review.items] == [items[0].item_id]
    [arbitration] = result.arbitration_results
    assert (arbitration.item_id, arbitration.must_accept) == (items[0].item_id, True)
    assert result.iteration == 2


def test_revise_input_after_gate_failure_passes_validation_errors():
    draft = make_draft()
    draft.start_drafting(MAX_ITERATIONS)
    draft.submit_version(
        title="t", summary="s", content_markdown="본문", section_ids=[], resolutions=[], author_note="", call_id="c"
    )
    draft.fail_validation(["필수 앵커가 없다: intro"], MAX_VALIDATION_RETRIES)
    draft.start_drafting(MAX_ITERATIONS)

    result = build_revise_draft_input(draft, ROUND, RULES, max_iterations=MAX_ITERATIONS)

    assert result.validation_errors == ["필수 앵커가 없다: intro"]
    assert result.reviews == []


def test_revise_input_includes_human_instruction_for_human_revision_and_its_gate_retry():
    draft, _ = _revising_after_full(IssueSeverity.CRITICAL)
    draft.revision_cause = RevisionCause.HUMAN
    draft.human_instruction = "결론을 줄여라"
    draft.start_drafting(MAX_ITERATIONS)
    assert (
        build_revise_draft_input(draft, ROUND, RULES, max_iterations=MAX_ITERATIONS).human_instruction
        == "결론을 줄여라"
    )

    draft.submit_version(
        title="t", summary="s", content_markdown="x", section_ids=[], resolutions=[], author_note="", call_id="c"
    )
    draft.fail_validation(["짧다"], MAX_VALIDATION_RETRIES)
    draft.start_drafting(MAX_ITERATIONS)

    assert (
        build_revise_draft_input(draft, ROUND, RULES, max_iterations=MAX_ITERATIONS).human_instruction
        == "결론을 줄여라"
    )


def test_review_input_is_blind_and_carries_author_tone_markers():
    draft = make_draft(A)
    write_next_version(draft)

    result = build_review_input(draft, ROUND, C)

    assert (result.scope, result.lens, result.secondary_lens) == ("FULL", ReviewLens.CRITICAL_PRACTICAL, None)
    assert result.tone_preservation.author_tone_markers == PERSONAS["architect"].tone_markers
    dumped = result.model_dump_json()
    assert "persona" not in result.model_dump()
    assert "author_slot" not in dumped and "display_name" not in dumped and "fake-1" not in dumped


def test_spot_check_input_filters_items_and_resolutions_to_the_slot():
    draft, items = _revising_after_full(IssueSeverity.CRITICAL)
    write_next_version(draft, accept(*items))

    result = build_spot_check_input(draft, ROUND, B)

    assert result.previous_content == draft.versions[0].content_markdown
    assert result.current_content == draft.versions[1].content_markdown
    assert [item.item_id for item in result.my_previous_items] == [items[0].item_id]
    assert [resolution.item_id for resolution in result.resolutions] == [items[0].item_id]
    assert result.lens == ReviewLens.FACT_STRUCTURE


def test_arbitrate_input_extracts_sections_reason_and_note():
    draft, items = _revising_after_full(IssueSeverity.CRITICAL)
    item = items[0]
    draft.start_drafting(MAX_ITERATIONS)
    draft.submit_version(
        title="t",
        summary="s",
        content_markdown="<!-- sec:intro -->\n서론\n<!-- sec:body-1 -->\n고친 본론",
        section_ids=["intro", "body-1"],
        resolutions=reject(item),
        author_note="",
        call_id="c",
    )
    draft.pass_validation()
    add_reviews(draft, resolved={item.item_id: False})
    _apply(draft)

    result = build_arbitrate_input(draft, item)

    assert result.item == item
    assert result.section_before == ""  # v1 본문(팩토리)에는 body-1 앵커가 없다
    assert result.section_after == "고친 본론"
    assert result.author_rejection_reason == "근거가 있다"
    assert result.reviewer_unresolved_note == "아직 고쳐지지 않았다"


def test_render_messages_substitutes_persona_and_dumps_input_json():
    draft = make_draft(C)

    messages = render_messages(build_write_draft_input(draft, ROUND, RULES), "v1")

    assert [message.role for message in messages] == ["system", "user"]
    assert "실용주의 트러블슈터" in messages[0].content and "$" not in messages[0].content
    payload = json.loads(messages[1].content.removeprefix("입력(JSON):\n"))
    assert payload["topic"]["title"] == draft.topic.title
