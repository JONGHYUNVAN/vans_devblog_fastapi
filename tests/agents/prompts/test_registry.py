"""프롬프트 템플릿 조회·가족·파일 로더·라운드 생성 전 검증 테스트 (설계 09 문서 1a).

실행 중에는 버전 문서의 prompts(PromptSet)에서 읽고, 저장소 파일은 기준 버전의 입력이다.
템플릿 문구의 불변식(JSON 언급, $ 잔존, 가족 배치)은 services/pipeline_validation 의 R-05·R-08 이 검사한다.
"""

import json
from pathlib import Path

import pytest

from app.agents.prompts.registry import (
    PROMPT_FAMILIES,
    TEMPLATE_NAMES,
    MissingTemplateError,
    load_prompt_files,
    load_template,
    prompt_family,
    resolve_template,
    templates_for_route,
    validate_prompts,
)
from app.models.model_binding import CallModel
from app.models.routing import build_snapshot

ROUTES_EXAMPLE = Path(__file__).parents[3] / "agents_design" / "routes.example.json"
PROMPTS_DIR = Path(__file__).parents[3] / "app" / "agents" / "prompts"
# v4 명세 §2.5 의 디렉터리 표. 가족에 없는 템플릿은 그 가족이 맡지 않는 호출이다
V4_EXPECTED = {
    "claude": set(TEMPLATE_NAMES) - {"fact_transform", "arbitrate_search", "glossary", "polish_revise"},
    "gemini": set(TEMPLATE_NAMES) - {"fact_transform", "arbitrate_search", "glossary", "polish_revise"},
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
    "common": {"arbitrate_search", "polish_revise"},
}


def _model(provider: str, model_id: str) -> CallModel:
    return CallModel(provider=provider, model_id=model_id, max_output_tokens=1000)


def test_load_prompt_files_reads_family_directories_and_flat_files_as_common():
    v4 = load_prompt_files("v4")
    v3 = load_prompt_files("v3")

    assert {family: set(templates) for family, templates in v4.items()} == V4_EXPECTED
    assert set(v3) == {"common"} and set(v3["common"]) == set(
        TEMPLATE_NAMES
    )  # polish_format 은 이름 밖이라 읽지 않는다
    assert load_prompt_files(PROMPTS_DIR / "v4") == v4  # 경로로도 읽는다


def test_resolve_template_falls_back_from_family_to_common():
    prompts = {"claude": {"write_draft": "claude 전용"}, "common": {"write_draft": "공통", "arbitrate_search": "검색"}}

    assert resolve_template(prompts, "write_draft", "claude") == "claude 전용"
    assert resolve_template(prompts, "write_draft", "grok") == "공통"
    assert resolve_template(prompts, "arbitrate_search", "claude") == "검색"
    with pytest.raises(MissingTemplateError):
        resolve_template(prompts, "final_read", "claude")
    with pytest.raises(ValueError):
        resolve_template(prompts, "nope", "claude")


def test_prompt_family_is_a_model_family_not_a_vendor():
    assert prompt_family(_model("anthropic", "claude-opus-5-5")) == "claude"
    assert prompt_family(_model("anthropic", "claude-haiku-5-5")) == "haiku"
    assert prompt_family(_model("google", "gemini-4-argon")) == "gemini"
    assert prompt_family(_model("xai", "grok-4.7")) == "grok"
    assert prompt_family(_model("fake", "fake-1")) == "fake"  # 가짜 벤더는 가족이 없어 common 으로 내려간다


def test_templates_for_route_covers_fact_lens_and_ignores_unknown_routes():
    assert templates_for_route("REVIEW:FACT_STRUCTURE") == ("fact_check", "review_full", "review_spot_check")
    assert templates_for_route("REVIEW:LOGIC_READABILITY") == ("review_full", "review_spot_check")
    assert templates_for_route("WRITE_DRAFT:architect") == ("write_draft",)
    assert templates_for_route("ARBITRATE") == ("arbitrate", "arbitrate_search")
    assert templates_for_route("GLOSSARY") == ("glossary",)
    assert templates_for_route("POLISH_FORMAT") == () and templates_for_route("SLOTS") == ()


def test_glossary_template_is_the_same_file_in_v3_flat_and_v4_haiku():
    """GLOSSARY 는 v3 운영 중에도 템플릿을 찾아야 했다 (08 문서 D-69). 기준 버전의 입력이 그대로다."""
    assert load_template("glossary", "v3") == load_template("glossary", "v4", "haiku")
    assert "glossary extractor" in load_template("glossary", "v3")


def test_example_routes_have_every_template_they_need_in_v4_and_v3():
    documents = json.loads(ROUTES_EXAMPLE.read_text(encoding="utf-8"))
    snapshot = build_snapshot(documents, {"anthropic", "google", "xai"})

    validate_prompts(snapshot, load_prompt_files("v4"))
    validate_prompts(snapshot, load_prompt_files("v3"))


def test_validate_prompts_names_the_missing_family_template():
    documents = json.loads(ROUTES_EXAMPLE.read_text(encoding="utf-8"))
    for document in documents:
        if document["_id"] == "FINAL_READ":
            document["candidates"].append({"provider": "xai", "model_id": "grok-4.7", "max_output_tokens": 8000})
    snapshot = build_snapshot(documents, {"anthropic", "google", "xai"})

    with pytest.raises(ValueError, match="FINAL_READ: final_read for grok"):
        validate_prompts(snapshot, load_prompt_files("v4"))


def test_unknown_template_name_and_version_raise():
    with pytest.raises(ValueError):
        load_template("nope", "v4")
    with pytest.raises(FileNotFoundError):
        load_template("arbitrate", "v0")
    with pytest.raises(FileNotFoundError):
        load_prompt_files("v0")


@pytest.mark.parametrize("family", PROMPT_FAMILIES)
def test_v4_family_templates_substitute_only_writer_bias(family):
    for name, template in load_prompt_files("v4").get(family, {}).items():
        assert "$display_name" not in template
        assert "$" not in template.replace("$writer_bias", "")
        assert ("$writer_bias" in template) == (name == "propose_topic")
