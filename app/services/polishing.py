"""발행 전 다듬기: 정규식, 줄바꿈(가장 싼 모델), 문체(작성 벤더가 아닌 모델). 뜻을 바꾸지 않는 표현만 고친다.

모델이 글 전체를 다시 쓰지 못하게 문단 단위 패치만 받고, 안전장치를 통과한 패치만 적용한다. 압축된 서사를 풀어 쓰는 일은 하지 않는다.
다듬기 실패는 글을 멈추지 않는다. 수정 루프로 돌아가지 않고, 패치가 없으면 새 버전도 만들지 않는다. 호출·게이트 실패는 PolishRecord 에 남는다.
"""

import logging
import re
from collections import Counter
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from app.agents.builders import persona_of, render_messages
from app.agents.contracts import ParagraphForPolish, PolishFormatInput, PolishOutput, PolishToneInput
from app.core.config import Settings
from app.models.enums import CallKind
from app.models.model_binding import CallModel
from app.models.post_draft import DiscardedPatch, PolishRecord, PostDraft
from app.models.publishing_round import PublishingRound
from app.models.sections import ANCHOR_PATTERN, find_anchors
from app.models.tiptap import parse_markdown, top_level_paragraphs
from app.services.agent_calls import AgentCallService
from app.services.routing import DraftRoutes
from app.services.validation import ValidationGate

logger = logging.getLogger(__name__)

_LONG_PARAGRAPH_CHARS = 300  # 정규식이 고칠 수 없어 사람에게 알린다
_INLINE_CODE = re.compile(r"`[^`\n]+`")
_NUMBER = re.compile(r"\d+(?:[.,]\d+)*")
_URL = re.compile(r"https?://[^\s)\]]+")
_LATIN = re.compile(r"[A-Za-z][A-Za-z0-9_+./-]*")
_NEGATION = re.compile(r"[않못없]|아니|(?<![가-힣])안(?= )|말아|말고")
_LENGTH_SLACK_CHARS = 30  # 문체 패치의 길이 변화 허용폭. 이보다 크게 바뀌면 설명을 더하거나 뺀 것으로 본다
_LENGTH_SLACK_RATIO = 0.25
_PARAGRAPH_TOKENS = frozenset({"paragraph_open", "inline", "paragraph_close"})
_PLAIN_INLINE = frozenset({"text", "softbreak", "hardbreak"})
_BREAK = re.compile(r" ?\n+ ?")
# 정규식으로 고치는 어미. 인라인 코드 밖에서만 적용한다
_PHRASE_FIXES = (
    (re.compile(r"하십시오"), "한다"),
    (re.compile(r"(?<=\S)라고 볼 수 있다"), "다"),
    (re.compile(r"(?<=\S)인 상황이다"), "이다"),
)


@dataclass
class _Document:
    """문단 위치가 패치로 밀리지 않게 조각 배열로 든다. text() 는 조각을 줄바꿈으로 잇는다."""

    pieces: list[str]
    paragraph_positions: list[int]

    @classmethod
    def parse(cls, markdown: str) -> "_Document":
        lines = markdown.split("\n")
        pieces: list[str] = []
        positions: list[int] = []
        cursor = 0
        for start, end, _ in top_level_paragraphs(markdown):
            if start > cursor:
                pieces.append("\n".join(lines[cursor:start]))
            positions.append(len(pieces))
            pieces.append("\n".join(lines[start:end]))
            cursor = end
        if cursor < len(lines):
            pieces.append("\n".join(lines[cursor:]))
        return cls(pieces, positions)

    def text(self) -> str:
        return "\n".join(self.pieces)

    def paragraphs(self) -> list[ParagraphForPolish]:
        return [
            ParagraphForPolish(index=index, text=self.pieces[position])
            for index, position in enumerate(self.paragraph_positions)
        ]


def _only_breaks_differ(before: str, after: str) -> bool:
    """줄바꿈과 그에 붙은 공백 하나만 다른가. 문단 나누기는 공백 하나를 빈 줄로 바꾸므로 이것까지 허용한다.

    공백 전체를 무시하면 띄어쓰기로 뜻이 바뀌는 한국어 문장과 인라인 코드가 깨진다.
    """
    return _BREAK.sub(" ", before) == _BREAK.sub(" ", after)


def _signature(text: str) -> tuple[list[str], ...]:
    """다듬기 전후에 같아야 하는 것: 인라인 코드, URL, 앵커, 숫자, 라틴 토큰의 순서와 부정 표현의 개수.

    순서까지 본다. "`A`가 `B`를" 맞바꾸거나 "3초 뒤 5번"을 "5초 뒤 3번"으로 바꿔도 개수는 같기 때문이다. 문장을 고치다 순서가 바뀐 패치도 함께 버려지지만,
    버리면 원문이 남을 뿐이다. 이것으로도 뜻을 보장하지 못하므로 diff 를 사람에게 보인다.
    """
    urls = _URL.findall(text)
    prose = _URL.sub("", _INLINE_CODE.sub("", text))
    return (
        _INLINE_CODE.findall(text),
        urls,
        ANCHOR_PATTERN.findall(text),
        _NUMBER.findall(prose),
        _LATIN.findall(prose),
        sorted(_NEGATION.findall(prose)),
    )


def _inline_markup(text: str) -> Counter[str]:
    """굵게·기울임·링크·인라인 코드의 개수. 빈 줄이 `**…**` 를 가르면 서식이 글자로 남아 여기서 줄어든다."""
    return Counter(
        child.type
        for token in parse_markdown(text)
        for child in token.children or []
        if child.type not in _PLAIN_INLINE
    )


def _preserved_violation(before: str, after: str) -> str | None:
    """모든 패치의 공통 조건. 문단을 문단으로만 바꾸고 보존 대상을 건드리지 않는다."""
    if not after.strip():
        return "빈 패치"
    if any(token.type not in _PARAGRAPH_TOKENS for token in parse_markdown(after)):
        return "문단이 아닌 구조"
    if _inline_markup(before) != _inline_markup(after):
        return "서식 변경"
    return "보존 대상 변경" if _signature(before) != _signature(after) else None


def _format_violation(before: str, after: str) -> str | None:
    if not _only_breaks_differ(before, after):
        return "줄바꿈 외의 변경"
    return _preserved_violation(before, after)


def _tone_violation(before: str, after: str) -> str | None:
    if abs(len(after) - len(before)) > max(_LENGTH_SLACK_CHARS, len(before) * _LENGTH_SLACK_RATIO):
        return "길이 변화"
    return _preserved_violation(before, after)


def _fix_phrases(text: str) -> str:
    pieces = re.split(f"({_INLINE_CODE.pattern})", text)
    for index in range(0, len(pieces), 2):  # 홀수 칸이 인라인 코드다
        for pattern, replacement in _PHRASE_FIXES:
            pieces[index] = pattern.sub(replacement, pieces[index])
    return "".join(pieces)


class PolishingService:
    def __init__(self, *, agent_call_service: AgentCallService, gate: ValidationGate, settings: Settings) -> None:
        self._calls = agent_call_service
        self._gate = gate
        self._settings = settings

    async def polish(self, draft: PostDraft, publishing_round: PublishingRound) -> None:
        """다듬기 단계를 끝낸다. 호출 실패는 그 호출만 건너뛴다. 돌아오면 post_review_stage 는 항상 FINAL_READ 다."""
        base = draft.current_version
        level = draft.topic.target_level
        record = PolishRecord(base_version=base.version_number)
        routes = DraftRoutes(publishing_round, draft)
        document = _Document.parse(base.content_markdown)
        last_call_id = ""

        patches = [(each.index, _fix_phrases(each.text)) for each in document.paragraphs()]
        self._apply(document, "regex", patches, _preserved_violation, level, record)
        record.long_paragraphs = [
            f"{each.text[:40]}… ({len(each.text)}자)"
            for each in document.paragraphs()
            if len(each.text) >= _LONG_PARAGRAPH_CHARS
        ]

        steps: list[
            tuple[CallKind, Callable[[], CallModel], Callable[[], Any], Callable[[str, str], str | None], str]
        ] = [
            (
                CallKind.POLISH_FORMAT,
                lambda: routes.format_polisher,
                lambda: PolishFormatInput(paragraphs=document.paragraphs()),
                _format_violation,
                "format",
            ),
            (
                CallKind.POLISH_TONE,
                lambda: routes.tone_polisher,
                lambda: PolishToneInput(
                    paragraphs=document.paragraphs(),
                    tone_markers=persona_of(publishing_round, draft.author_slot).tone_markers,
                ),
                _tone_violation,
                "tone",
            ),
        ]
        for kind, select_model, build_input, violation, source in steps:
            try:
                model = select_model()
                result = await self._calls.call(
                    kind=kind,
                    round_id=draft.round_id,
                    binding=model,
                    messages=render_messages(build_input(), self._settings.prompt_version),
                    schema=PolishOutput,
                    draft=draft,
                    target_version=base.version_number,
                )
            except Exception as exc:  # 다듬기 실패는 글을 멈추지 않는다(재시도 소진·예산 초과·벤더 거부 전부)
                logger.warning("draft %s skipped %s: %s", draft.draft_id, kind, exc)
                record.skipped.append(f"{kind}: {type(exc).__name__}")
                continue
            applied = self._apply(
                document, source, [(each.index, each.text) for each in result.output.patches], violation, level, record
            )
            if applied:
                last_call_id = result.call_id
                record.model_ids.append(model.model_id)

        polished = document.text()
        if record.applied == 0:
            draft.finish_polish(record)
            return
        errors = self._gate.check_markdown(polished, level)
        if errors:
            record.gate_errors = errors
            record.applied = 0
            record.model_ids = []
            draft.finish_polish(record)
            return
        draft.attach_polished_version(
            content_markdown=polished,
            section_ids=find_anchors(polished),
            record=record,
            call_id=last_call_id or f"{draft.draft_id}:polish:regex",
        )

    def _apply(
        self,
        document: _Document,
        source: str,
        patches: list[tuple[int, str]],
        violation: Callable[[str, str], str | None],
        level: int | None,
        record: PolishRecord,
    ) -> int:
        """패치를 문단 순서로 하나씩 적용한다. 안전장치나 게이트를 어기면 그 패치만 버린다. 적용한 수를 돌려준다."""
        applied = 0
        for index, text in sorted(patches, key=lambda patch: patch[0]):
            if not 0 <= index < len(document.paragraph_positions):
                record.discarded.append(DiscardedPatch(source=source, index=index, reason="없는 문단"))
                continue
            position = document.paragraph_positions[index]
            before = document.pieces[position]
            if text == before:
                continue
            reason = violation(before, text)
            if reason is None:
                document.pieces[position] = text
                errors = self._gate.check_markdown(document.text(), level)
                if errors:
                    document.pieces[position] = before
                    reason = f"게이트: {errors[0].split(':')[0]}"
            if reason is not None:
                record.discarded.append(DiscardedPatch(source=source, index=index, reason=reason))
                continue
            applied += 1
        record.applied += applied
        return applied
