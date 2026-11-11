"""설명문 파이프라인 단계 출력의 결정론적 검사 (pipeline/expository/README.md "단계 사이에 코드가 할 일").

모델이 놓치기 쉬운 규칙만 본다. 모두 오류 문장 목록을 돌려주고 비면 통과다. 러너가 오류 문장을 그대로 모델에게 돌려주므로
작성·번역 모델이 읽는 영어로 쓴다(기존 게이트 ValidationGate 의 오류는 한국어 그대로 섞인다).
"""

import re
from collections.abc import Iterator
from typing import Any

from app.models.pipeline import Parameters, StructureParameters
from app.models.sections import CODE_ANCHOR_PATTERN, find_anchors, mask_code_fences
from app.models.tiptap import parse_markdown
from app.services.validation import ValidationGate

MIN_SECTIONS = 3
MAX_TAKEAWAY_WORDS = 10
MAX_TITLE_CHARS = 40
MAX_SUMMARY_CHARS = 150
SUMMARY_ROWS = (2, 5)
VERDICTS = ("SUPPORTED", "REFUTED", "UNVERIFIABLE")
CLAIM_TYPES = ("API", "CONFIG_OPTION", "VERSION", "DB_SYNTAX", "PROTOCOL_OR_FIGURE")

# 지시문 낱말이 독자 문장으로 새는 것. 2026-11-11 첫 뼈대 테스트에서 "토이 프로젝트 수준의"·"설계 딜레마"가 본문에 들어갔다
LEAK_PATTERN = re.compile(
    r"toy[- ]projects?|undergraduate|dilemma|problem_statement|core_argument|core_message|"
    r"mechanism_explanation|deep_dive|focus_mechanisms|key_takeaway",
    re.IGNORECASE,
)
# 설명문만 쓴다(사용자 결정 2026-11-11). 비교형 주제는 AI 슬롭으로 판정됐다
COMPARISON_PATTERN = re.compile(
    r"\bvs\.?(?=\s)|\bversus\b|\bdifferences? between\b|\bcompared (?:to|with)\b", re.IGNORECASE
)
_SENTENCE_BREAK = re.compile(r"[.!?][\"')\]]*\s+(?=\S)")
_ABBREVIATION = re.compile(r"(?:\be\.g|\bi\.e|\betc|\bvs|\bcf)\.$", re.IGNORECASE)
_HANGUL = re.compile(r"[가-힣]")
_TABLE_LINE = re.compile(r"^\s*\|")
_PATH_TOKEN = re.compile(r"[A-Za-z_]+|\d+")
_NO_LENGTH_LIMIT = 10**9  # 영어 본문은 단어 수로만 조절한다. 글자 수 상한은 번역본에만 건다
_INTRO_KEYS = ("problem_statement", "concept_definition")
_SECTION_KEYS = ("sub_topic", "principle", "mechanism_explanation", "key_takeaway")
_APPLICATION_KEYS = ("architecture_pattern", "exception_case")


def article_section_ids(section_count: int) -> list[str]:
    """작성 단계가 써야 하는 섹션 앵커 순서. 뼈대의 섹션 수만큼 mech-N 이 있다."""
    return [
        "intro",
        "concept",
        *[f"mech-{index}" for index in range(1, section_count + 1)],
        "application",
        "exceptions",
        "conclusion",
    ]


def count_sentences(text: str) -> int:
    """영어 문장 수. e.g.·i.e. 같은 약어 뒤의 마침표는 문장 끝으로 세지 않는다. 버전 번호(2.0)·메서드 호출(a.b())은 공백이 없어 끊기지 않는다."""
    stripped = text.strip()
    if not stripped:
        return 0
    breaks = [
        match for match in _SENTENCE_BREAK.finditer(stripped) if not _ABBREVIATION.search(stripped[: match.start() + 1])
    ]
    return len(breaks) + 1


def check_topic(output: dict[str, Any], categories: list[dict[str, Any]], *, given: bool = False) -> list[str]:
    """1단계. categories 가 비었으면(Post BE 주소 없음) 카테고리 쌍은 보지 않는다.

    given 은 사람이 파일로 준 1단계 결과다. 뼈대에 들어가는 네 필드만 요구하고 keywords·카테고리는 없어도 된다.
    """
    errors = _require_strings(output, ("topic", "core_argument"))
    errors += _require_string_list(output, "focus_mechanisms", MIN_SECTIONS)
    errors += _require_string_list(output, "out_of_scope", 1)
    if given:
        return errors
    errors += _require_strings(output, ("main_category_id", "sub_category_id"))
    keywords = output.get("keywords")
    if not (isinstance(keywords, list) and len(keywords) == 3 and all(_is_text(keyword) for keyword in keywords)):
        errors.append("keywords must be exactly 3 non-empty strings")
    if _is_text(output.get("topic")) and COMPARISON_PATTERN.search(output["topic"]):
        errors.append(
            "topic is framed as a comparison of two options. Reframe it as one behavior or failure the article explains"
        )
    if categories:
        pairs = {(category["main_category_id"], category["sub_category_id"]) for category in categories}
        if (output.get("main_category_id"), output.get("sub_category_id")) not in pairs:
            errors.append("(main_category_id, sub_category_id) must be one pair from inputs.categories")
    return errors + _leaks(output)


def check_outline(output: dict[str, Any], inputs: dict[str, Any]) -> list[str]:
    """2단계. 논제 그대로 복사, focus_mechanisms 와 섹션 1:1, 섹션 문장 규칙."""
    errors = _check_outline_shape(output)
    if errors:
        return errors
    if output["core_message"] != inputs["core_argument"]:
        errors.append("core_message must be an exact, unmodified copy of inputs.core_argument")
    sections = output["deep_dive_sections"]
    if len(sections) != len(inputs["focus_mechanisms"]):
        errors.append(
            f"deep_dive_sections has {len(sections)} items; write exactly one per item of inputs.focus_mechanisms "
            f"({len(inputs['focus_mechanisms'])})"
        )
    errors += _check_section_count(sections)
    errors += _check_section_rules(sections)
    return errors + _leaks({key: value for key, value in output.items() if key != "core_message"})


def check_review(output: dict[str, Any], inputs: dict[str, Any]) -> list[str]:
    """3단계. 남긴 섹션은 글자까지 그대로, 뺀 것·더한 것은 기록, 3개 미만일 때만 채운다."""
    outline, log = output.get("outline"), output.get("review_log")
    if not isinstance(outline, dict) or not isinstance(log, list):
        return ["output must have outline (object) and review_log (array)"]
    errors = _check_outline_shape(outline)
    if errors:
        return errors
    source = inputs["outline"]
    if _without_sections(outline) != _without_sections(source):
        errors.append("fields outside deep_dive_sections changed; copy them exactly as given")
    source_sections = source["deep_dive_sections"]
    sections = outline["deep_dive_sections"]
    source_topics = {section["sub_topic"] for section in source_sections}
    kept = [section for section in sections if section in source_sections]
    new = [section for section in sections if section not in source_sections]
    added = {entry.get("sub_topic") for entry in log if isinstance(entry, dict) and entry.get("action") == "added"}
    removed = {entry.get("sub_topic") for entry in log if isinstance(entry, dict) and entry.get("action") == "removed"}
    for section in new:
        if section["sub_topic"] in source_topics:
            errors.append(f"kept section '{section['sub_topic']}' was rewritten; copy kept sections exactly as given")
        elif section["sub_topic"] not in added:
            errors.append(f"new section '{section['sub_topic']}' is not logged with action 'added'")
    output_topics = {section["sub_topic"] for section in sections}
    for section in source_sections:
        if section["sub_topic"] not in output_topics and section["sub_topic"] not in removed:
            errors.append(f"section '{section['sub_topic']}' was removed but is not logged with action 'removed'")
    if new and len(kept) >= MIN_SECTIONS:
        errors.append("sections were added although 3 or more were kept; add sections only when fewer than 3 remain")
    errors += _check_section_count(sections)
    errors += _check_section_rules(new)
    return errors + _leaks({"deep_dive_sections": new})


def check_outline_fact_check(output: dict[str, Any], inputs: dict[str, Any]) -> list[str]:
    """4단계. 바뀐 필드는 corrected_text 가 있는 REFUTED 주장이 가리키는 곳뿐이어야 한다."""
    outline, claims = output.get("outline"), output.get("claims")
    if not isinstance(outline, dict) or not isinstance(claims, list):
        return ["output must have outline (object) and claims (array)"]
    errors = _check_outline_shape(outline) + _check_claims(claims, ("location",))
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
    touched = sorted({int(path[1]) for path in changed if path[0] == "deep_dive_sections"})
    errors += _check_section_rules([outline["deep_dive_sections"][index] for index in touched])
    return errors


def blocking_claims(output: dict[str, Any]) -> list[dict[str, Any]]:
    """4단계에서 섹션의 메커니즘이나 논제를 바꾸지 않고는 고칠 수 없다고 표시된 주장. 있으면 뼈대부터 다시 만든다."""
    return [claim for claim in output.get("claims", []) if isinstance(claim, dict) and claim.get("blocking") is True]


def check_article(output: dict[str, Any], outline: dict[str, Any]) -> list[str]:
    """5단계. 섹션 앵커 순서, 기존 게이트의 코드 블록·변환·코드 주석 검사, 표·지시문 낱말 없음."""
    errors = _require_strings(output, ("title", "content_markdown"))
    if errors:
        return errors
    markdown = output["content_markdown"]
    expected = article_section_ids(len(outline["deep_dive_sections"]))
    anchors = [anchor for anchor in find_anchors(markdown) if not CODE_ANCHOR_PATTERN.match(anchor)]
    if anchors != expected:
        errors.append(f"section anchors are {anchors}; they must be exactly {expected} in this order")
    gate = ValidationGate(
        min_chars=1, max_chars=_NO_LENGTH_LIMIT, structure=StructureParameters(required_sections=expected)
    )
    # check_style 의 어미 검사는 한국어 어미만 가리므로 영어 본문에서는 코드 주석 검사만 실제로 걸린다
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


def _check_outline_shape(outline: Any) -> list[str]:
    if not isinstance(outline, dict) or not _is_text(outline.get("core_message")):
        return ["outline needs core_message (non-empty string)"]
    errors: list[str] = []
    introduction = outline.get("introduction")
    if not isinstance(introduction, dict) or _require_strings(introduction, _INTRO_KEYS):
        errors.append(f"introduction needs non-empty {list(_INTRO_KEYS)}")
    sections = outline.get("deep_dive_sections")
    if not isinstance(sections, list) or not all(
        isinstance(section, dict) and not _require_strings(section, _SECTION_KEYS) for section in sections
    ):
        errors.append(f"deep_dive_sections must be an array of objects with non-empty {list(_SECTION_KEYS)}")
    application = outline.get("practical_application")
    if not isinstance(application, dict) or _require_strings(application, _APPLICATION_KEYS):
        errors.append(f"practical_application needs non-empty {list(_APPLICATION_KEYS)}")
    return errors


def _check_section_count(sections: list[dict[str, Any]]) -> list[str]:
    if len(sections) < MIN_SECTIONS:
        return [f"deep_dive_sections has {len(sections)} items; it must have at least {MIN_SECTIONS}"]
    return []


def _check_section_rules(sections: list[dict[str, Any]]) -> list[str]:
    """principle 두 문장(중심문장 + 결론), mechanism_explanation 한 문장, key_takeaway 10단어 이하."""
    errors: list[str] = []
    for section in sections:
        name = section["sub_topic"]
        sentences = count_sentences(section["principle"])
        if sentences != 2:
            errors.append(
                f"principle of '{name}' has {sentences} sentences; write exactly two (topic sentence + concluding sentence)"
            )
        sentences = count_sentences(section["mechanism_explanation"])
        if sentences != 1:
            errors.append(f"mechanism_explanation of '{name}' has {sentences} sentences; write one causal sentence")
        words = len(section["key_takeaway"].split())
        if words > MAX_TAKEAWAY_WORDS:
            errors.append(f"key_takeaway of '{name}' has {words} words; keep it within {MAX_TAKEAWAY_WORDS}")
    return errors


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


def _without_sections(outline: dict[str, Any]) -> dict[str, Any]:
    return {key: value for key, value in outline.items() if key != "deep_dive_sections"}


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
