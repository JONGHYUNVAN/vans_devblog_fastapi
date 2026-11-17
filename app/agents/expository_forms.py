"""설명문 파이프라인의 글 양식 (09 문서 D-104).

글 양식은 글의 목적(개념 설명·절차·분류·문제 원인)에 따라 뼈대가 어떤 region 으로 이루어지는지 적은 JSON 이다.
실제 값은 파이프라인 버전 문서의 `prompts.expository_form.{양식 id}` 에 있고, 저장소 `pipeline/expository/forms/` 는 넣는 입력이다.
뼈대의 겉모양(regions 배열과 슬롯 이름)은 모든 양식이 같다. 양식이 정하는 것은 역할의 순서·개수와 역할마다 말해야 하는 것이다.
그래서 뼈대 검사와 조립 코드는 양식마다 갈라지지 않는다.

양식의 필드: label·use_when·topic·thesis·focus_points(1단계가 양식을 고르고 주제·논제·본론 초점의 꼴을 맞추는 설명),
overview_header(전체 정리 표의 머리 두 칸), outline_rules·review_rules·write_rules(그 양식에만 붙는 규칙), regions.
region 의 필드: role, section(섹션 앵커 id. 여러 번 올 수 있으면 {section}-{n}), min·max(그 역할의 region 수. max null 은 상한 없음),
focus(본론이다: 1단계 focus_points 와 1:1 이고 전체 정리 표의 행이다), example(예시 슬롯을 쓴다),
sentences(슬롯 → [최소, 최대] 문장 수), outline(뼈대에 적을 것), write(섹션으로 쓰는 법).
"""

import json
import re
from typing import Any

from app.models.pipeline import EXPOSITORY_FORM_FAMILY, PromptSet

SLOTS = ("heading", "key_sentence", "elaboration", "example", "takeaway")  # 뼈대 region 의 글 슬롯. 모든 양식이 같다
INTRO_SECTION = "intro"  # 메타 설명이 이 섹션의 첫 문단에서 나온다(expository_assembly.first_paragraph)
_TEXT_KEYS = ("label", "use_when", "topic", "thesis", "focus_points")
_RULE_KEYS = ("outline_rules", "review_rules", "write_rules")
_REGION_TEXT_KEYS = ("role", "section", "outline", "write")
_SENTENCE_SLOTS = ("elaboration", "example")
_SECTION_ID = re.compile(r"^[a-z][a-z0-9]*$")
_RESERVED_SECTIONS = ("code", "glossary")  # code-N 은 코드 블록 앵커, glossary 는 조립이 붙이는 용어 정리 절이다


def parse_form(text: str) -> dict[str, Any]:
    """버전 문서의 양식 본문 → 양식. 모양이 틀리면 ValueError.

    첫 region 은 intro 섹션이고 마지막 region 은 맺음이다. 둘 다 한 번만 오고 본론이 아니다: 조립이 메타 설명을 intro 에서
    뽑고 전체 정리 표를 마지막 섹션에 붙이기 때문이다.
    """
    try:
        form = json.loads(text)
    except json.JSONDecodeError as exc:
        raise ValueError(f"양식이 올바른 JSON 이 아니다: {exc}") from exc
    if not isinstance(form, dict):
        raise ValueError("양식은 JSON 객체여야 한다")
    missing = [key for key in _TEXT_KEYS if not _is_text(form.get(key))]
    if missing:
        raise ValueError(f"양식에 {missing} 설명(문자열)이 없다")
    header = form.get("overview_header")
    if not (isinstance(header, list) and len(header) == 2 and all(_is_text(cell) for cell in header)):
        raise ValueError("overview_header 는 문자열 둘이어야 한다")
    for key in _RULE_KEYS:
        rules = form.get(key, [])
        if not isinstance(rules, list) or not all(_is_text(rule) for rule in rules):
            raise ValueError(f"{key} 는 문자열 목록이어야 한다")
    regions = form.get("regions")
    if not isinstance(regions, list) or len(regions) < 2:
        raise ValueError("regions 는 둘 이상의 객체 목록이어야 한다")
    for index, region in enumerate(regions):
        problem = _region_problem(region)
        if problem:
            raise ValueError(f"regions[{index}]: {problem}")
    for key in ("role", "section"):
        names = [region[key] for region in regions]
        if len(set(names)) != len(names):
            raise ValueError(f"regions 의 {key} 는 서로 달라야 한다")
    first, last = regions[0], regions[-1]
    if first["section"] != INTRO_SECTION or not _once(first) or first["focus"]:
        raise ValueError(
            f"첫 region 은 section 이 {INTRO_SECTION} 이고 한 번만 오는(min 1, max 1) 본론 아닌 region 이어야 한다"
        )
    if not _once(last) or last["focus"]:
        raise ValueError("마지막 region 은 한 번만 오는(min 1, max 1) 본론 아닌 region 이어야 한다")
    if not any(region["focus"] for region in regions):
        raise ValueError("본론 region(focus: true)이 하나 이상 있어야 한다")
    return form


def stage_forms(prompts: PromptSet) -> dict[str, dict[str, Any]]:
    """버전 문서의 prompts 에서 양식 id → 양식. 하나도 없거나 본문이 틀리면 ValueError(호출 전에 멈춘다)."""
    stored = prompts.get(EXPOSITORY_FORM_FAMILY, {})
    if not stored:
        raise ValueError("파이프라인 버전에 설명문 글 양식이 없다. `python -m cli.expository seed` 로 넣는다")
    forms: dict[str, dict[str, Any]] = {}
    for form_id, text in stored.items():
        try:
            forms[form_id] = parse_form(text)
        except ValueError as exc:
            raise ValueError(f"글 양식 {form_id}: {exc}") from exc
    return forms


def role_specs(form: dict[str, Any]) -> dict[str, dict[str, Any]]:
    """역할 이름 → 양식의 region 정의."""
    return {region["role"]: region for region in form["regions"]}


def focus_range(form: dict[str, Any]) -> tuple[int, int | None]:
    """본론 region 수의 (최소, 최대). 1단계의 focus_points 수가 이 안이어야 한다. 상한이 없으면 최대는 None."""
    focus = [region for region in form["regions"] if region["focus"]]
    low = sum(region["min"] for region in focus)
    high = None if any(region["max"] is None for region in focus) else sum(region["max"] for region in focus)
    return low, high


def repeatable_roles(form: dict[str, Any]) -> list[str]:
    """여러 번 올 수 있는 본론 역할. 뼈대 검토(3단계)가 빼고 채우는 대상이다. 없는 양식은 검토를 부르지 않는다."""
    return [region["role"] for region in form["regions"] if region["focus"] and region["max"] != 1]


def section_ids(form: dict[str, Any], regions: list[dict[str, Any]]) -> list[str]:
    """뼈대 regions 의 순서대로 섹션 앵커 id. 한 번만 올 수 있는 역할은 section 그대로, 아니면 {section}-{n} 이다.

    양식에 없는 역할이 있으면 KeyError(뼈대 검사가 먼저 막는다).
    """
    specs = role_specs(form)
    counts: dict[str, int] = {}
    ids: list[str] = []
    for region in regions:
        spec = specs[region["role"]]
        if spec["max"] == 1:
            ids.append(spec["section"])
        else:
            counts[spec["role"]] = counts.get(spec["role"], 0) + 1
            ids.append(f"{spec['section']}-{counts[spec['role']]}")
    return ids


def form_choices(forms: dict[str, dict[str, Any]]) -> list[dict[str, Any]]:
    """1단계 입력: 고를 수 있는 양식과, 고른 양식에서 주제·논제·본론 초점이 가져야 할 꼴."""
    choices: list[dict[str, Any]] = []
    for form_id, form in forms.items():
        low, high = focus_range(form)
        choices.append(
            {"id": form_id, **{key: form[key] for key in _TEXT_KEYS}, "focus_points_min": low, "focus_points_max": high}
        )
    return choices


def outline_view(form_id: str, form: dict[str, Any]) -> dict[str, Any]:
    """2단계 지시문의 form 자리: 역할의 순서·개수와 역할마다 뼈대에 적을 것."""
    return {
        "id": form_id,
        "regions": [
            {
                "role": region["role"],
                "min": region["min"],
                "max": region["max"],
                "focus": region["focus"],
                "example": region["example"],
                "sentences": region.get("sentences", {}),
                "guidance": region["outline"],
            }
            for region in form["regions"]
        ],
        "rules": form.get("outline_rules", []),
    }


def review_view(form_id: str, form: dict[str, Any]) -> dict[str, Any]:
    """3단계 지시문의 form 자리: 뼈대 단계의 것에 빼고 채울 수 있는 역할과 그 양식의 검토 기준을 더한다."""
    return {
        **outline_view(form_id, form),
        "repeatable_roles": repeatable_roles(form),
        "review_rules": form.get("review_rules", []),
    }


def write_view(form_id: str, form: dict[str, Any], regions: list[dict[str, Any]]) -> dict[str, Any]:
    """5단계 지시문의 form 자리: 섹션마다 앵커 id 와 쓰는 법. sections[i] 는 뼈대의 regions[i] 를 펼친 섹션이다."""
    specs = role_specs(form)
    return {
        "id": form_id,
        "sections": [
            {"section_id": section_id, "role": region["role"], "guidance": specs[region["role"]]["write"]}
            for section_id, region in zip(section_ids(form, regions), regions, strict=True)
        ],
        "rules": form.get("write_rules", []),
    }


def _region_problem(region: Any) -> str | None:
    """양식의 region 정의 하나가 틀린 점. 맞으면 None."""
    if not isinstance(region, dict) or not all(_is_text(region.get(key)) for key in _REGION_TEXT_KEYS):
        return f"{list(_REGION_TEXT_KEYS)} 가 문자열로 있어야 한다"
    if not _SECTION_ID.match(region["section"]) or region["section"] in _RESERVED_SECTIONS:
        return f"section {region['section']!r} 은 소문자·숫자 이름이어야 하고 {list(_RESERVED_SECTIONS)} 는 쓸 수 없다"
    low, high = region.get("min"), region.get("max", 0)
    if not (_is_count(low) and (high is None or (_is_count(high) and high >= max(low, 1)))):
        return "min 은 0 이상의 정수, max 는 min 이상(1 이상)의 정수 또는 null 이어야 한다"
    if not isinstance(region.get("focus"), bool) or not isinstance(region.get("example"), bool):
        return "focus 와 example 은 true·false 여야 한다"
    sentences = region.get("sentences", {})
    if not isinstance(sentences, dict) or not all(
        slot in _SENTENCE_SLOTS
        and isinstance(span, list)
        and len(span) == 2
        and all(_is_count(bound) for bound in span)
        and 1 <= span[0] <= span[1]
        for slot, span in sentences.items()
    ):
        return f"sentences 는 {list(_SENTENCE_SLOTS)} → [최소, 최대] 문장 수여야 한다"
    return None


def _once(region: dict[str, Any]) -> bool:
    return region["min"] == 1 and region["max"] == 1


def _is_count(value: Any) -> bool:
    return type(value) is int and value >= 0  # bool 은 int 의 하위 타입이라 type 으로 가린다


def _is_text(value: Any) -> bool:
    return isinstance(value, str) and bool(value.strip())
