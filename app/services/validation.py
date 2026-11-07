"""VALIDATING 게이트: LLM 없이 현재 버전을 결정론적으로 검사한다 (설계 04 문서 3절 표).

본문만 보는 검사는 check_markdown 이다. 다듬기가 패치마다 부른다.
"""

import ast
import json
import logging
import re
from collections import Counter

from app.models.enums import ArbitrationOutcome, ResolutionStatus, ReviewScope, RevisionCause
from app.models.pipeline import CodeCommentParameters, Parameters, StructureParameters, StyleParameters
from app.models.post_draft import DraftVersion, PostDraft
from app.models.sections import ANCHOR_PATTERN, CODE_ANCHOR_PATTERN, find_anchors, markdown_links, mask_code_fences
from app.models.tiptap import code_language, markdown_to_tiptap, parse_markdown

logger = logging.getLogger(__name__)

_FENCE_LINE = re.compile(r"^\s*(```|~~~)")
_TREE_SITTER_GRAMMARS = {"typescript": "typescript", "ts": "typescript", "tsx": "tsx"}


class ValidationGate:
    """validate 는 실패 사유 목록을 돌려준다. 비어 있으면 통과다. 상태 전이(pass/fail_validation)는 호출자가 한다.

    임계값과 구조 규칙은 라운드 버전 문서의 조절 값이다(from_parameters). 글마다 라운드가 다를 수 있어 조립 때 한 번이 아니라 걸음마다 만든다.
    """

    def __init__(
        self,
        *,
        min_chars: int,
        max_chars: int,
        min_code_blocks: int = 1,
        structure: StructureParameters | None = None,
        style: StyleParameters | None = None,
        code_comments: CodeCommentParameters | None = None,
    ) -> None:
        self._min_chars = min_chars
        self._max_chars = max_chars
        self._min_code_blocks = min_code_blocks
        self._structure = structure if structure is not None else StructureParameters()
        self._style = style if style is not None else StyleParameters()
        self._code_comments = code_comments if code_comments is not None else CodeCommentParameters()

    @classmethod
    def from_parameters(cls, parameters: Parameters) -> "ValidationGate":
        """라운드 버전의 조절 값으로 게이트를 만든다. 글마다 라운드가 다를 수 있어 걸음마다 부른다."""
        return cls(
            min_chars=parameters.draft_min_chars,
            max_chars=parameters.draft_max_chars,
            min_code_blocks=parameters.min_code_blocks,
            structure=parameters.structure,
            style=parameters.style,
            code_comments=parameters.code_comments,
        )

    def validate(self, draft: PostDraft) -> list[str]:
        """현재 버전의 실패 사유 목록. 비어 있으면 통과다. 상태 전이는 호출자가 한다.

        본문 검사(앵커·코드 블록·길이·변환 가능) 뒤에, 리뷰 반영본이면 해소 대조, 사람 재수정이 아니면 앵커 보존을 본다. 외부 링크는 막지 않고 경고만 남긴다.
        """
        version = draft.current_version
        markdown = version.content_markdown
        # 1) 본문만 보는 검사. LLM 앞의 결정론적 관문이라 실패하면 REVISING(GATE) 로 돌아가고 iteration 은 오르지 않는다(D-11)
        errors = self.check_markdown(markdown, draft.topic.target_level)
        # 1a) 문체·코드 주석 (D-85). 다듬기의 check_markdown 에는 넣지 않는다: 다듬기는 자기 패치의 구조만 책임진다
        errors += self.check_style(markdown)
        # 2) 리뷰 반영본만 해소 대조. 초안·게이트 재작성·사람 재수정은 대조할 리뷰가 없다
        if version.origin_cause == RevisionCause.REVIEW:
            errors += _check_resolutions(draft, version)
        # 3) 사람 재수정은 리뷰 없이 발행 대기로 가므로 앵커 보존을 묻지 않는다. 그 밖에는 미결 지적이 가리킬 자리가 남아야 한다
        if version.origin_cause != RevisionCause.HUMAN:
            errors += _check_anchor_persistence(draft, version, set(find_anchors(markdown)))
        _warn_external_links(draft.draft_id, markdown)
        return errors

    def check_markdown(self, markdown: str, level: int | None) -> list[str]:
        """본문만 보는 검사. 리뷰 해소와 무관한 순수 함수다."""
        errors = _check_anchors(find_anchors(markdown), self._structure.required_sections_for(level))
        errors += _check_code_blocks(markdown, self._min_code_blocks)
        errors += _check_length(markdown, self._min_chars, self._max_chars)
        errors += _check_convertible(markdown)
        return errors

    def check_style(self, markdown: str) -> list[str]:
        """문체·코드 주석 검사 (D-85). 어미를 프롬프트로 고정하지 않고 생성 결과를 잰다. 위반은 예시를 들어 수정 호출이 고치게 한다.

        VALIDATING 게이트(validate)만 부른다. 다듬기가 패치마다 부르는 check_markdown 에는 넣지 않는다: 다듬기 전부터 있던 어미를
        다듬기 실패로 돌리면 안 되고, 문단 하나로는 혼용을 가릴 수 없다.
        """
        return _check_register(markdown, self._style, self._structure.prose_register) + _check_code_comments(
            markdown, self._code_comments
        )


def _check_anchors(anchors: list[str], required_sections: list[str]) -> list[str]:
    errors = [f"필수 앵커가 없다: {section_id}" for section_id in required_sections if section_id not in anchors]
    errors += [f"앵커가 중복된다: {section_id}" for section_id, count in Counter(anchors).items() if count > 1]
    return errors


def _check_code_blocks(markdown: str, min_code_blocks: int) -> list[str]:
    errors: list[str] = []
    lines = markdown.split("\n")
    # 펜스 수가 홀수면 닫히지 않은 코드 블록이다. markdown-it 은 이것을 조용히 본문으로 읽으므로 파서보다 먼저 센다
    if sum(1 for line in lines if _FENCE_LINE.match(line)) % 2:
        errors.append("코드 펜스의 열림·닫힘 짝이 맞지 않는다")
    fences = [token for token in parse_markdown(markdown) if token.type == "fence"]
    if len(fences) < min_code_blocks:
        errors.append(f"코드 블록이 {len(fences)}개다. {min_code_blocks}개 이상이어야 한다")
    anchored_lines: set[int] = set()
    for fence in fences:
        start_line = fence.map[0] if fence.map else 0
        language = code_language(fence.info)
        label = f"{start_line + 1}행의 코드 블록"
        if language is None:
            errors.append(f"{label}에 언어 태그가 없다")
        if not fence.content.strip():
            errors.append(f"{label}이 비어 있다")
        # code-N 앵커가 코드 블록 바로 위에 있어야 리뷰 지적이 코드 블록을 가리킬 수 있다(04 문서 3절)
        anchor_line = _previous_nonblank(lines, start_line)
        anchor = ANCHOR_PATTERN.search(lines[anchor_line]) if anchor_line is not None else None
        if anchor is None or not CODE_ANCHOR_PATTERN.match(anchor.group(1)):
            errors.append(f"{label} 바로 위에 code-N 앵커가 없다")
        else:
            anchored_lines.add(anchor_line)
        errors += _check_syntax(label, language, fence.content)
    for number, line in enumerate(lines):
        match = ANCHOR_PATTERN.search(line)
        if match and CODE_ANCHOR_PATTERN.match(match.group(1)) and number not in anchored_lines:
            errors.append(f"앵커 {match.group(1)} 바로 아래에 코드 블록이 없다")
    return errors


def _previous_nonblank(lines: list[str], index: int) -> int | None:
    for number in range(index - 1, -1, -1):
        if lines[number].strip():
            return number
    return None


def _check_syntax(label: str, language: str | None, code: str) -> list[str]:
    # 문법만 본다. 코드 블록은 실행되지 않으니 타입·논리는 리뷰어 몫이고, 잘린 조각은 -snippet 태그로 검사를 피한다
    try:
        if language == "python":
            ast.parse(code)
        elif language == "json":
            json.loads(code)
        elif language in _TREE_SITTER_GRAMMARS and _has_syntax_error(_TREE_SITTER_GRAMMARS[language], code):
            return [f"{label}({language})의 문법 오류: 잘린 조각이면 {language}-snippet 태그를 쓴다"]
    except (SyntaxError, ValueError) as exc:
        return [f"{label}({language})의 문법 오류: {exc}"]
    return []


def _has_syntax_error(grammar: str, code: str) -> bool:
    """tree-sitter 는 오류를 던지지 않고 트리에 표시한다. 문법 검사만 하고 타입·논리 오류는 잡지 못한다.

    문법은 wheel 에 없어 첫 사용 때 받는다. 이미지에 넣어 두지 않은 환경에서 받지 못하면 그 검사만 건너뛴다. 게이트는 네트워크에 기대지 않는다.
    """
    try:
        from tree_sitter_language_pack import get_parser  # 네이티브 모듈은 쓸 때만 읽는다 (코드 그래프 G-24)

        parser = get_parser(grammar)
    except Exception as exc:  # 다운로드·네이티브 로드 실패의 종류가 여럿이다
        logger.warning("skip %s syntax check: %s: %s", grammar, type(exc).__name__, exc)
        return False
    return parser.parse(code.encode()).root_node.has_error


# ---------- 문체 (D-85) ----------
# 문장은 마침표·물음표·느낌표 뒤 공백에서 나눈다. 끝의 따옴표·괄호·강조 기호는 어미 판별 전에 뗀다
_SENTENCE_SPLIT = re.compile(r"(?<=[.!?])\s+")
_SENTENCE_TAIL = re.compile(r"[\s.!?…\"'”’)\]`*_]+$")
# 줄 머리로 거르는 것: 제목, 표, 앵커·HTML 주석, 인용, 구분선. 목록 표식은 떼고 문장으로 센다
_SKIP_LINE = re.compile(r"^\s*(#|\||<!--|>|---\s*$)")
_LIST_MARKER = re.compile(r"^\s*(?:[-*+]|\d+[.)])\s+")
# 끝 글자로 어미를 가른다. 순서가 중요하다: "~세요"는 "~요"보다, "~니다"는 "~다"보다 먼저 봐야 한다
_IMPERATIVE_ENDINGS = ("하십시오", "십시오", "하세요", "주세요", "보세요", "하라", "해라", "하자", "보자")
_POLITE_ENDINGS = ("니다", "니까")  # 합쇼체: ~습니다·~입니다·~합니까
_HAEYO_ENDINGS = ("요", "죠")  # 해요체
_PLAIN_ENDINGS = ("다",)  # 해라체 서술문. 의문·영문·코드로 끝나는 문장은 가리지 않는다
_REGISTER_LABELS = {"plain": "해라체(~다)", "polite": "합쇼체(~습니다)", "haeyo": "해요체(~요)"}
_EXAMPLES_SHOWN = 3


def _classify_ending(sentence: str) -> str | None:
    """문장 하나의 어미 종류. imperative·polite·haeyo·plain 중 하나이고, 가릴 수 없으면 None."""
    core = _SENTENCE_TAIL.sub("", sentence)
    if core.endswith(_IMPERATIVE_ENDINGS):
        return "imperative"
    if core.endswith(_POLITE_ENDINGS):
        return "polite"
    if core.endswith(_HAEYO_ENDINGS):
        return "haeyo"
    if core.endswith(_PLAIN_ENDINGS):
        return "plain"
    return None


def _prose_sentences(markdown: str) -> list[tuple[str, str]]:
    """코드·제목·표·앵커·인용을 뺀 서술문을 (섹션 id, 문장) 으로. 섹션은 직전 앵커다(없으면 "intro" 앞이라 "?")."""
    sentences: list[tuple[str, str]] = []
    section = "?"
    for line in mask_code_fences(markdown).split("\n"):
        anchor = ANCHOR_PATTERN.search(line)
        if anchor:
            section = anchor.group(1)
            continue
        if not line.strip() or _SKIP_LINE.match(line):
            continue
        text = _LIST_MARKER.sub("", line).strip()
        sentences += [(section, piece.strip()) for piece in _SENTENCE_SPLIT.split(text) if piece.strip()]
    return sentences


def _format_examples(examples: list[tuple[str, str]]) -> str:
    return "; ".join(f"[{section}] '{sentence[:60]}'" for section, sentence in examples[:_EXAMPLES_SHOWN])


def _check_register(markdown: str, style: StyleParameters, expected: str | None = None) -> list[str]:
    """어미 혼용·명령형 검사 (D-85)와 기대 문체 검사 (D-86). 소수 어미가 비율·문장 수 임계를 모두 넘을 때만 혼용으로 본다.

    expected 가 있으면 다수 어미가 그 문체여야 한다(블로그 사람 글은 전부 합쇼체). 어미를 고치는 것은 수정 호출의 몫이다.
    오류 문장에 예시와 섹션을 넣어 모델이 어디를 고칠지 알게 한다.
    """
    classified: dict[str, list[tuple[str, str]]] = {"plain": [], "polite": [], "haeyo": [], "imperative": []}
    for section, sentence in _prose_sentences(markdown):
        kind = _classify_ending(sentence)
        if kind is not None:
            classified[kind].append((section, sentence))
    errors: list[str] = []
    registers = {kind: items for kind, items in classified.items() if kind != "imperative" and items}
    total = sum(len(items) for items in registers.values())
    if len(registers) > 1:
        majority = max(registers, key=lambda kind: len(registers[kind]))
        for kind, items in registers.items():
            if kind == majority:
                continue
            # 비율과 최소 문장 수를 함께 본다. 긴 글의 한두 문장, 짧은 글의 높은 비율 어느 쪽도 혼자서는 혼용이 아니다
            if len(items) >= style.minority_register_min and len(items) / total >= style.minority_register_ratio:
                errors.append(
                    f"문체가 섞였다: {_REGISTER_LABELS[majority]} {len(registers[majority])}문장, "
                    f"{_REGISTER_LABELS[kind]} {len(items)}문장. 한 가지 어미로 통일하라. 예: {_format_examples(items)}"
                )
    if expected in _REGISTER_LABELS and total >= style.minority_register_min:
        majority = max(registers, key=lambda kind: len(registers[kind])) if registers else None
        if majority is not None and majority != expected:
            # 블로그 전체가 한 문체다. 글 하나가 다른 문체면 독자에게 반말·존댓말이 섞인 블로그로 보인다
            errors.append(
                f"블로그 글의 문체는 {_REGISTER_LABELS[expected]}인데 본문은 {_REGISTER_LABELS[majority]} "
                f"{len(registers[majority])}문장이다. 모든 서술문을 {_REGISTER_LABELS[expected]}로 바꿔라. "
                f"예: {_format_examples(registers[majority])}"
            )
    if style.forbid_imperatives and classified["imperative"]:
        errors.append(
            f"설명글에 명령형·청유형 문장이 {len(classified['imperative'])}개 있다. 서술문으로 바꿔라. "
            f"예: {_format_examples(classified['imperative'])}"
        )
    return errors


# ---------- 코드 블록 주석 (D-85) ----------
_C_STYLE = ("//", "/*", "*", "*/")
# 라운드 b48cf759 의 A 글(Java 8블록 중 3블록 주석 0줄)이 python·typescript 만 보던 검사를 통과했다. 블로그가 다루는 언어를 다 넣는다
_COMMENT_MARKERS = {
    "python": ("#", '"""', "'''"),
    "typescript": _C_STYLE,
    "ts": _C_STYLE,
    "tsx": _C_STYLE,
    "javascript": _C_STYLE,
    "js": _C_STYLE,
    "java": _C_STYLE,
    "kotlin": _C_STYLE,
    "kt": _C_STYLE,
    "go": _C_STYLE,
    "rust": _C_STYLE,
    "csharp": _C_STYLE,
    "cs": _C_STYLE,
    "sql": ("--", "/*", "*", "*/"),
    "bash": ("#",),
    "sh": ("#",),
    "yaml": ("#",),
    "yml": ("#",),
}
_PATH_HEADER = re.compile(r"[\w./-]+\.(py|ts|tsx|js|jsx|mjs|java|kt|kts|go|rs|cs|sql|sh|ya?ml)\b")


def _comment_markers(language: str | None) -> tuple[str, ...] | None:
    """언어 태그(스니펫 접미 포함)에 맞는 주석 표식. 주석 문법을 모르는 언어(json·sql·bash 등)는 None 이라 검사하지 않는다."""
    if language is None:
        return None
    return _COMMENT_MARKERS.get(language.removesuffix("-snippet"))


def _check_code_comments(markdown: str, rule: CodeCommentParameters) -> list[str]:
    """긴 코드 블록의 설명 주석 수 (D-85). 파일 경로만 적은 머리줄은 주석으로 세지 않는다.

    독자는 본문보다 코드를 복사한다. 왜 그 분기인지, 어느 줄이 불변식을 지키는지는 코드 안에 있어야 한다.
    """
    errors: list[str] = []
    lines = markdown.split("\n")
    for fence in (token for token in parse_markdown(markdown) if token.type == "fence"):
        markers = _comment_markers(code_language(fence.info))
        if markers is None:
            continue
        code_lines = [line.strip() for line in fence.content.split("\n") if line.strip()]
        if len(code_lines) < rule.min_lines:
            continue
        comment_lines = [line for line in code_lines if line.startswith(markers)]
        if comment_lines and code_lines[0] == comment_lines[0] and _PATH_HEADER.search(comment_lines[0]):
            comment_lines = comment_lines[1:]  # `# app/x.py` 같은 머리줄은 위치 표시지 설명이 아니다
        required = -(-len(code_lines) // rule.lines_per_comment)  # 올림
        if len(comment_lines) >= required:
            continue
        start_line = fence.map[0] if fence.map else 0
        anchor_line = _previous_nonblank(lines, start_line)
        anchor = ANCHOR_PATTERN.search(lines[anchor_line]) if anchor_line is not None else None
        label = f"코드 블록 {anchor.group(1)}" if anchor else f"{start_line + 1}행의 코드 블록"
        errors.append(
            f"{label}({len(code_lines)}줄)에 설명 주석이 {len(comment_lines)}줄이다. {rule.lines_per_comment}줄당 1줄 이상, "
            "왜 그렇게 하는지와 중요한 분기에 한국어 주석을 넣어라. 파일 경로만 적은 머리줄은 세지 않는다"
        )
    return errors


def _check_length(markdown: str, min_chars: int, max_chars: int) -> list[str]:
    length = len(markdown)
    if length < min_chars:
        return [f"본문이 {length}자다. {min_chars}자 이상이어야 한다"]
    if length > max_chars:
        return [f"본문이 {length}자다. {max_chars}자 이하여야 한다"]
    return []


def _check_convertible(markdown: str) -> list[str]:
    # 발행 때 Tiptap JSON 으로 변환한다. 승인 뒤에 변환이 실패하면 사람이 다시 봐야 하므로 게이트에서 미리 돌려 본다
    try:
        markdown_to_tiptap(markdown)
    except ValueError as exc:
        return [f"발행 형식으로 변환할 수 없다: {exc}"]
    return []


def _check_resolutions(draft: PostDraft, version: DraftVersion) -> list[str]:
    """두 층 검사 (04 문서 3절): 차단 집합과 정확히 일치, FULL 버전의 비차단은 선택, 그 밖의 ID 는 실패."""
    if draft.last_reviewed_version is None:
        return []
    reviewed = draft.last_reviewed_version
    blocking_ids = {item.item_id for item in draft.open_blocking_items(reviewed)}
    reviews = draft.reviews_of(reviewed)
    non_blocking_ids: set[str] = set()
    if any(review.scope == ReviewScope.FULL for review in reviews):
        non_blocking_ids = {
            item.item_id for review in reviews for item in review.items if not item.severity.is_blocking
        }
    upheld_reviewer = {
        arbitration.item_id
        for arbitration in draft.arbitrations
        if arbitration.outcome == ArbitrationOutcome.UPHOLD_REVIEWER
    }
    ids = [resolution.item_id for resolution in version.resolutions]
    errors = [f"해소가 중복된다: {item_id}" for item_id, count in Counter(ids).items() if count > 1]
    errors += [f"차단 항목의 해소가 없다: {item_id}" for item_id in sorted(blocking_ids - set(ids))]
    for resolution in version.resolutions:
        item_id = resolution.item_id
        if item_id in blocking_ids:
            if resolution.status == ResolutionStatus.DEFERRED:
                errors.append(f"차단 항목은 미룰 수 없다: {item_id}")
            if item_id in upheld_reviewer and resolution.status != ResolutionStatus.ACCEPTED:
                errors.append(f"중재에서 리뷰어가 맞다고 판정된 항목은 수용해야 한다: {item_id}")
        elif item_id in non_blocking_ids:
            if resolution.status in (ResolutionStatus.REJECTED_WITH_REASON, ResolutionStatus.OUT_OF_SCOPE):
                errors.append(f"비차단 항목은 ACCEPTED 또는 DEFERRED 만 가능하다: {item_id}")
        else:
            errors.append(f"리뷰에 없는 항목이다: {item_id}")
    return errors


def _check_anchor_persistence(draft: PostDraft, version: DraftVersion, anchors: set[str]) -> list[str]:
    """직전 리뷰 완료 버전의 미결 차단 항목이 가리키는 앵커가 남아 있거나, section_id_after 가 새 위치를 가리켜야 한다."""
    if draft.last_reviewed_version is None:
        return []
    errors: list[str] = []
    for item in draft.open_blocking_items(draft.last_reviewed_version):
        if item.section_id in anchors:
            continue
        resolution = version.resolution_for(item.item_id)
        moved_to = resolution.section_id_after if resolution else None
        if moved_to is None:
            errors.append(f"항목 {item.item_id} 의 앵커 {item.section_id} 가 사라졌는데 section_id_after 가 없다")
        elif moved_to not in anchors:
            errors.append(f"항목 {item.item_id} 의 section_id_after {moved_to} 가 본문에 없다")
    return errors


def _warn_external_links(draft_id: str, markdown: str) -> None:
    for url in markdown_links(markdown):
        if not url.startswith(("http://", "https://")):
            logger.warning("draft %s has a link that is not http(s): %s", draft_id, url)
