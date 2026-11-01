"""본문 마크다운의 섹션 앵커(<!-- sec:id -->) 찾기·지우기·자르기 (설계 04 문서 3절). 순수 함수."""

import re

ANCHOR_PATTERN = re.compile(r"<!--\s*sec:([A-Za-z0-9_-]+)\s*-->")
# 앵커만 있는 줄은 줄째 지운다. 줄 안에 섞인 앵커는 앵커만 지운다.
_ANCHOR_LINE_PATTERN = re.compile(r"^[ \t]*<!--\s*sec:[A-Za-z0-9_-]+\s*-->[ \t]*\n?", re.MULTILINE)
CODE_ANCHOR_PATTERN = re.compile(r"^code-\d+$")

REQUIRED_SECTIONS = ["intro", "body-1", "tradeoff", "conclusion"]


def section_anchor(section_id: str) -> str:
    return f"<!-- sec:{section_id} -->"


def find_anchors(markdown: str) -> list[str]:
    """나오는 순서대로. 중복도 그대로 돌려준다(게이트가 중복을 잡는다)."""
    return ANCHOR_PATTERN.findall(markdown)


def strip_anchors(markdown: str) -> str:
    """발행 변환 전에 앵커를 지운다."""
    return ANCHOR_PATTERN.sub("", _ANCHOR_LINE_PATTERN.sub("", markdown))


def section_text(markdown: str, section_id: str) -> str | None:
    """앵커부터 다음 앵커 직전까지(앵커 줄 제외). 없으면 None. 같은 앵커가 둘이면 첫 번째."""
    matches = list(ANCHOR_PATTERN.finditer(markdown))
    for index, match in enumerate(matches):
        if match.group(1) != section_id:
            continue
        end = matches[index + 1].start() if index + 1 < len(matches) else len(markdown)
        return markdown[match.end() : end].strip("\n")
    return None
