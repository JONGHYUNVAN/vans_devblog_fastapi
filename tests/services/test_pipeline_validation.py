"""버전 문서 검증 규칙 R-04·R-05·R-08~R-11 (설계 09 문서 4절). 규칙마다 통과 1건·위반 1건."""

import json
from pathlib import Path

from app.models.enums import AgentSlot
from app.models.pipeline import EXPOSITORY_FAMILY, content_hash_of
from app.services.pipeline_validation import Finding, errors_of, validate_version
from app.services.pipeline_versions import build_baseline_version
from tests.factories import make_expository_body, make_pipeline_body

ROUTES_EXAMPLE = Path(__file__).parents[2] / "agents_design" / "routes.example.json"


def _rules(findings: list[Finding], level: str | None = None) -> set[str]:
    return {finding.rule for finding in findings if level is None or finding.level == level}


def test_fixture_and_baseline_bodies_pass_without_errors():
    assert errors_of(validate_version(make_pipeline_body())) == []
    documents = json.loads(ROUTES_EXAMPLE.read_text(encoding="utf-8"))
    baseline = build_baseline_version("v4", documents)
    findings = validate_version(baseline, expected_hash=content_hash_of(baseline))

    assert errors_of(findings) == []
    assert _rules(findings, "warning") <= {"R-05"}  # 어느 라우트도 쓰지 않는 템플릿만 경고일 수 있다


def test_r04_rejects_routes_that_cannot_be_chosen_to_the_end():
    body = make_pipeline_body()
    del body.routes["ARBITRATE"]

    findings = validate_version(body)

    assert "R-04" in _rules(errors_of(findings))
    assert any("ARBITRATE" in finding.message for finding in findings)


def test_r05_names_the_missing_family_template_and_warns_about_orphans():
    body = make_pipeline_body()
    del body.prompts["common"]["final_read"]
    body.prompts["claude"] = {"write_draft": "claude 전용 JSON"}  # 가짜 벤더 라우트는 claude 가족을 쓰지 않는다

    findings = validate_version(body)

    errors = [finding for finding in errors_of(findings) if finding.rule == "R-05"]
    assert errors and all("FINAL_READ: template final_read for fake-" in error.message for error in errors)
    assert any(finding.level == "warning" and "claude.write_draft" in finding.message for finding in findings)


def test_r08_checks_json_mention_placeholders_and_family_names():
    body = make_pipeline_body()
    body.prompts["common"]["final_read"] = "$display_name 를 부른다"  # JSON 언급 없음
    body.prompts["common"]["propose_topic"] = "주제 JSON"  # $writer_bias 없음
    body.prompts["nope"] = {"write_draft": "JSON"}
    body.prompts["common"]["arbitrate_search"] = "검색 호출은 글로 답한다"  # JSON 불필요

    messages = [finding.message for finding in errors_of(validate_version(body)) if finding.rule == "R-08"]

    assert any("final_read: does not ask for JSON" in message for message in messages)
    assert any("stray placeholder $display_name" in message for message in messages)
    assert any("propose_topic must substitute $writer_bias" in message for message in messages)
    assert any("unknown prompt family nope" in message for message in messages)
    assert not any("arbitrate_search" in message for message in messages)


def test_r08_prompt_lint_merges_code_defaults_with_data_and_only_warns():
    body = make_pipeline_body()
    body.prompts["claude"] = {
        "write_draft": "Write JSON. 60 max_chars 40% comments 마치며 transliteration 스테이트리스"
    }
    body.parameters.prompt_lint = {"write_draft": ["멱등성"]}

    findings = validate_version(body)

    lint = [finding for finding in findings if "expected phrase" in finding.message]
    assert [finding.level for finding in lint] == ["warning"]
    assert "'멱등성'" in lint[0].message
    body.parameters.prompt_lint = {}
    body.prompts["claude"]["write_draft"] = "Write JSON only"
    defaults = [finding for finding in validate_version(body) if "expected phrase" in finding.message]
    assert len(defaults) == 7  # 데이터에서 lint 를 비워도 코드 기본 목록은 남는다 (Q-38). 어미 구절 둘은 D-85 로 뺐다


def test_r09_checks_parameter_ranges():
    body = make_pipeline_body(draft_min_chars=50, draft_max_chars=10, max_iterations=0)
    body.parameters.structure.required_sections = ["body-1"]
    body.parameters.polish.phrase_fixes = [("(unclosed", "x")]
    body.parameters.polish.tell_patterns[0].pattern = "(unclosed"
    body.parameters.polish.tell_patterns[1].id = body.parameters.polish.tell_patterns[2].id
    body.parameters.publish.note.reviewers = "- 리뷰: $model"  # edit-param 오타. 줄에 모델이 빠진다

    messages = [finding.message for finding in errors_of(validate_version(body)) if finding.rule == "R-09"]

    assert any("draft_min_chars" in message for message in messages)
    assert any("max_iterations" in message for message in messages)
    assert any("must include intro" in message for message in messages)
    assert any("does not compile" in message for message in messages)
    assert "publish.note.reviewers must contain $models" in messages
    assert any("polish.tell_patterns D-14 pattern" in message for message in messages)
    assert "polish.tell_patterns ids must be unique" in messages


def test_r10_checks_slots_and_distinct_personas_and_lenses():
    body = make_pipeline_body()
    body.slots[AgentSlot.B] = "architect"

    messages = [finding.message for finding in errors_of(validate_version(body)) if finding.rule == "R-10"]
    assert any("distinct persona" in message for message in messages)

    body = make_pipeline_body()
    body.slots[AgentSlot.C] = "nobody"
    messages = [finding.message for finding in errors_of(validate_version(body)) if finding.rule == "R-10"]
    assert any("unknown persona nobody" in message for message in messages)


def test_r11_compares_the_declared_hash_with_the_body():
    body = make_pipeline_body()

    assert errors_of(validate_version(body, expected_hash=content_hash_of(body))) == []
    assert _rules(errors_of(validate_version(body, expected_hash="sha256:0"))) == {"R-11"}


def test_r13_passes_the_repository_expository_inputs_and_names_what_is_missing():
    assert errors_of(validate_version(make_expository_body())) == []

    body = make_expository_body()
    del body.prompts[EXPOSITORY_FAMILY]["5_write"]
    del body.routes["EXPOSITORY:8_translate"]
    body.prompts[EXPOSITORY_FAMILY]["2_outline"] = '{"task": "no inputs"}'
    body.prompts[EXPOSITORY_FAMILY]["10_extra"] = "{}"

    messages = [finding.message for finding in errors_of(validate_version(body)) if finding.rule == "R-13"]

    assert any("expository.5_write is missing" in message for message in messages)
    assert any("EXPOSITORY:8_translate has no candidate" in message for message in messages)
    assert any("expository.2_outline: " in message for message in messages)
    assert any("expository.10_extra: unknown stage" in message for message in messages)
