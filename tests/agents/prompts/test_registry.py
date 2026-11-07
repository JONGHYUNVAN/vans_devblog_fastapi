"""프롬프트 템플릿 로더·가족·라운드 생성 전 검증 테스트."""

import json
from pathlib import Path

import pytest

from app.agents.prompts.registry import (
    PROMPT_FAMILIES,
    PROMPT_VERSION,
    TEMPLATE_NAMES,
    load_template,
    prompt_family,
    templates_for_route,
    validate_prompts,
)
from app.models.model_binding import CallModel
from app.models.routing import build_snapshot

ROUTES_EXAMPLE = Path(__file__).parents[3] / "agents_design" / "routes.example.json"
# v4 명세 §2.5 의 디렉터리 표. 가족에 없는 템플릿은 그 가족이 맡지 않는 호출이다
V4_EXPECTED = {
    "claude": set(TEMPLATE_NAMES) - {"fact_transform", "arbitrate_search", "glossary"},
    "gemini": set(TEMPLATE_NAMES) - {"fact_transform", "arbitrate_search", "glossary"},
    "grok": {
        "propose_topic",
        "write_draft",
        "revise_draft",
        "review_full",
        "review_spot_check",
        "fact_check",
        "arbitrate",
    },
    "haiku": {"fact_transform", "glossary"},
}


def _model(provider: str, model_id: str) -> CallModel:
    return CallModel(provider=provider, model_id=model_id, max_output_tokens=1000)


FLAT_VERSION = "v3"  # 평면 배치는 v1~v3 다. v4 는 가족 디렉터리만 있다


@pytest.mark.parametrize("name", TEMPLATE_NAMES)
def test_every_flat_template_exists_and_asks_for_json_only(name):
    template = load_template(name, FLAT_VERSION)

    assert "JSON" in template
    assert "$" not in template.replace("$display_name", "").replace("$writer_bias", "")


@pytest.mark.parametrize("family", PROMPT_FAMILIES)
def test_v4_family_templates_match_the_spec_layout(family):
    present = {
        name
        for name in TEMPLATE_NAMES
        if (Path(__file__).parents[3] / "app/agents/prompts/v4" / family / f"{name}.md").is_file()
    }

    assert present == V4_EXPECTED[family]
    for name in present:
        template = load_template(name, "v4", family)
        assert "JSON" in template
        assert "$display_name" not in template
        assert "$" not in template.replace("$writer_bias", "")
        assert ("$writer_bias" in template) == (name == "propose_topic")


def test_v4_common_template_serves_every_family():
    for family in PROMPT_FAMILIES:
        assert "Dispute Fact Investigation" in load_template("arbitrate_search", "v4", family)


def test_prompt_family_is_a_model_family_not_a_vendor():
    assert prompt_family(_model("anthropic", "claude-opus-5-5")) == "claude"
    assert prompt_family(_model("anthropic", "claude-haiku-5-5")) == "haiku"
    assert prompt_family(_model("google", "gemini-4-argon")) == "gemini"
    assert prompt_family(_model("xai", "grok-4.7")) == "grok"
    assert (
        prompt_family(_model("fake", "fake-1")) == "fake"
    )  # 가짜 벤더는 가족 디렉터리가 없어 평면 템플릿으로 내려간다


def test_templates_for_route_covers_fact_lens_and_ignores_unknown_routes():
    assert templates_for_route("REVIEW:FACT_STRUCTURE") == ("fact_check", "review_full", "review_spot_check")
    assert templates_for_route("REVIEW:LOGIC_READABILITY") == ("review_full", "review_spot_check")
    assert templates_for_route("WRITE_DRAFT:architect") == ("write_draft",)
    assert templates_for_route("ARBITRATE") == ("arbitrate", "arbitrate_search")
    assert templates_for_route("GLOSSARY") == ("glossary",)
    assert templates_for_route("POLISH_FORMAT") == () and templates_for_route("SLOTS") == ()


def test_glossary_template_is_the_same_file_in_v3_flat_and_v4_haiku():
    """GLOSSARY 는 v3 운영 중에도 템플릿을 찾아야 한다 (08 문서 Q-25 결정). 두 자리의 문구가 같아야 가족과 무관하게 같은 호출이다."""
    assert load_template("glossary", "v3") == load_template("glossary", "v4", "haiku")
    assert "glossary extractor" in load_template("glossary", "v3")


def test_example_routes_have_every_v4_template_they_need():
    documents = json.loads(ROUTES_EXAMPLE.read_text(encoding="utf-8"))
    snapshot = build_snapshot(documents, {"anthropic", "google", "xai"})

    validate_prompts(snapshot, "v4")
    validate_prompts(snapshot, "v3")


def test_validate_prompts_names_the_missing_family_template():
    documents = json.loads(ROUTES_EXAMPLE.read_text(encoding="utf-8"))
    for document in documents:
        if document["_id"] == "FINAL_READ":
            document["candidates"].append({"provider": "xai", "model_id": "grok-4.7", "max_output_tokens": 8000})
    snapshot = build_snapshot(documents, {"anthropic", "google", "xai"})

    with pytest.raises(ValueError, match="FINAL_READ: final_read for grok"):
        validate_prompts(snapshot, "v4")


def test_unknown_template_name_raises():
    with pytest.raises(ValueError):
        load_template("nope")


def test_unknown_version_raises_file_not_found():
    with pytest.raises(FileNotFoundError):
        load_template("arbitrate", "v0")


@pytest.mark.parametrize("family", ["claude", "gemini", "grok"])
def test_v4_write_and_revise_templates_keep_the_register_and_length_budget(family):
    """첫 비교(2026-11-07)에서 돌아온 결함: 길이 초과, "~합니다" 어미, 상투 결론. 작성·수정 프롬프트가 그 규칙을 잃지 않게 묶어 둔다."""
    write = load_template("write_draft", "v4", family)
    assert "60" in write and "max_chars" in write and "40%" in write
    assert "~다" in write and "합니다" in write and "마치며" in write
    assert "transliteration" in write and "스테이트리스" in write
    revise = load_template("revise_draft", "v4", family)
    assert "~다" in revise and "합니다" in revise


@pytest.mark.parametrize("family", ["claude", "gemini", "grok"])
def test_v4_propose_topic_templates_fix_terminology(family):
    """주제 제안이 만든 음차("스테이트리스")를 작성자가 그대로 따랐다. 표기 규칙은 제안 쪽에도 있어야 한다."""
    template = load_template("propose_topic", "v4", family)
    assert "transliteration" in template and "스테이트리스" in template and "stateless" in template


def test_default_prompt_version_is_v4_everywhere():
    """2026-11-07 사용자 결정(D-62): 2차 비교 뒤 기본값을 v4 로 올렸다. 레지스트리 상수와 Settings 기본값이 어긋나면 라운드와 호출이 다른 버전을 본다."""
    from app.core.config import Settings

    assert PROMPT_VERSION == "v4"
    assert Settings(_env_file=None).prompt_version == PROMPT_VERSION
