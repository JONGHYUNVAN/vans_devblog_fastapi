"""파이프라인 버전 문서 모델: 조절 값 기본값이 1a 이전 코드와 같고, 해시는 본문만·키 순서와 무관하다 (설계 09 문서 2절)."""

import pytest
from pydantic import ValidationError

from app.models.enums import ClaimStatus, FinalReadKind, IssueSeverity
from app.models.pipeline import (
    BODY_FIELDS,
    Parameters,
    PipelineVersion,
    PipelineVersionBody,
    StructureParameters,
    content_hash_of,
    version_id_of,
)
from tests.factories import make_pipeline_body, make_pipeline_version


def test_parameter_defaults_reproduce_the_pre_1a_settings_and_constants():
    parameters = Parameters()

    assert (parameters.max_iterations, parameters.max_validation_retries, parameters.max_llm_retries) == (2, 2, 3)
    assert (parameters.max_publish_attempts, parameters.max_topic_attempts, parameters.draft_token_limit) == (
        3,
        2,
        300_000,
    )
    assert (parameters.draft_min_chars, parameters.draft_max_chars, parameters.min_code_blocks) == (4000, 20000, 1)
    assert parameters.structure.required_sections == ["intro", "body-1", "tradeoff"]
    assert parameters.fact_check.severity == {
        ClaimStatus.REFUTED: IssueSeverity.CRITICAL,
        ClaimStatus.UNVERIFIABLE: IssueSeverity.MINOR,
    }
    assert parameters.final_read.blocking_kinds == [
        FinalReadKind.BODY_CODE_CONTRADICTION,
        FinalReadKind.CORE_MECHANISM_MISSING,
    ]
    assert parameters.polish.phrase_fixes[0] == ("하십시오", "한다")
    assert parameters.publish.requires_approval is True
    assert parameters.publish.note.heading == "## 이 글은 어떻게 만들어졌나"
    assert (parameters.glossary.min_terms, parameters.glossary.max_terms, parameters.glossary.full_list_max) == (
        8,
        12,
        300,
    )


def test_required_sections_follow_the_level_rule():
    structure = StructureParameters()

    assert structure.required_sections_for(100) == ["intro", "body-1"]
    assert structure.required_sections_for(300) == ["intro", "body-1", "tradeoff"]
    assert structure.required_sections_for(None) == ["intro", "body-1", "tradeoff"]


def test_content_hash_covers_the_body_only_and_ignores_key_order():
    body = make_pipeline_body()
    reordered = PipelineVersionBody.model_validate(dict(reversed(list(body.model_dump(include=BODY_FIELDS).items()))))
    first = make_pipeline_version("v1", 1, body=body)
    second = make_pipeline_version("v2", 2, parent_version_id="v1", body=body)

    assert content_hash_of(body) == content_hash_of(reordered)
    assert first.content_hash == second.content_hash == content_hash_of(body)
    assert first.body() == body
    assert content_hash_of(make_pipeline_body(max_iterations=3)) != content_hash_of(body)


def test_version_id_is_the_sequence_number():
    assert version_id_of(7) == "v7"
    with pytest.raises(ValidationError):
        make_pipeline_version(seq=0)


def test_route_documents_round_trip_to_the_agent_routes_shape():
    body = make_pipeline_body(with_glossary=True)
    documents = body.route_documents()

    assert documents[0] == {"_id": "SLOTS", "A": "architect", "B": "systems_engineer", "C": "troubleshooter"}
    assert documents[1]["_id"] == "LEVEL_CRITERIA" and documents[1]["version"] == "v1"
    assert {each["_id"] for each in documents[2:]} == set(body.routes)
    assert documents[2]["candidates"][0] == {"provider": "fake-a", "model_id": "fake-a-1", "max_output_tokens": 1000}


def test_stored_document_loads_back():
    version = make_pipeline_version(label="x") if False else make_pipeline_version()
    stored = version.model_dump(by_alias=True)

    assert stored["_id"] == "v1" and "version_id" not in stored
    assert PipelineVersion.model_validate(stored) == version


def test_style_and_code_comment_parameters_have_the_d85_defaults():
    """D-85: 어미는 게이트가 잰다. 옛 버전 문서(v1)에 이 키가 없어도 기본값으로 읽힌다."""
    parameters = Parameters()
    assert (parameters.style.minority_register_ratio, parameters.style.minority_register_min) == (0.1, 3)
    assert parameters.style.forbid_imperatives is True
    assert (parameters.code_comments.min_lines, parameters.code_comments.lines_per_comment) == (12, 25)
