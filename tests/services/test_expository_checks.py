"""설명문 파이프라인 단계 출력 검사."""

import copy
from typing import Any

from app.models.pipeline import Parameters
from app.services.expository_checks import (
    check_outline,
    check_outline_fact_check,
    check_review,
    check_topic,
    check_translation,
    count_sentences,
)
from tests.factories import (
    make_expository_english_article,
    make_expository_korean_article,
    make_expository_outline,
    make_expository_section,
    make_expository_topic,
)

TITLE = "셀프 호출이 트랜잭션을 건너뛰는 이유"


def _outline_inputs() -> dict[str, Any]:
    topic = make_expository_topic()
    return {key: topic[key] for key in ("topic", "core_argument", "focus_mechanisms", "out_of_scope")}


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
    assert any("comparison" in error for error in check_topic(topic, []))


def test_check_outline_passes_a_valid_outline():
    inputs = _outline_inputs()
    assert check_outline(make_expository_outline(inputs["core_argument"]), inputs) == []


def test_check_outline_flags_three_sentence_principle_and_instruction_wording():
    inputs = _outline_inputs()
    outline = make_expository_outline(inputs["core_argument"])
    outline["deep_dive_sections"][0]["principle"] += " A third sentence follows."
    outline["introduction"]["problem_statement"] = "A common toy project situation."
    errors = check_outline(outline, inputs)
    assert any("exactly two" in error for error in errors)
    assert any("toy project" in error for error in errors)


def test_check_review_passes_a_logged_removal_and_refill():
    inputs = _review_inputs()
    revised = copy.deepcopy(inputs["outline"])
    revised["deep_dive_sections"][2] = make_expository_section(9)
    log = [
        {"action": "removed", "sub_topic": "Mechanism 3", "reason": "duplicate"},
        {"action": "added", "sub_topic": "Mechanism 9", "reason": "fills the gap"},
    ]
    assert check_review({"outline": revised, "review_log": log}, inputs) == []


def test_check_review_flags_a_rewritten_kept_section():
    inputs = _review_inputs()
    revised = copy.deepcopy(inputs["outline"])
    revised["deep_dive_sections"][0]["key_takeaway"] = "Rewritten takeaway"
    errors = check_review({"outline": revised, "review_log": []}, inputs)
    assert any("rewritten" in error for error in errors)


def test_check_review_flags_an_addition_when_three_sections_were_kept():
    inputs = _review_inputs()
    revised = copy.deepcopy(inputs["outline"])
    revised["deep_dive_sections"].append(make_expository_section(9))
    log = [{"action": "added", "sub_topic": "Mechanism 9", "reason": "more depth"}]
    errors = check_review({"outline": revised, "review_log": log}, inputs)
    assert any("only when fewer than 3 remain" in error for error in errors)


def test_check_outline_fact_check_allows_only_changes_of_refuted_claims():
    outline = make_expository_outline("A thesis.")
    corrected = copy.deepcopy(outline)
    corrected["deep_dive_sections"][0]["principle"] = (
        "The container wraps the bean in a CGLIB proxy. Therefore only proxied calls get advice."
    )
    inputs = {"topic": "topic", "outline": outline}
    claim = _refuted("deep_dive_sections[0].principle", "The container wraps the bean in a CGLIB proxy.")
    assert check_outline_fact_check({"outline": corrected, "claims": [claim]}, inputs) == []
    errors = check_outline_fact_check({"outline": corrected, "claims": []}, inputs)
    assert any("deep_dive_sections[0].principle changed" in error for error in errors)


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
