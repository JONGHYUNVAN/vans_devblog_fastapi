"""설명문 파이프라인에서 모델 대신 코드가 하는 조립: 사실 확인 패치 적용, 요약 표·전체 정리 표·용어 정리 절 넣기, 서론 첫 문단 꺼내기.

표를 모델이 본문과 따로 쓰면 본문과 어긋날 수 있어 작성 단계는 표를 쓰지 않고, 여기서 요약 단계 출력과 뼈대로 만든다.
"""

import re
from collections.abc import Callable
from typing import Any

from app.models.sections import ANCHOR_PATTERN, CODE_ANCHOR_PATTERN, mask_code_fences, section_anchor

GLOSSARY_SECTION = "glossary"
_NUMBERED_HEADING = re.compile(r"^##\s+(\d+)\.", re.MULTILINE)
_BLOCK_SPLIT = re.compile(r"\n[ \t]*\n")
_NOT_PROSE = ("#", "<!--", "```", "~~~", "|", "**📊", ">", "-", "1.")


def section_span(markdown: str, section_id: str) -> tuple[int, int] | None:
    """섹션 앵커 바로 뒤부터 다음 섹션 앵커 앞까지의 위치. code-N 앵커는 섹션 경계가 아니다. 없으면 None."""
    masked = mask_code_fences(markdown)
    anchors = [match for match in ANCHOR_PATTERN.finditer(masked) if not CODE_ANCHOR_PATTERN.match(match.group(1))]
    for index, match in enumerate(anchors):
        if match.group(1) == section_id:
            end = anchors[index + 1].start() if index + 1 < len(anchors) else len(markdown)
            return match.end(), end
    return None


def apply_patches(
    markdown: str, patches: list[dict[str, Any]], validate: Callable[[str], list[str]]
) -> tuple[str, list[dict[str, Any]]]:
    """사실 확인 패치를 하나씩 적용하고 (본문, 버린 패치) 를 돌려준다.

    before 가 그 섹션에 정확히 한 번 나올 때만 바꾼다. 바꾼 본문이 validate 에 걸리면(앵커·코드 블록이 깨지면) 그 패치는 되돌린다.
    버린 패치에는 rejected_because 가 붙는다.
    """
    rejected: list[dict[str, Any]] = []
    for patch in patches:
        span = section_span(markdown, patch["section_id"])
        if span is None:
            rejected.append({**patch, "rejected_because": "section not found"})
            continue
        start, end = span
        body = markdown[start:end]
        occurrences = body.count(patch["before"]) if patch["before"] else 0
        if occurrences != 1:
            rejected.append({**patch, "rejected_because": f"before occurs {occurrences} times in the section"})
            continue
        candidate = markdown[:start] + body.replace(patch["before"], patch["after"], 1) + markdown[end:]
        errors = validate(candidate)
        if errors:
            rejected.append({**patch, "rejected_because": f"patched article fails checks: {errors}"})
            continue
        markdown = candidate
    return markdown, rejected


def assemble_article(
    markdown: str, summaries: list[dict[str, Any]], outline: dict[str, Any], terms: list[dict[str, Any]]
) -> str:
    """섹션마다 요약 표, 결론 끝에 뼈대의 전체 정리 표, 글 끝에 용어 정리 절(정의가 있는 용어만)을 넣는다."""
    for summary in summaries:
        rows = [(row["item"], row["explanation"]) for row in summary["rows"]]
        table = _table(("Item", "Explanation"), rows, title=f"Summary: {summary['title']}")
        markdown = _append_to_section(markdown, summary["section_id"], table)
    overview = [(section["sub_topic"], section["key_takeaway"]) for section in outline["deep_dive_sections"]]
    markdown = _append_to_section(markdown, "conclusion", _table(("Mechanism", "Takeaway"), overview, title="Overview"))
    defined = [(term["term_en"], term["definition"]) for term in terms if term.get("definition", "").strip()]
    if defined:
        # 제목 번호는 작성 단계가 1부터 매긴다. 용어 정리는 그 다음 번호다
        numbers = [int(number) for number in _NUMBERED_HEADING.findall(mask_code_fences(markdown))]
        heading = f"## {max(numbers, default=0) + 1}. Glossary"
        table = _table(("Term", "Definition"), defined)
        markdown = f"{markdown.rstrip()}\n\n{section_anchor(GLOSSARY_SECTION)}\n{heading}\n\n{table}\n"
    return markdown


def first_paragraph(markdown: str) -> str:
    """서론의 첫 문단(제목·앵커·코드·표·목록·인용을 뺀 첫 덩어리). 메타 설명의 입력이다. 없으면 빈 문자열."""
    span = section_span(markdown, "intro")
    text = markdown[span[0] : span[1]] if span else markdown
    for block in _BLOCK_SPLIT.split(text):
        stripped = block.strip()
        if stripped and not stripped.startswith(_NOT_PROSE):
            return " ".join(stripped.split())
    return ""


def _append_to_section(markdown: str, section_id: str, block: str) -> str:
    """섹션 끝(다음 섹션 앵커 앞)에 덩어리 하나를 붙인다. 섹션이 없으면 ValueError(앞 단계 검사가 앵커를 보장한다)."""
    span = section_span(markdown, section_id)
    if span is None:
        raise ValueError(f"section not found: {section_id}")
    start, end = span
    body = markdown[start:end].rstrip()
    tail = "\n\n" + markdown[end:] if end < len(markdown) else "\n"
    return markdown[:start] + body + "\n\n" + block + tail


def _table(header: tuple[str, str], rows: list[tuple[str, str]], title: str | None = None) -> str:
    lines = [f"**📊 {title}**", ""] if title else []
    lines += [f"| {header[0]} | {header[1]} |", "|---|---|"]
    lines += [f"| {_cell(left)} | {_cell(right)} |" for left, right in rows]
    return "\n".join(lines)


def _cell(text: str) -> str:
    """표 칸 하나. 줄바꿈은 칸을 깨므로 공백으로, 세로줄은 칸 구분자라 이스케이프한다."""
    return " ".join(str(text).split()).replace("|", "\\|")
