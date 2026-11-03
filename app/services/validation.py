"""VALIDATING 게이트: LLM 없이 현재 버전을 결정론적으로 검사한다 (설계 04 문서 3절 표)."""

import ast
import json
import logging
import re
from collections import Counter

from app.models.enums import ArbitrationOutcome, ResolutionStatus, ReviewScope, RevisionCause
from app.models.post_draft import DraftVersion, PostDraft
from app.models.sections import ANCHOR_PATTERN, CODE_ANCHOR_PATTERN, REQUIRED_SECTIONS, find_anchors
from app.models.tiptap import code_language, markdown_to_tiptap, parse_markdown

logger = logging.getLogger(__name__)

_FENCE_LINE = re.compile(r"^\s*(```|~~~)")
_EXTERNAL_LINK = re.compile(r"\[[^\]]*\]\(([^)\s]+)\)")


class ValidationGate:
    """validate 는 실패 사유 목록을 돌려준다. 비어 있으면 통과다. 상태 전이(pass/fail_validation)는 호출자가 한다."""

    def __init__(self, *, min_chars: int, max_chars: int, min_code_blocks: int = 1) -> None:
        self._min_chars = min_chars
        self._max_chars = max_chars
        self._min_code_blocks = min_code_blocks

    def validate(self, draft: PostDraft) -> list[str]:
        version = draft.current_version
        markdown = version.content_markdown
        anchors = find_anchors(markdown)
        errors = _check_anchors(anchors)
        errors += _check_code_blocks(markdown, self._min_code_blocks)
        errors += _check_length(markdown, self._min_chars, self._max_chars)
        errors += _check_convertible(markdown)
        if version.origin_cause == RevisionCause.REVIEW:
            errors += _check_resolutions(draft, version)
        if version.origin_cause != RevisionCause.HUMAN:
            errors += _check_anchor_persistence(draft, version, set(anchors))
        _warn_external_links(draft.draft_id, markdown)
        return errors


def _check_anchors(anchors: list[str]) -> list[str]:
    errors = [f"필수 앵커가 없다: {section_id}" for section_id in REQUIRED_SECTIONS if section_id not in anchors]
    errors += [f"앵커가 중복된다: {section_id}" for section_id, count in Counter(anchors).items() if count > 1]
    return errors


def _check_code_blocks(markdown: str, min_code_blocks: int) -> list[str]:
    errors: list[str] = []
    lines = markdown.split("\n")
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
    try:
        if language == "python":
            ast.parse(code)
        elif language == "json":
            json.loads(code)
    except (SyntaxError, ValueError) as exc:
        return [f"{label}({language})의 문법 오류: {exc}"]
    return []


def _check_length(markdown: str, min_chars: int, max_chars: int) -> list[str]:
    length = len(markdown)
    if length < min_chars:
        return [f"본문이 {length}자다. {min_chars}자 이상이어야 한다"]
    if length > max_chars:
        return [f"본문이 {length}자다. {max_chars}자 이하여야 한다"]
    return []


def _check_convertible(markdown: str) -> list[str]:
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
            if resolution.status == ResolutionStatus.REJECTED_WITH_REASON:
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
    for url in _EXTERNAL_LINK.findall(markdown):
        if not url.startswith(("http://", "https://")):
            logger.warning("draft %s has a link that is not http(s): %s", draft_id, url)
