"""설명문 파이프라인 단계 출력 검사."""

import copy
from typing import Any

from app.models.pipeline import Parameters
from app.services.expository_checks import (
    check_article,
    check_outline,
    check_outline_fact_check,
    check_review,
    check_topic,
    check_translation,
    count_sentences,
)
from tests.factories import (
    CONCEPT_SECTION_IDS,
    make_expository_concept_outline,
    make_expository_concept_topic,
    make_expository_english_article,
    make_expository_forms,
    make_expository_korean_article,
    make_expository_outline,
    make_expository_region,
    make_expository_section,
    make_expository_topic,
)

TITLE = "셀프 호출이 트랜잭션을 건너뛰는 이유"
FORMS = make_expository_forms()
DIAGNOSIS = ("diagnosis", FORMS["diagnosis"])
CONCEPT = ("concept", FORMS["concept"])
FIRST_CAUSE = 2  # 문제 원인 뼈대의 region 순서: problem, concept, cause…, fix, exceptions, synthesis


def _outline_inputs() -> dict[str, Any]:
    topic = make_expository_topic()
    return {key: topic[key] for key in ("topic", "core_argument", "focus_points", "out_of_scope")}


def _review_inputs() -> dict[str, Any]:
    inputs = _outline_inputs()
    return {**inputs, "outline": make_expository_outline(inputs["core_argument"])}


def _refuted(location: str, corrected_text: str) -> dict[str, Any]:
    return {
        "location": location,
        "claim_type": "API",
        "claim": "claim",
        "verdict": "REFUTED",
        "source_url": "https://docs.spring.io",
        "evidence": "evidence",
        "corrected_text": corrected_text,
        "blocking": False,
    }


def test_count_sentences_ignores_abbreviations_and_version_numbers():
    assert count_sentences("Spring Boot 2.0 uses CGLIB, e.g. for classes. Calls via this.inner() skip it.") == 2


def test_check_topic_rejects_comparison_framing():
    topic = {**make_expository_topic(), "topic": "Natural keys vs surrogate keys"}
    assert any("comparison" in error for error in check_topic(topic, [], FORMS))


def test_check_topic_requires_a_known_form_and_a_focus_count_the_form_takes():
    topic = make_expository_topic()
    assert check_topic(topic, [], FORMS) == []
    assert check_topic(make_expository_concept_topic(), [], FORMS, given=True) == []

    unknown = check_topic({**topic, "form": "essay"}, [], FORMS)
    assert unknown == ["form must be one of ['concept', 'diagnosis', 'procedure', 'taxonomy']"]
    # 문제 원인 양식은 원인 region 이 3개 이상, 개념 양식은 본론이 3~4개다
    assert check_topic({**topic, "focus_points": ["a", "b"]}, [], FORMS) == [
        "focus_points has 2 items; form 'diagnosis' takes 3 or more"
    ]
    five = {**make_expository_concept_topic(), "focus_points": ["a", "b", "c", "d", "e"]}
    assert check_topic(five, [], FORMS, given=True) == ["focus_points has 5 items; form 'concept' takes 3 to 4"]


def test_check_outline_passes_a_valid_outline():
    inputs = _outline_inputs()
    assert check_outline(make_expository_outline(inputs["core_argument"]), inputs, *DIAGNOSIS) == []


def test_check_outline_passes_a_concept_outline_without_a_problem_or_a_fix():
    topic = make_expository_concept_topic()
    outline = make_expository_concept_outline(topic["core_argument"])
    assert check_outline(outline, topic, *CONCEPT) == []

    # 선택 region(분류·대조)은 본론 초점이 넷일 때만 온다
    contrast = make_expository_region(
        "contrast",
        "B-tree and B+tree",
        "A B-tree stores rows in internal pages and a B+tree does not.",
        "The B-tree can stop above the leaves. The B+tree always reaches a leaf.",
        "A lookup of key 7 stops at the root in the B-tree.",
        "Rows in internal pages or only in leaves",
    )
    outline["regions"].insert(4, contrast)
    assert any("focus regions" in error for error in check_outline(outline, topic, *CONCEPT))
    four = {**topic, "focus_points": [*topic["focus_points"], "B-tree against B+tree"]}
    assert check_outline(outline, four, *CONCEPT) == []


def test_check_outline_flags_sentence_counts_and_instruction_wording():
    inputs = _outline_inputs()
    outline = make_expository_outline(inputs["core_argument"])
    outline["regions"][FIRST_CAUSE]["elaboration"] += " A second sentence follows."
    outline["regions"][FIRST_CAUSE + 1]["key_sentence"] += " It also logs."
    outline["regions"][0]["elaboration"] = "A common toy project situation."
    errors = check_outline(outline, inputs, *DIAGNOSIS)
    assert "elaboration of 'Mechanism 1' has 2 sentences; write exactly 1" in errors
    assert "key_sentence of 'Mechanism 2' has 2 sentences; write exactly 1" in errors
    assert any("toy project" in error for error in errors)


def test_check_outline_flags_roles_out_of_order_and_slots_a_role_does_not_use():
    inputs = _outline_inputs()
    outline = make_expository_outline(inputs["core_argument"])
    outline["regions"][0]["takeaway"] = "A disappearing log row"
    outline["regions"][FIRST_CAUSE]["example"] = ""
    errors = check_outline(outline, inputs, *DIAGNOSIS)
    assert (
        "takeaway of 'A failure log that disappears' must be an empty string; role 'problem' does not use it" in errors
    )
    assert "example of 'Mechanism 1' must not be empty" in errors

    swapped = make_expository_outline(inputs["core_argument"])
    swapped["regions"][0], swapped["regions"][1] = swapped["regions"][1], swapped["regions"][0]
    [error] = check_outline(swapped, inputs, *DIAGNOSIS)
    assert error.startswith("region roles are ['concept', 'problem', 'cause'")
    assert "problem (exactly 1), concept (exactly 1), cause (3 or more)" in error


def test_check_review_passes_a_logged_removal_and_refill():
    inputs = _review_inputs()
    revised = copy.deepcopy(inputs["outline"])
    revised["regions"][FIRST_CAUSE + 2] = make_expository_section(9)
    log = [
        {"action": "removed", "heading": "Mechanism 3", "reason": "duplicate"},
        {"action": "added", "heading": "Mechanism 9", "reason": "fills the gap"},
    ]
    assert check_review({"outline": revised, "review_log": log}, inputs, *DIAGNOSIS) == []


def test_check_review_flags_a_rewritten_kept_region():
    inputs = _review_inputs()
    revised = copy.deepcopy(inputs["outline"])
    revised["regions"][FIRST_CAUSE]["takeaway"] = "Rewritten takeaway"
    errors = check_review({"outline": revised, "review_log": []}, inputs, *DIAGNOSIS)
    assert any("rewritten" in error for error in errors)


def test_check_review_flags_an_addition_when_three_regions_were_kept():
    inputs = _review_inputs()
    revised = copy.deepcopy(inputs["outline"])
    revised["regions"].insert(FIRST_CAUSE + 3, make_expository_section(9))
    log = [{"action": "added", "heading": "Mechanism 9", "reason": "more depth"}]
    errors = check_review({"outline": revised, "review_log": log}, inputs, *DIAGNOSIS)
    assert any("only when fewer than 3 remain" in error for error in errors)


def test_check_review_flags_a_change_outside_the_repeatable_role():
    inputs = _review_inputs()
    revised = copy.deepcopy(inputs["outline"])
    revised["regions"][0]["elaboration"] = "OrderService calls saveFailureLog from another bean."
    errors = check_review({"outline": revised, "review_log": []}, inputs, *DIAGNOSIS)
    assert any("outside ['cause'] changed" in error for error in errors)


def test_check_outline_fact_check_allows_only_changes_of_refuted_claims():
    outline = make_expository_outline("A thesis.")
    corrected = copy.deepcopy(outline)
    corrected["regions"][FIRST_CAUSE]["key_sentence"] = "The container wraps the bean in a CGLIB proxy."
    inputs = {"topic": "topic", "outline": outline}
    location = f"regions[{FIRST_CAUSE}].key_sentence"
    claim = _refuted(location, "The container wraps the bean in a CGLIB proxy.")
    assert check_outline_fact_check({"outline": corrected, "claims": [claim]}, inputs, *DIAGNOSIS) == []
    errors = check_outline_fact_check({"outline": corrected, "claims": []}, inputs, *DIAGNOSIS)
    assert any(f"{location} changed" in error for error in errors)


def test_check_article_takes_the_section_order_of_the_form():
    article = {"title": "t", "content_markdown": make_expository_english_article(ids=CONCEPT_SECTION_IDS)}
    assert check_article(article, CONCEPT_SECTION_IDS) == []
    errors = check_article(article, ["intro", "structure", "rationale", "mechanism", "conclusion"])
    assert any("section anchors are" in error for error in errors)


def test_check_translation_passes_matching_polite_article():
    english, korean = make_expository_english_article(), make_expository_korean_article(glossary=False)
    output = {"title": TITLE, "content_markdown": korean}
    assert check_translation(output, english, Parameters(draft_min_chars=10)) == []


def test_check_translation_flags_changed_anchor_and_plain_register():
    english = make_expository_english_article()
    korean = make_expository_korean_article(glossary=False)
    plain = korean.replace("설명합니다", "설명한다").replace("않습니다", "않는다").replace("sec:concept", "sec:basics")
    errors = check_translation({"title": TITLE, "content_markdown": plain}, english, Parameters(draft_min_chars=10))
    assert any("anchors changed" in error for error in errors)
    assert any("합쇼체" in error for error in errors)
