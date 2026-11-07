"""VALIDATING 게이트: LLM 없이 현재 버전을 결정론적으로 검사한다 (설계 04 문서 3절 표).

본문만 보는 검사는 check_markdown 이다. 다듬기가 패치마다 부른다.
"""

import ast
import json
import logging
import re
from collections import Counter

from app.models.enums import ArbitrationOutcome, ResolutionStatus, ReviewScope, RevisionCause
from app.models.pipeline import Parameters, StructureParameters
from app.models.post_draft import DraftVersion, PostDraft
from app.models.sections import ANCHOR_PATTERN, CODE_ANCHOR_PATTERN, find_anchors, markdown_links
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
    ) -> None:
        self._min_chars = min_chars
        self._max_chars = max_chars
        self._min_code_blocks = min_code_blocks
        self._structure = structure if structure is not None else StructureParameters()

    @classmethod
    def from_parameters(cls, parameters: Parameters) -> "ValidationGate":
        """라운드 버전의 조절 값으로 게이트를 만든다. 글마다 라운드가 다를 수 있어 걸음마다 부른다."""
        return cls(
            min_chars=parameters.draft_min_chars,
            max_chars=parameters.draft_max_chars,
            min_code_blocks=parameters.min_code_blocks,
            structure=parameters.structure,
        )

    def validate(self, draft: PostDraft) -> list[str]:
        """현재 버전의 실패 사유 목록. 비어 있으면 통과다. 상태 전이는 호출자가 한다.

        본문 검사(앵커·코드 블록·길이·변환 가능) 뒤에, 리뷰 반영본이면 해소 대조, 사람 재수정이 아니면 앵커 보존을 본다. 외부 링크는 막지 않고 경고만 남긴다.
        """
        version = draft.current_version
        markdown = version.content_markdown
        # 1) 본문만 보는 검사. LLM 앞의 결정론적 관문이라 실패하면 REVISING(GATE) 로 돌아가고 iteration 은 오르지 않는다(D-11)
        errors = self.check_markdown(markdown, draft.topic.target_level)
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
