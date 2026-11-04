"""본문 마크다운의 섹션 앵커(<!-- sec:id -->) 찾기·지우기·자르기 (설계 04 문서 3절). 순수 함수.

코드 펜스 안의 앵커 문자열은 앵커가 아니다(코드 내용). 세 함수 모두 펜스 안을 가린 뒤 찾는다.
"""

import re

ANCHOR_PATTERN = re.compile(r"<!--\s*sec:([A-Za-z0-9_-]+)\s*-->")
# 앵커만 있는 줄은 앵커와 양옆 공백을 지운다. 줄바꿈은 남겨 앞뒤 문단이 합쳐지지 않게 한다. 줄 안에 섞인 앵커는 앵커만 지운다.
_STRIP_PATTERN = re.compile(
    r"^[ \t]*<!--\s*sec:[A-Za-z0-9_-]+\s*-->[ \t]*$|<!--\s*sec:[A-Za-z0-9_-]+\s*-->", re.MULTILINE
)
_FENCE_PATTERN = re.compile(r"^ {0,3}(`{3,}|~{3,})")
CODE_ANCHOR_PATTERN = re.compile(r"^code-\d+$")

REQUIRED_SECTIONS = ["intro", "body-1", "tradeoff", "conclusion"]


def normalize_section_id(section_id: str) -> str:
    """모델이 앵커 표기를 그대로 옮긴 `sec:body-3` 을 앵커 id `body-3` 으로 바꾼다 (설계 07 문서 D-46)."""
    return section_id.strip().removeprefix("sec:")


def section_anchor(section_id: str) -> str:
    return f"<!-- sec:{section_id} -->"


def find_anchors(markdown: str) -> list[str]:
    """나오는 순서대로. 중복도 그대로 돌려준다(게이트가 중복을 잡는다)."""
    return ANCHOR_PATTERN.findall(mask_code_fences(markdown))


def strip_anchors(markdown: str) -> str:
    """발행 변환 전에 앵커를 지운다."""
    masked = mask_code_fences(markdown)
    pieces: list[str] = []
    last = 0
    for match in _STRIP_PATTERN.finditer(masked):
        pieces.append(markdown[last : match.start()])
        last = match.end()
    pieces.append(markdown[last:])
    return "".join(pieces)


def section_text(markdown: str, section_id: str) -> str | None:
    """앵커부터 다음 앵커 직전까지(앵커 줄 제외). 없으면 None. 같은 앵커가 둘이면 첫 번째."""
    matches = list(ANCHOR_PATTERN.finditer(mask_code_fences(markdown)))
    for index, match in enumerate(matches):
        if match.group(1) != section_id:
            continue
        end = matches[index + 1].start() if index + 1 < len(matches) else len(markdown)
        return markdown[match.end() : end].strip("\n")
    return None


def mask_code_fences(markdown: str) -> str:
    """펜스 코드 블록 안의 줄을 같은 길이의 공백으로 바꾼다. 길이가 같아 원문 인덱스를 그대로 쓸 수 있다.

    펜스 규칙은 CommonMark: 들여쓰기 3칸 이하의 ``` 또는 ~~~ 3개 이상으로 열고, 같은 문자가 여는 것 이상 길이로만 있는 줄로 닫는다.
    """
    lines = markdown.split("\n")
    fence: str | None = None
    for index, line in enumerate(lines):
        match = _FENCE_PATTERN.match(line)
        if fence is None:
            if match:
                fence = match.group(1)
        elif (
            match
            and match.group(1)[0] == fence[0]
            and len(match.group(1)) >= len(fence)
            and not line[match.end() :].strip()
        ):
            fence = None
        else:
            lines[index] = " " * len(line)
    return "\n".join(lines)
