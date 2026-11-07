"""파이프라인 버전 문서의 검증 (설계 09 문서 4절). 순수 함수. 시드(bootstrap·import·edit-*) 때 돌고 오류가 하나라도 있으면 쓰지 않는다.

1a 는 단계 목록이 없으므로 R-04(라우트)·R-05(템플릿)·R-08(템플릿 내용)·R-09(조절 값)·R-10(슬롯·페르소나)·R-11(해시)만 본다.
지금의 validate_snapshot(끝까지 고를 수 있는지)·validate_prompts(가족마다 템플릿이 있는지)가 각각 R-04·R-05 다.
"""

import re
from dataclasses import dataclass
from typing import Literal

from app.agents.prompts.registry import (
    COMMON_FAMILY,
    PROMPT_FAMILIES,
    TEMPLATE_NAMES,
    MissingTemplateError,
    prompt_family,
    resolve_template,
    templates_for_route,
)
from app.models.enums import AgentSlot
from app.models.pipeline import PipelineVersionBody, content_hash_of
from app.models.routing import build_snapshot, validate_snapshot

Level = Literal["error", "warning"]

# R-08 의 기본 필수 구절 (D-62 가 보강한 규칙. 데이터의 prompt_lint 를 더해 합친다, Q-38). 가족 전용 템플릿에만 적용한다
DEFAULT_PROMPT_LINT: dict[str, list[str]] = {
    "write_draft": ["60", "max_chars", "40%", "~다", "합니다", "마치며", "transliteration", "스테이트리스"],
    "revise_draft": ["~다", "합니다"],
    "propose_topic": ["transliteration", "스테이트리스", "stateless"],
}
# 작성·수정·제안을 맡는 가족. common·haiku 의 스텁·변환 템플릿에는 적용하지 않는다
_LINT_FAMILIES = ("claude", "gemini", "grok")
# 검색 호출의 템플릿은 글로 답을 받는다(json_schema 없음). 구조화 출력 호출만 "JSON" 을 요구한다
_SEARCH_TEMPLATES = ("fact_check", "arbitrate_search")
_PLACEHOLDER = re.compile(r"\$\{?([A-Za-z_][A-Za-z0-9_]*)\}?")


@dataclass(frozen=True)
class Finding:
    """규칙 하나의 발견. error 는 시드를 막고 warning 은 찍기만 한다."""

    rule: str
    level: Level
    message: str

    def __str__(self) -> str:
        return f"[{self.rule}] {self.level}: {self.message}"


def validate_version(body: PipelineVersionBody, *, expected_hash: str | None = None) -> list[Finding]:
    """규칙별 발견 목록. 비어 있으면 통과다. CLI 는 error 가 있으면 종료 코드 1 이다."""
    findings: list[Finding] = []
    findings += _r10_slots_and_personas(body)
    findings += _r04_routes(body)
    findings += _r05_templates(body)
    findings += _r08_template_contents(body)
    findings += _r09_parameters(body)
    if expected_hash is not None and expected_hash != content_hash_of(body):
        findings.append(Finding("R-11", "error", f"content_hash {expected_hash} does not match the body"))
    return findings


def errors_of(findings: list[Finding]) -> list[Finding]:
    """오류만 거른다. 비어 있으면 그 본문은 저장해도 된다."""
    return [finding for finding in findings if finding.level == "error"]


def _r10_slots_and_personas(body: PipelineVersionBody) -> list[Finding]:
    """세 슬롯이 서로 다른 페르소나를 가리키고, 그 페르소나들의 주 렌즈가 모두 다르다(D-19). 렌즈가 겹치면 리뷰어 배정이 안 된다."""
    findings: list[Finding] = []
    missing = [slot.value for slot in AgentSlot if slot not in body.slots]
    if missing:
        findings.append(Finding("R-10", "error", f"slots must name a persona for every slot; missing {missing}"))
    for slot, persona_id in body.slots.items():
        if persona_id not in body.personas:
            findings.append(Finding("R-10", "error", f"slot {slot.value} names unknown persona {persona_id}"))
    chosen = [body.slots[slot] for slot in AgentSlot if slot in body.slots]
    if len(set(chosen)) != len(chosen):
        findings.append(Finding("R-10", "error", "every slot needs a distinct persona"))
    lenses = [body.personas[persona_id].primary_lens for persona_id in chosen if persona_id in body.personas]
    if len(set(lenses)) != len(lenses):
        findings.append(Finding("R-10", "error", "the three personas must have distinct primary lenses (D-19)"))
    return findings


def _r04_routes(body: PipelineVersionBody) -> list[Finding]:
    """세 벤더가 다 있을 때 끝까지 고를 수 있는지. 지금 RoundService.seed_routes 의 검사와 같다."""
    declared = {candidate.provider for candidates in body.routes.values() for candidate in candidates}
    try:
        snapshot = build_snapshot(body.route_documents(), declared)
        validate_snapshot(snapshot)
    except (ValueError, KeyError) as exc:
        return [Finding("R-04", "error", str(exc))]
    return []


def _r05_templates(body: PipelineVersionBody) -> list[Finding]:
    """단계가 가리키는 템플릿이 선택기가 고를 수 있는 모든 후보의 가족에 있다. 어느 라우트도 쓰지 않는 템플릿은 경고."""
    findings: list[Finding] = []
    needed: set[tuple[str, str]] = set()
    for route_id, candidates in body.routes.items():
        for name in templates_for_route(route_id):
            for candidate in candidates:
                family = prompt_family(candidate)
                needed.add((family, name))
                try:
                    resolve_template(body.prompts, name, family)
                except MissingTemplateError:
                    findings.append(
                        Finding(
                            "R-05",
                            "error",
                            f"{route_id}: template {name} for {family} ({candidate.model_id}) is missing",
                        )
                    )
    for family, templates in body.prompts.items():
        if family not in (*PROMPT_FAMILIES, COMMON_FAMILY):
            findings.append(Finding("R-08", "error", f"unknown prompt family {family}"))
        for name in templates:
            if name not in TEMPLATE_NAMES:
                findings.append(Finding("R-08", "error", f"prompts.{family}.{name}: unknown template name"))
            elif family != COMMON_FAMILY and (family, name) not in needed:
                findings.append(Finding("R-05", "warning", f"prompts.{family}.{name} is used by no route"))
    return findings


def _r08_template_contents(body: PipelineVersionBody) -> list[Finding]:
    """템플릿 본문 검사: JSON 요구(검색 템플릿 제외), 치환되지 않는 $자리표시자, propose_topic 의 $writer_bias, 필수 구절(경고)."""
    findings: list[Finding] = []
    # 코드 기본 구절에 데이터의 prompt_lint 를 더한다(Q-38). 데이터가 기본을 지울 수는 없다: 지우고 싶은 구절은 코드에서 뺀다
    lint = {name: list(phrases) for name, phrases in DEFAULT_PROMPT_LINT.items()}
    for name, phrases in body.parameters.prompt_lint.items():
        lint[name] = lint.get(name, []) + [phrase for phrase in phrases if phrase not in lint.get(name, [])]
    for family, templates in body.prompts.items():
        for name, text in templates.items():
            label = f"prompts.{family}.{name}"
            if name not in _SEARCH_TEMPLATES and "JSON" not in text:
                findings.append(Finding("R-08", "error", f"{label}: does not ask for JSON"))
            for match in _PLACEHOLDER.finditer(text):
                placeholder = match.group(1)
                if placeholder == "writer_bias" and name == "propose_topic":
                    continue
                findings.append(
                    Finding("R-08", "error", f"{label}: stray placeholder ${placeholder} at offset {match.start()}")
                )
            if name == "propose_topic" and "$writer_bias" not in text:
                findings.append(Finding("R-08", "error", f"{label}: propose_topic must substitute $writer_bias"))
            if family in _LINT_FAMILIES:
                for phrase in lint.get(name, []):
                    if phrase not in text:
                        findings.append(Finding("R-08", "warning", f"{label}: expected phrase {phrase!r} is missing"))
    return findings


def _r09_parameters(body: PipelineVersionBody) -> list[Finding]:
    """조절 값의 범위. pydantic 은 타입만 보므로 상한·하한·서로 간의 관계(min < max, 선택 섹션 ⊂ 필수 섹션, 정규식 컴파일)는 여기서 본다."""
    findings: list[Finding] = []
    parameters = body.parameters

    def require(condition: bool, message: str) -> None:
        if not condition:
            findings.append(Finding("R-09", "error", message))

    require(parameters.draft_min_chars < parameters.draft_max_chars, "draft_min_chars must be below draft_max_chars")
    require(parameters.min_code_blocks >= 0, "min_code_blocks must be 0 or more")
    for name in (
        "max_iterations",
        "max_validation_retries",
        "max_llm_retries",
        "max_publish_attempts",
        "max_topic_attempts",
    ):
        require(getattr(parameters, name) >= 1, f"parameters.{name} must be at least 1")
    require(parameters.draft_token_limit > 0, "draft_token_limit must be positive")
    for name in ("topic_keyword_overlap", "topic_title_overlap"):
        require(0.0 <= getattr(parameters, name) <= 1.0, f"parameters.{name} must be between 0 and 1")
    require(parameters.topic_avoid_lookback_days >= 0, "topic_avoid_lookback_days must be 0 or more")
    for section in ("intro", "body-1"):
        require(
            section in parameters.structure.required_sections, f"structure.required_sections must include {section}"
        )
    for level, sections in parameters.structure.sections_optional_by_level.items():
        require(level.isdigit(), f"structure.sections_optional_by_level key {level!r} must be a level number")
        for section in sections:
            require(
                section in parameters.structure.required_sections,
                f"structure.sections_optional_by_level[{level}] names {section} which is not a required section",
            )
    for pattern, _ in parameters.polish.phrase_fixes:
        try:
            re.compile(pattern)
        except re.error as exc:
            findings.append(
                Finding("R-09", "error", f"polish.phrase_fixes pattern {pattern!r} does not compile: {exc}")
            )
    require(parameters.polish.length_slack_chars >= 0, "polish.length_slack_chars must be 0 or more")
    require(0.0 <= parameters.polish.length_slack_ratio <= 1.0, "polish.length_slack_ratio must be between 0 and 1")
    require(parameters.publish.note.max_rejected_shown >= 0, "publish.note.max_rejected_shown must be 0 or more")
    glossary = parameters.glossary
    require(0 < glossary.min_terms <= glossary.max_terms, "glossary.min_terms must be positive and at most max_terms")
    require(glossary.full_list_max >= 0 and glossary.surface_max_chars > 0, "glossary limits must be positive")
    return findings
