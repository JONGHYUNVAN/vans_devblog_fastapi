"""설명문 파이프라인 단계 출력의 결정론적 검사 (pipeline/expository/README.md "단계 사이에 코드가 하는 일").

모델이 놓치기 쉬운 규칙만 본다. 모두 오류 문장 목록을 돌려주고 비면 통과다. 러너가 오류 문장을 그대로 모델에게 돌려주므로
작성·번역 모델이 읽는 영어로 쓴다(기존 게이트 ValidationGate 의 오류는 한국어 그대로 섞인다).
뼈대 검사는 글 양식(agents/expository_forms)의 역할 목록을 읽는 한 벌이다. 양식마다 다른 코드가 없다(09 문서 D-104).
"""

import re
from collections.abc import Iterator
from typing import Any

from app.agents.expository_forms import SLOTS, focus_range, repeatable_roles, role_specs
from app.models.pipeline import Parameters, StructureParameters
from app.models.sections import CODE_ANCHOR_PATTERN, find_anchors, mask_code_fences
from app.models.tiptap import parse_markdown
from app.services.validation import ValidationGate

MAX_TAKEAWAY_WORDS = 10
MAX_TITLE_CHARS = 40
MAX_SUMMARY_CHARS = 150
SUMMARY_ROWS = (2, 5)
VERDICTS = ("SUPPORTED", "REFUTED", "UNVERIFIABLE")
CLAIM_TYPES = ("API", "CONFIG_OPTION", "VERSION", "DB_SYNTAX", "PROTOCOL_OR_FIGURE")

# 지시문 낱말이 독자 문장으로 새는 것. 2026-11-11 첫 뼈대 테스트에서 "토이 프로젝트 수준의"·"설계 딜레마"가 본문에 들어갔다.
# 필드 이름은 밑줄이 든 것만 잡는다: elaboration·example·takeaway 는 보통 영어 낱말이다
LEAK_PATTERN = re.compile(
    r"toy[- ]projects?|undergraduate|dilemma|core_argument|core_message|key_sentence|focus_points|out_of_scope",
    re.IGNORECASE,
)
# 주제는 대상 하나다(사용자 결정 2026-11-11). 비교형 주제는 AI 슬롭으로 판정됐다. 어느 양식이든 본다
COMPARISON_PATTERN = re.compile(
    r"\bvs\.?(?=\s)|\bversus\b|\bdifferences? between\b|\bcompared (?:to|with)\b", re.IGNORECASE
)
_SENTENCE_BREAK = re.compile(r"[.!?][\"')\]]*\s+(?=\S)")
_ABBREVIATION = re.compile(r"(?:\be\.g|\bi\.e|\betc|\bvs|\bcf)\.$", re.IGNORECASE)
_HANGUL = re.compile(r"[가-힣]")
_TABLE_LINE = re.compile(r"^\s*\|")
_PATH_TOKEN = re.compile(r"[A-Za-z_]+|\d+")
_NO_LENGTH_LIMIT = 10**9  # 5단계 본문은 어절 수로만 조절한다. 글자 수 상한은 8단계 결과에만 건다
_REQUIRED_SLOTS = ("heading", "key_sentence", "elaboration")  # 어느 역할이든 채운다. example·takeaway 는 역할이 정한다

Form = dict[str, Any]


def count_sentences(text: str) -> int:
    """영어 문장 수. e.g.·i.e. 같은 약어 뒤의 마침표는 문장 끝으로 세지 않는다. 버전 번호(2.0)·메서드 호출(a.b())은 공백이 없어 끊기지 않는다."""
    stripped = text.strip()
    if not stripped:
        return 0
    breaks = [
        match for match in _SENTENCE_BREAK.finditer(stripped) if not _ABBREVIATION.search(stripped[: match.start() + 1])
    ]
    return len(breaks) + 1


def check_topic(
    output: dict[str, Any], categories: list[dict[str, Any]], forms: dict[str, Form], *, given: bool = False
) -> list[str]:
    """1단계. form 은 forms(고를 수 있는 양식)의 id 이고 focus_points 수는 그 양식의 본론 region 수 범위 안이다.

    categories 가 비었으면(Post BE 주소 없음) 카테고리 쌍은 보지 않는다. given 은 사람이 파일로 준 1단계 결과다:
    뼈대에 들어가는 필드와 양식만 요구하고 keywords·카테고리는 없어도 된다.
    """
    errors = _require_strings(output, ("topic", "core_argument"))
    errors += _require_string_list(output, "focus_points", 1)
    errors += _require_string_list(output, "out_of_scope", 1)
    form_id = output.get("form")
    if not isinstance(form_id, str) or form_id not in forms:
        errors.append(f"form must be one of {sorted(forms)}")
    elif isinstance(output.get("focus_points"), list):
        low, high = focus_range(forms[form_id])
        count = len(output["focus_points"])
        if count < low or (high is not None and count > high):
            errors.append(f"focus_points has {count} items; form '{form_id}' takes {_span_text(low, high)}")
    if given:
        return errors
    errors += _require_strings(output, ("main_category_id", "sub_category_id"))
    keywords = output.get("keywords")
    if not (isinstance(keywords, list) and len(keywords) == 3 and all(_is_text(keyword) for keyword in keywords)):
        errors.append("keywords must be exactly 3 non-empty strings")
    if _is_text(output.get("topic")) and COMPARISON_PATTERN.search(output["topic"]):
        errors.append("topic is framed as a comparison of two options. Reframe it as one subject the article explains")
    if categories:
        pairs = {(category["main_category_id"], category["sub_category_id"]) for category in categories}
        if (output.get("main_category_id"), output.get("sub_category_id")) not in pairs:
            errors.append("(main_category_id, sub_category_id) must be one pair from inputs.categories")
    return errors + _leaks(output)


def check_outline(output: dict[str, Any], inputs: dict[str, Any], form_id: str, form: Form) -> list[str]:
    """2단계. 논제 그대로 복사, 역할의 순서·개수가 양식과 같다, 본론 region 과 focus_points 1:1, region 문장 규칙."""
    errors = _check_outline_shape(output, form_id, form)
    if errors:
        return errors
    if output["core_message"] != inputs["core_argument"]:
        errors.append("core_message must be an exact, unmodified copy of inputs.core_argument")
    regions = output["regions"]
    specs = role_specs(form)
    focus = [region for region in regions if specs[region["role"]]["focus"]]
    if len(focus) != len(inputs["focus_points"]):
        errors.append(
            f"the outline has {len(focus)} focus regions; write exactly one per item of inputs.focus_points "
            f"({len(inputs['focus_points'])})"
        )
    errors += _check_region_rules(regions, form)
    return errors + _leaks({key: value for key, value in output.items() if key != "core_message"})


def check_review(output: dict[str, Any], inputs: dict[str, Any], form_id: str, form: Form) -> list[str]:
    """3단계. 여러 번 올 수 있는 본론 역할의 region 만 빼고 채울 수 있다.

    남긴 region 은 글자까지 그대로, 뺀 것·더한 것은 기록, 그 역할의 최소 개수보다 적게 남았을 때만 채운다.
    """
    outline, log = output.get("outline"), output.get("review_log")
    if not isinstance(outline, dict) or not isinstance(log, list):
        return ["output must have outline (object) and review_log (array)"]
    errors = _check_outline_shape(outline, form_id, form)
    if errors:
        return errors
    source = inputs["outline"]
    repeatable = set(repeatable_roles(form))
    source_regions, regions = source["regions"], outline["regions"]
    fixed = [region for region in regions if region["role"] not in repeatable]
    if outline["core_message"] != source["core_message"] or fixed != [
        region for region in source_regions if region["role"] not in repeatable
    ]:
        errors.append(
            f"core_message or a region outside {sorted(repeatable)} changed; copy them exactly as given, in the same order"
        )
    source_headings = {region["heading"] for region in source_regions if region["role"] in repeatable}
    added = {entry.get("heading") for entry in log if isinstance(entry, dict) and entry.get("action") == "added"}
    removed = {entry.get("heading") for entry in log if isinstance(entry, dict) and entry.get("action") == "removed"}
    new = [region for region in regions if region["role"] in repeatable and region not in source_regions]
    for region in new:
        if region["heading"] in source_headings:
            errors.append(f"kept region '{region['heading']}' was rewritten; copy kept regions exactly as given")
        elif region["heading"] not in added:
            errors.append(f"new region '{region['heading']}' is not logged with action 'added'")
    output_headings = {region["heading"] for region in regions}
    for region in source_regions:
        if region["role"] in repeatable and region["heading"] not in output_headings | removed:
            errors.append(f"region '{region['heading']}' was removed but is not logged with action 'removed'")
    specs = role_specs(form)
    for role in sorted(repeatable):
        kept = [region for region in regions if region["role"] == role and region in source_regions]
        if len(kept) >= specs[role]["min"] and any(region["role"] == role for region in new):
            errors.append(
                f"'{role}' regions were added although {specs[role]['min']} or more were kept; "
                f"add regions only when fewer than {specs[role]['min']} remain"
            )
    errors += _check_region_rules(new, form)
    return errors + _leaks({"regions": new})


def check_outline_fact_check(output: dict[str, Any], inputs: dict[str, Any], form_id: str, form: Form) -> list[str]:
    """4단계. 바뀐 필드는 corrected_text 가 있는 REFUTED 주장이 가리키는 곳뿐이어야 한다."""
    outline, claims = output.get("outline"), output.get("claims")
    if not isinstance(outline, dict) or not isinstance(claims, list):
        return ["output must have outline (object) and claims (array)"]
    errors = _check_outline_shape(outline, form_id, form) + _check_claims(claims, ("location",))
    if errors:
        return errors
    source = dict(_leaves(inputs["outline"]))
    revised = dict(_leaves(outline))
    if source.keys() != revised.keys():
        return [
            "the outline's keys or list lengths changed; keep its structure exactly and edit only refuted sentences"
        ]
    corrected = {
        tuple(_PATH_TOKEN.findall(claim["location"]))
        for claim in claims
        if claim.get("verdict") == "REFUTED" and _is_text(claim.get("corrected_text"))
    }
    changed = [path for path in source if source[path] != revised[path]]
    errors += [
        f"{_format_path(path)} changed, but no REFUTED claim with corrected_text has that location; "
        "change only the sentences of refuted claims"
        for path in changed
        if path not in corrected
    ]
    touched = sorted({int(path[1]) for path in changed if path[0] == "regions"})
    errors += _check_region_rules([outline["regions"][index] for index in touched], form)
    return errors


def blocking_claims(output: dict[str, Any]) -> list[dict[str, Any]]:
    """4단계에서 region 의 핵심 문장이나 논제를 바꾸지 않고는 고칠 수 없다고 표시된 주장. 있으면 뼈대부터 다시 만든다."""
    return [claim for claim in output.get("claims", []) if isinstance(claim, dict) and claim.get("blocking") is True]


def check_article(output: dict[str, Any], sections: list[str]) -> list[str]:
    """5단계. 섹션 앵커가 sections(뼈대와 양식에서 나온 순서) 그대로, 기존 게이트의 코드 블록·변환·코드 주석 검사, 표·지시문 낱말 없음."""
    errors = _require_strings(output, ("title", "content_markdown"))
    if errors:
        return errors
    markdown = output["content_markdown"]
    anchors = [anchor for anchor in find_anchors(markdown) if not CODE_ANCHOR_PATTERN.match(anchor)]
    if anchors != sections:
        errors.append(f"section anchors are {anchors}; they must be exactly {sections} in this order")
    gate = ValidationGate(
        min_chars=1, max_chars=_NO_LENGTH_LIMIT, structure=StructureParameters(required_sections=sections)
    )
    # check_style 의 어미 검사는 한국어 어미만 가린다. 본문을 한국어로 쓰는 지시문(09 문서 D-105)에서는 여기서도 걸린다
    errors += gate.check_markdown(markdown, None) + gate.check_style(markdown)
    prose = mask_code_fences(markdown)
    if any(_TABLE_LINE.match(line) for line in prose.split("\n")):
        errors.append("the article contains a table; leave tables out, a later step adds them")
    leaked = sorted({match.group(0) for match in LEAK_PATTERN.finditer(prose)})
    if leaked:
        errors.append(f"the article contains wording from the instructions {leaked}; write for the reader instead")
    return errors


def check_article_fact_check(output: dict[str, Any]) -> list[str]:
    """6단계. 패치 적용 가능 여부는 조립 단계가 섹션별로 본다. 여기서는 모양만."""
    claims, patches = output.get("claims"), output.get("patches")
    if not isinstance(claims, list) or not isinstance(patches, list):
        return ["output must have claims (array) and patches (array)"]
    errors = _check_claims(claims, ("section_id",))
    for index, patch in enumerate(patches):
        if not isinstance(patch, dict) or _require_strings(patch, ("section_id", "before", "reason")):
            errors.append(f"patches[{index}] needs non-empty section_id, before, and reason")
        elif not isinstance(patch.get("after"), str):
            errors.append(f"patches[{index}].after must be a string")
    return errors


def check_summary_glossary(output: dict[str, Any], section_ids: list[str]) -> list[str]:
    """7단계. 요약 표는 section_ids 순서 그대로 하나씩, 용어는 번역이 따를 한국어 표기가 있어야 한다."""
    summaries, terms = output.get("section_summaries"), output.get("terms")
    if not isinstance(summaries, list) or not isinstance(terms, list):
        return ["output must have section_summaries (array) and terms (array)"]
    errors: list[str] = []
    ids = [summary.get("section_id") if isinstance(summary, dict) else None for summary in summaries]
    if ids != section_ids:
        errors.append(f"section_summaries ids are {ids}; they must be exactly {section_ids} in this order")
    low, high = SUMMARY_ROWS
    for summary in summaries:
        if not isinstance(summary, dict):
            continue
        rows = summary.get("rows")
        if not _is_text(summary.get("title")) or not isinstance(rows, list) or not low <= len(rows) <= high:
            errors.append(f"summary '{summary.get('section_id')}' needs a title and {low} to {high} rows")
        elif not all(isinstance(row, dict) and not _require_strings(row, ("item", "explanation")) for row in rows):
            errors.append(f"summary '{summary.get('section_id')}' has a row without item or explanation")
    for index, term in enumerate(terms):
        if not isinstance(term, dict) or _require_strings(term, ("term_en", "term_ko")):
            errors.append(f"terms[{index}] needs non-empty term_en and term_ko")
        elif not isinstance(term.get("keep_english"), bool) or not isinstance(term.get("definition"), str):
            errors.append(f"terms[{index}] needs keep_english (boolean) and definition (string)")
        elif not term["keep_english"] and not _HANGUL.search(term["term_ko"]):
            errors.append(
                f"terms[{index}].term_ko '{term['term_ko']}' has no Korean; set keep_english or give the Korean term"
            )
    return errors


def check_translation(output: dict[str, Any], english_markdown: str, parameters: Parameters) -> list[str]:
    """8단계. 앵커·코드 블록은 영어본과 같고, 기존 게이트의 길이·변환·합쇼체·명령형·코드 주석 검사를 통과해야 한다."""
    errors = _require_strings(output, ("title", "content_markdown"))
    if errors:
        return errors
    markdown, title = output["content_markdown"], output["title"]
    if len(title) > MAX_TITLE_CHARS:
        errors.append(f"title has {len(title)} characters; keep it within {MAX_TITLE_CHARS}")
    if any(mark in title for mark in "():"):
        errors.append("title must not contain parentheses or a colon")
    source_anchors = find_anchors(english_markdown)
    if find_anchors(markdown) != source_anchors:
        errors.append(f"anchors changed; keep exactly {source_anchors} in this order")
    source_fences, fences = _fence_line_counts(english_markdown), _fence_line_counts(markdown)
    if source_fences != fences:
        errors.append(
            f"code blocks changed (line counts {fences}, expected {source_fences}); translate only comments, line for line"
        )
    sections = [anchor for anchor in source_anchors if not CODE_ANCHOR_PATTERN.match(anchor)]
    gate = ValidationGate(
        min_chars=parameters.draft_min_chars,
        max_chars=parameters.draft_reject_chars,
        min_code_blocks=parameters.min_code_blocks,
        structure=StructureParameters(required_sections=sections, prose_register="polite"),  # 블로그 글은 합쇼체(D-86)
        style=parameters.style,
        code_comments=parameters.code_comments,
    )
    return errors + gate.check_markdown(markdown, None) + gate.check_style(markdown)


def check_summary(output: dict[str, Any]) -> list[str]:
    """9단계. 메타 설명 150자 이하, "이 글은"으로 시작하지 않는다."""
    summary = output.get("summary")
    if not _is_text(summary):
        return ["summary must be a non-empty string"]
    errors: list[str] = []
    if len(summary) > MAX_SUMMARY_CHARS:
        errors.append(f"summary has {len(summary)} characters; keep it within {MAX_SUMMARY_CHARS}")
    if summary.lstrip().startswith("이 글"):
        errors.append("summary must not open with '이 글은' or '이 글에서는'")
    return errors


def _check_outline_shape(outline: Any, form_id: str, form: Form) -> list[str]:
    """뼈대의 겉모양: 양식 id, 논제, 슬롯이 모두 문자열인 region 목록, 양식과 같은 역할 순서·개수."""
    if not isinstance(outline, dict) or not _is_text(outline.get("core_message")):
        return ["outline needs core_message (non-empty string)"]
    if outline.get("form") != form_id:
        return [f"outline.form must be '{form_id}'"]
    specs = role_specs(form)
    regions = outline.get("regions")
    if not isinstance(regions, list) or not all(
        isinstance(region, dict)
        and region.get("role") in specs
        and all(isinstance(region.get(slot), str) for slot in SLOTS)
        for region in regions
    ):
        return [
            f"regions must be an array of objects with role (one of {list(specs)}) and string values for {list(SLOTS)}"
        ]
    # 양식의 역할을 차례로 훑으며 뼈대의 region 을 소비한다. 개수가 범위를 벗어나거나 남는 region 이 있으면 순서가 틀린 것이다
    roles = [region["role"] for region in regions]
    position, in_range = 0, True
    for spec in form["regions"]:
        count = 0
        while position < len(roles) and roles[position] == spec["role"]:
            position, count = position + 1, count + 1
        in_range &= count >= spec["min"] and (spec["max"] is None or count <= spec["max"])
    if not in_range or position < len(roles):
        expected = ", ".join(f"{spec['role']} ({_span_text(spec['min'], spec['max'])})" for spec in form["regions"])
        return [f"region roles are {roles}; they must come in this order and number: {expected}"]
    return []


def _check_region_rules(regions: list[dict[str, Any]], form: Form) -> list[str]:
    """region 하나의 규칙: 슬롯 채움(예시·정리 구절은 역할이 정한다), key_sentence 한 문장, 양식이 적은 문장 수, takeaway 10단어 이하."""
    specs = role_specs(form)
    errors: list[str] = []
    for region in regions:
        spec = specs[region["role"]]
        name = region["heading"].strip() or region["role"]
        errors += [f"{slot} of '{name}' must not be empty" for slot in _REQUIRED_SLOTS if not region[slot].strip()]
        for slot, used in (("example", spec["example"]), ("takeaway", spec["focus"])):
            if used and not region[slot].strip():
                errors.append(f"{slot} of '{name}' must not be empty")
            elif not used and region[slot].strip():
                errors.append(f"{slot} of '{name}' must be an empty string; role '{region['role']}' does not use it")
        spans = {"key_sentence": [1, 1], **spec.get("sentences", {})}
        for slot, (low, high) in spans.items():
            sentences = count_sentences(region[slot])
            if sentences and not low <= sentences <= high:
                errors.append(f"{slot} of '{name}' has {sentences} sentences; write {_span_text(low, high)}")
        words = len(region["takeaway"].split())
        if words > MAX_TAKEAWAY_WORDS:
            errors.append(f"takeaway of '{name}' has {words} words; keep it within {MAX_TAKEAWAY_WORDS}")
    return errors


def _span_text(low: int, high: int | None) -> str:
    if high is None:
        return f"{low} or more"
    return f"exactly {low}" if low == high else f"{low} to {high}"


def _check_claims(claims: list[Any], location_keys: tuple[str, ...]) -> list[str]:
    errors: list[str] = []
    for index, claim in enumerate(claims):
        if not isinstance(claim, dict) or _require_strings(claim, (*location_keys, "claim")):
            errors.append(f"claims[{index}] needs non-empty {[*location_keys, 'claim']}")
        elif claim.get("verdict") not in VERDICTS or claim.get("claim_type") not in CLAIM_TYPES:
            errors.append(f"claims[{index}] needs verdict in {list(VERDICTS)} and claim_type in {list(CLAIM_TYPES)}")
    return errors


def _leaks(value: Any) -> list[str]:
    return [
        f"{_format_path(path)} contains wording from the instructions ('{match.group(0)}'); write for the reader instead"
        for path, text in _leaves(value)
        if isinstance(text, str)
        for match in [LEAK_PATTERN.search(text)]
        if match
    ]


def _leaves(value: Any, path: tuple[str, ...] = ()) -> Iterator[tuple[tuple[str, ...], Any]]:
    """중첩 dict·list 의 끝값을 (경로, 값) 으로. 경로는 키와 0부터 센 목록 번호의 문자열이다."""
    if isinstance(value, dict):
        for key, item in value.items():
            yield from _leaves(item, (*path, str(key)))
    elif isinstance(value, list):
        for index, item in enumerate(value):
            yield from _leaves(item, (*path, str(index)))
    else:
        yield path, value


def _format_path(path: tuple[str, ...]) -> str:
    return "".join(f"[{part}]" if part.isdigit() else f".{part}" for part in path).lstrip(".")


def _fence_line_counts(markdown: str) -> list[int]:
    return [token.content.count("\n") for token in parse_markdown(markdown) if token.type == "fence"]


def _require_strings(value: dict[str, Any], keys: tuple[str, ...]) -> list[str]:
    return [f"{key} must be a non-empty string" for key in keys if not _is_text(value.get(key))]


def _require_string_list(value: dict[str, Any], key: str, minimum: int) -> list[str]:
    items = value.get(key)
    if isinstance(items, list) and len(items) >= minimum and all(_is_text(item) for item in items):
        return []
    return [f"{key} must be an array of at least {minimum} non-empty strings"]


def _is_text(value: Any) -> bool:
    return isinstance(value, str) and bool(value.strip())
