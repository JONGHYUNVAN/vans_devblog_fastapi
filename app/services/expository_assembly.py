"""설명문 파이프라인에서 모델 대신 코드가 하는 조립: 사실 확인 패치 적용, 요약 표·전체 정리 표·용어 정리 절 넣기, 서론 첫 문단 꺼내기,
8단계가 문단 하나씩 고치도록 본문을 덩어리로 나누고 다시 잇기.

표를 모델이 본문과 따로 쓰면 본문과 어긋날 수 있어 작성 단계는 표를 쓰지 않고, 여기서 요약 단계 출력과 뼈대로 만든다.
"""

import re
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from app.models.sections import ANCHOR_PATTERN, CODE_ANCHOR_PATTERN, mask_code_fences, section_anchor

GLOSSARY_SECTION = "glossary"
BLOCK_KINDS = ("anchor", "heading", "code", "table", "quote", "list", "paragraph")
_NUMBERED_HEADING = re.compile(r"^##\s+(\d+)\.", re.MULTILINE)
_BLOCK_SPLIT = re.compile(r"\n[ \t]*\n")
_NOT_PROSE = ("#", "<!--", "```", "~~~", "|", ">", "-", "1.")
# 코드가 표 위에 넣는 라벨 줄(`**요약: …**`). 통째로 굵은 한 줄이면 라벨로 본다. 옛 실행 문서의 `**📊 …**` 도 여기에 든다
_TABLE_LABEL = re.compile(r"\*\*(?:(?!\*\*).)+\*\*")
_FENCE_LINE = re.compile(r"^ {0,3}(`{3,}|~{3,})")  # models/sections 의 펜스 규칙과 같다
_ANCHOR_LINE = re.compile(r"^\s*<!--\s*sec:[A-Za-z0-9_-]+\s*-->\s*$")
_LIST_ITEM = re.compile(r"^(?:[-*+]|\d+\.)\s")


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
    markdown: str,
    summaries: list[dict[str, Any]],
    terms: list[dict[str, Any]],
    *,
    overview_header: tuple[str, str],
    overview_rows: list[tuple[str, str]],
    last_section: str,
) -> str:
    """섹션마다 요약 표, 마지막 섹션 끝에 전체 정리 표, 글 끝에 용어 정리 절(정의가 있는 용어만)을 넣는다.

    전체 정리 표의 행은 뼈대 본론 region 의 (heading, takeaway), 머리는 글 양식의 overview_header 다(D-104).
    """
    # 표 라벨은 한국어로 넣는다. 8단계는 문단만 고치고 표를 보지 않는다
    for summary in summaries:
        rows = [(row["item"], row["explanation"]) for row in summary["rows"]]
        table = _table(("항목", "설명"), rows, title=f"요약: {summary['title']}")
        markdown = _append_to_section(markdown, summary["section_id"], table)
    markdown = _append_to_section(markdown, last_section, _table(overview_header, overview_rows, title="전체 정리"))
    defined = [(term["term_en"], term["definition"]) for term in terms if term.get("definition", "").strip()]
    if defined:
        # 제목 번호는 작성 단계가 1부터 매긴다. 용어 정리는 그 다음 번호다
        numbers = [int(number) for number in _NUMBERED_HEADING.findall(mask_code_fences(markdown))]
        heading = f"## {max(numbers, default=0) + 1}. 용어 정리"
        table = _table(("용어", "정의"), defined)
        markdown = f"{markdown.rstrip()}\n\n{section_anchor(GLOSSARY_SECTION)}\n{heading}\n\n{table}\n"
    return markdown


def first_paragraph(markdown: str) -> str:
    """서론의 첫 문단(제목·앵커·코드·표·목록·인용을 뺀 첫 덩어리). 메타 설명의 입력이다. 없으면 빈 문자열."""
    span = section_span(markdown, "intro")
    text = markdown[span[0] : span[1]] if span else markdown
    for block in _BLOCK_SPLIT.split(text):
        stripped = block.strip()
        if stripped and not stripped.startswith(_NOT_PROSE) and not _TABLE_LABEL.fullmatch(stripped):
            return " ".join(stripped.split())
    return ""


@dataclass(frozen=True)
class Block:
    """빈 줄로 나뉜 본문 덩어리 하나. kind 는 BLOCK_KINDS 중 하나, section 은 그 덩어리가 든 섹션 id(첫 앵커 앞이면 빈 문자열)."""

    text: str
    kind: str
    section: str


def split_blocks(markdown: str) -> list[Block]:
    """본문을 빈 줄로 나눈 덩어리 목록. 코드 펜스 안의 빈 줄은 나누지 않는다. 8단계가 문단 하나씩 고치는 단위다.

    code-N 앵커 줄은 바로 아래 펜스와 한 덩어리라 code 로 남는다. 섹션 앵커와 `## N.` 제목은 보통 한 덩어리(heading)다.
    """
    chunks: list[list[str]] = []
    current: list[str] = []
    fence: str | None = None
    for line in markdown.split("\n"):
        match = _FENCE_LINE.match(line)
        if fence is None and match:
            fence = match.group(1)
        elif (
            fence is not None
            and match
            and match.group(1)[0] == fence[0]
            and len(match.group(1)) >= len(fence)
            and not line[match.end() :].strip()
        ):
            fence = None
        if fence is None and not line.strip():
            if current:
                chunks.append(current)
                current = []
            continue
        current.append(line)
    if current:
        chunks.append(current)
    blocks: list[Block] = []
    section = ""
    for lines in chunks:
        text = "\n".join(lines)
        kind = _block_kind(lines)
        if kind != "code":
            for match in ANCHOR_PATTERN.finditer(text):
                if not CODE_ANCHOR_PATTERN.match(match.group(1)):
                    section = match.group(1)
        blocks.append(Block(text=text, kind=kind, section=section))
    return blocks


def join_blocks(blocks: list[Block]) -> str:
    """split_blocks 의 반대. 덩어리 사이에 빈 줄 하나, 끝에 줄바꿈 하나."""
    return "\n\n".join(block.text for block in blocks) + "\n"


def _block_kind(lines: list[str]) -> str:
    if any(_FENCE_LINE.match(line) for line in lines):
        return "code"
    rest = [line.strip() for line in lines if not _ANCHOR_LINE.match(line)]
    if not rest:
        return "anchor"
    first = rest[0]
    if first.startswith("#"):
        return "heading"
    if first.startswith("|") or (len(rest) == 1 and _TABLE_LABEL.fullmatch(first)):
        return "table"
    if first.startswith(">"):
        return "quote"
    if _LIST_ITEM.match(first):
        return "list"
    return "paragraph"


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
    lines = [f"**{title}**", ""] if title else []
    lines += [f"| {header[0]} | {header[1]} |", "|---|---|"]
    lines += [f"| {_cell(left)} | {_cell(right)} |" for left, right in rows]
    return "\n".join(lines)


def _cell(text: str) -> str:
    """표 칸 하나. 줄바꿈은 칸을 깨므로 공백으로, 세로줄은 칸 구분자라 이스케이프한다."""
    return " ".join(str(text).split()).replace("|", "\\|")
