"""발행 전 다듬기: 정규식 어미 치환과 문체(작성 벤더가 아닌 모델). 뜻을 바꾸지 않는 표현만 고친다. 줄바꿈 다듬기(POLISH_FORMAT)는 v4 에서 뺐다.

모델이 글 전체를 다시 쓰지 못하게 문단 단위 패치만 받고, 안전장치를 통과한 패치만 적용한다. 압축된 서사를 풀어 쓰는 일은 하지 않는다.
다듬기 실패는 글을 멈추지 않는다. 수정 루프로 돌아가지 않고, 패치가 없으면 새 버전도 만들지 않는다. 호출·게이트 실패는 PolishRecord 에 남는다.
"""

import logging
import re
from collections import Counter
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from functools import partial

from app.agents.builders import render_messages
from app.agents.contracts import ParagraphForPolish, PolishFinding, PolishOutput, PolishToneInput
from app.models.enums import CallKind
from app.models.pipeline import PipelineVersion, PolishParameters, TellPattern
from app.models.post_draft import DiscardedPatch, PolishRecord, PostDraft
from app.models.publishing_round import PublishingRound
from app.models.sections import ANCHOR_PATTERN, find_anchors
from app.models.tiptap import parse_markdown, top_level_paragraphs
from app.services.agent_calls import AgentCallService
from app.services.routing import DraftRoutes
from app.services.validation import ValidationGate

logger = logging.getLogger(__name__)

_INLINE_CODE = re.compile(r"`[^`\n]+`")
_NUMBER = re.compile(r"\d+(?:[.,]\d+)*")
_URL = re.compile(r"https?://[^\s)\]]+")
_LATIN = re.compile(r"[A-Za-z][A-Za-z0-9_+./-]*")
# "A가 아니라 B" 의 대조 연결 "아니라"는 세지 않는다. 부정 대구(C-8)를 고치면 이 "아니"가 빠지는데, 극성을 바꾸는 것이 아니다 (D-90)
_NEGATION = re.compile(r"[않못없]|아니(?!라)|(?<![가-힣])안(?= )|말아|말고")
_PARAGRAPH_TOKENS = frozenset({"paragraph_open", "inline", "paragraph_close"})
_PLAIN_INLINE = frozenset({"text", "softbreak", "hardbreak"})
# 정규식 어미 치환·허용폭·긴 문단 기준의 기본값. 실행 때는 라운드 버전 문서의 polish 조절 값을 쓴다 (09 문서 2.4절)
_DEFAULTS = PolishParameters()
_PHRASE_FIXES = tuple((re.compile(pattern), replacement) for pattern, replacement in _DEFAULTS.phrase_fixes)


@dataclass
class _Document:
    """문단 위치가 패치로 밀리지 않게 조각 배열로 든다. text() 는 조각을 줄바꿈으로 잇는다."""

    pieces: list[str]
    paragraph_positions: list[int]

    @classmethod
    def parse(cls, markdown: str) -> "_Document":
        """마크다운을 최상위 문단과 그 사이 조각으로 쪼갠다. 코드 블록·목록·제목은 문단이 아니라 사이 조각에 남아 패치 대상이 아니다."""
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
        """조각을 다시 이어 본문으로. 패치가 없으면 원문과 같다."""
        return "\n".join(self.pieces)

    def paragraphs(self) -> list[ParagraphForPolish]:
        """모델에 보낼 문단 목록. index 는 문단 순번이고 패치의 index 가 이것을 가리킨다."""
        return [
            ParagraphForPolish(index=index, text=self.pieces[position])
            for index, position in enumerate(self.paragraph_positions)
        ]


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
    # 인라인 코드를 지우면 서식 개수도 줄어든다. 사유 집계가 내용 훼손을 서식 문제로 세지 않게 보존 대상을 먼저 본다
    if _signature(before) != _signature(after):
        return "보존 대상 변경"
    return "서식 변경" if _inline_markup(before) != _inline_markup(after) else None


def _tone_violation(
    before: str,
    after: str,
    *,
    slack_chars: int = _DEFAULTS.length_slack_chars,
    slack_ratio: float = _DEFAULTS.length_slack_ratio,
) -> str | None:
    if abs(len(after) - len(before)) > max(slack_chars, len(before) * slack_ratio):
        return "길이 변화"
    return _preserved_violation(before, after)


def _find_tells(
    paragraphs: Iterable[ParagraphForPolish], patterns: Iterable[tuple[TellPattern, re.Pattern[str]]]
) -> list[PolishFinding]:
    """문단 산문(인라인 코드 제외)에서 AI 문체 패턴을 모두 찾는다 (D-90). 고치지 않는다. 고칠지는 다듬기 모델이 정한다."""
    found: list[PolishFinding] = []
    for paragraph in paragraphs:
        prose = _INLINE_CODE.sub(" ", paragraph.text)
        for tell, compiled in patterns:
            found += [
                PolishFinding(index=paragraph.index, rule=tell.id, label=tell.label, match=match.group(0))
                for match in compiled.finditer(prose)
            ]
    return found


def _count_tells(found: Iterable[PolishFinding]) -> dict[str, int]:
    return dict(Counter(each.rule for each in found))


def _fix_phrases(text: str, fixes: Iterable[tuple[re.Pattern[str], str]] = _PHRASE_FIXES) -> str:
    pieces = re.split(f"({_INLINE_CODE.pattern})", text)
    for index in range(0, len(pieces), 2):  # 홀수 칸이 인라인 코드다
        for pattern, replacement in fixes:
            pieces[index] = pattern.sub(replacement, pieces[index])
    return "".join(pieces)


class PolishingService:
    """POST_REVIEW 의 다듬기 단계. 호출 서비스만 들고, 게이트·정규식·허용폭은 걸음마다 라운드 버전의 조절 값으로 만든다."""

    def __init__(self, *, agent_call_service: AgentCallService) -> None:
        self._calls = agent_call_service

    async def polish(self, draft: PostDraft, publishing_round: PublishingRound, version: PipelineVersion) -> None:
        """다듬기 단계를 끝낸다. 호출 실패는 그 호출만 건너뛴다. 돌아오면 post_review_stage 는 항상 FINAL_READ 다.

        정규식 표·허용폭·게이트 임계는 라운드 버전 문서의 조절 값이다.
        """
        parameters = version.parameters
        polish = parameters.polish
        gate = ValidationGate.from_parameters(parameters)
        base = draft.current_version
        level = draft.topic.target_level
        record = PolishRecord(base_version=base.version_number)
        routes = DraftRoutes(publishing_round, draft, version.personas)
        document = _Document.parse(base.content_markdown)
        last_call_id = ""

        # 다듬기 전 패턴. 글 전체 횟수가 min_count 이상인 규칙만 모델에 알린다(부정 대구는 한 번은 사람도 쓴다)
        tells = [(each, re.compile(each.pattern)) for each in polish.tell_patterns]
        found = _find_tells(document.paragraphs(), tells)
        record.tells_before = _count_tells(found)
        min_count = {each.id: each.min_count for each in polish.tell_patterns}
        findings = [each for each in found if record.tells_before[each.rule] >= min_count[each.rule]]

        fixes = [(re.compile(pattern), replacement) for pattern, replacement in polish.phrase_fixes]
        patches = [(each.index, _fix_phrases(each.text, fixes)) for each in document.paragraphs()]
        self._apply(document, "regex", patches, _preserved_violation, level, record, gate)
        record.long_paragraphs = [
            f"{each.text[:40]}… ({len(each.text)}자)"
            for each in document.paragraphs()
            if len(each.text) >= polish.long_paragraph_chars
        ]

        try:
            model = routes.tone_polisher
            result = await self._calls.call(
                kind=CallKind.POLISH_TONE,
                round_id=draft.round_id,
                binding=model,
                messages=render_messages(
                    PolishToneInput(paragraphs=document.paragraphs(), findings=findings), version.prompts, model
                ),
                schema=PolishOutput,
                draft=draft,
                target_version=base.version_number,
                max_attempts=parameters.max_llm_retries,
                pipeline_version=version.version_id,
            )
        except Exception as exc:  # 다듬기 실패는 글을 멈추지 않는다(재시도 소진·예산 초과·벤더 거부 전부)
            logger.warning("draft %s skipped %s: %s", draft.draft_id, CallKind.POLISH_TONE, exc)
            record.skipped.append(f"{CallKind.POLISH_TONE}: {type(exc).__name__}")
        else:
            applied = self._apply(
                document,
                "tone",
                [(each.index, each.text) for each in result.output.patches],
                partial(_tone_violation, slack_chars=polish.length_slack_chars, slack_ratio=polish.length_slack_ratio),
                level,
                record,
                gate,
            )
            if applied:
                last_call_id = result.call_id
                record.model_ids.append(model.model_id)

        polished = document.text()
        record.tells_after = dict(record.tells_before)  # 새 버전을 만들지 않으면 본문은 그대로다
        if record.applied == 0:
            draft.finish_polish(record)
            return
        errors = gate.check_markdown(polished, level)
        if errors:
            record.gate_errors = errors
            record.applied = 0
            record.model_ids = []
            draft.finish_polish(record)
            return
        record.tells_after = _count_tells(_find_tells(document.paragraphs(), tells))
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
        gate: ValidationGate,
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
                errors = gate.check_markdown(document.text(), level)
                if errors:
                    document.pieces[position] = before
                    reason = f"게이트: {errors[0].split(':')[0]}"
            if reason is not None:
                record.discarded.append(DiscardedPatch(source=source, index=index, reason=reason))
                continue
            applied += 1
        record.applied += applied
        return applied
