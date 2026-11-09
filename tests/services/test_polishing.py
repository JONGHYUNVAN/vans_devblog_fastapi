"""다듬기 테스트: 안전장치, 패치 적용, 실패가 글을 멈추지 않는지, 다듬은 버전의 발행 (설계 04 문서 발행 전 단계)."""

import re

import pytest

from app.agents.contracts import ParagraphForPolish
from app.core.exceptions import LLMTransportError
from app.models.enums import AgentSlot, PostStatus, RevisionCause
from app.models.pipeline import DEFAULT_TELL_PATTERNS, PolishParameters, TellPattern
from app.services.pipeline_views import build_escalation_summary
from app.services.polishing import _Document, _find_tells, _fix_phrases, _tone_violation
from app.services.publishing import build_publish_note
from app.services.validation import ValidationGate
from tests.factories import assess_level_output, draft_output, final_read_output, polish_output, review_output
from tests.fakes import PipelineHarness

pytestmark = pytest.mark.anyio

A = AgentSlot.A
BODY = (
    "<!-- sec:intro -->\n첫 문장이다. 둘째 문장이다.\n\n<!-- sec:body-1 -->\n값을 확인한다. `retry` 를 2번 호출하지 않는다.\n\n"
    "<!-- sec:code-1 -->\n```python\nprint(1)\n```\n\n<!-- sec:tradeoff -->\n트레이드오프\n\n<!-- sec:conclusion -->\n결론\n"
)
FIRST, SECOND = 0, 1


def _scripts(**overrides) -> dict:
    return {
        "WRITE_DRAFT": [draft_output(BODY)],
        "REVIEW_FULL": review_output(),
        "ASSESS_LEVEL": assess_level_output(quote="트레이드오프"),
    } | overrides


async def _drive(**overrides):
    harness = PipelineHarness(_scripts(**overrides))
    await harness.seed_round()
    return harness, await harness.drive(A)


# ---------- 순수 함수 ----------


@pytest.mark.parametrize(
    "markdown",
    [
        BODY,
        "문단 하나",
        "문단\n\n둘째 문단\n",
        "# 제목\n\n본문\n\n- 목록\n\n끝\n",
        "<!-- sec:intro -->\n본문\n\n\n뒤\n",
    ],
)
def test_document_round_trips_when_nothing_is_patched(markdown):
    assert _Document.parse(markdown).text() == markdown


def test_document_lists_only_top_level_paragraphs():
    paragraphs = _Document.parse("# 제목\n\n본문\n\n- 목록\n\n```python\nx\n```\n\n> 인용\n\n끝\n").paragraphs()

    assert [paragraph.text for paragraph in paragraphs] == ["본문", "끝"]


@pytest.mark.parametrize(
    ("before", "after"),
    [
        ("`retry` 를 쓴다", "`retries` 를 쓴다"),  # 인라인 코드
        ("3번 부른다", "4번 부른다"),  # 숫자
        ("[문서](https://a.test) 참고", "[문서](https://b.test) 참고"),  # URL
        ("막지 않는다", "막는다"),  # 부정 표현
        ("<!-- sec:body-1 --> 본문", "본문"),  # 앵커
        ("본문", "```\n코드\n```"),  # 코드 펜스
        ("본문", "  "),  # 빈 패치
    ],
)
def test_tone_safeguards_reject_changes_to_protected_tokens(before, after):
    assert _tone_violation(before, after) is not None


@pytest.mark.parametrize(
    ("before", "after"),
    [
        ("`A`가 `B`를 부른다", "`B`가 `A`를 부른다"),  # 인라인 코드 순서
        ("3초 뒤 5번 부른다", "5초 뒤 3번 부른다"),  # 숫자 순서
        ("at-least-once 로 보낸다", "at-most-once 로 보낸다"),  # 코드 표시 없는 영문 용어
        ("이것은 버그가 아니다", "이것은 버그다"),  # 부정 표현
        ("그렇게 하면 안 된다", "그렇게 하면 된다"),
        ("본문이다", "## 제목\n\n본문이다"),  # 문단이 아닌 구조
        ("**굵은 글** 이다", "굵은 글 이다"),  # 서식
    ],
)
def test_tone_safeguards_reject_patches_that_keep_counts_but_change_meaning(before, after):
    assert _tone_violation(before, after) is not None


def test_tone_safeguards_reject_large_length_changes():
    paragraph = "첫 문장이다. " * 20

    assert _tone_violation(paragraph, "첫 문장이다.") == "길이 변화"
    assert _tone_violation("짧다.", "짧다. " + "새로 더한 주장이다. " * 10) == "길이 변화"


def test_tone_safeguards_allow_a_sentence_rewrite_that_keeps_protected_tokens():
    before = "동시 중복 요청은 기본 키 제약이 두 번째 insert를 막는다"
    after = "같은 요청이 동시에 들어와도 기본 키 제약 때문에 두 번째 insert는 실패한다"

    assert _tone_violation(before, after) is None


def test_contrastive_anira_is_not_counted_as_negation():  # D-90
    before = "순환 참조는 생성 순서의 문제가 아니라 경계의 문제다"

    assert _tone_violation(before, "순환 참조는 생성 순서보다 경계의 문제다") is None
    assert _tone_violation("이것은 버그가 아니다", "이것은 버그다") is not None  # 극성을 바꾸는 부정은 그대로 막는다
    assert _tone_violation("이것은 버그가 아닙니다.", "이것은 버그입니다.") is not None  # 한 음절 "아닙"도 센다
    assert (
        _tone_violation("취향이 아니라 경로를 닫는 수단입니다.", "취향 문제가 아닌, 경로를 닫는 수단입니다.")
        is not None
    )
    assert _tone_violation("A가 아니고 B입니다.", "A가 아닌 B입니다.") is None  # 활용만 바뀐 부정은 같은 수다


def test_find_tells_reports_matches_outside_inline_code():
    tells = [(each, re.compile(each.pattern)) for each in DEFAULT_TELL_PATTERNS]
    found = _find_tells([ParagraphForPolish(index=3, text="이 봉합은 `봉합` 함수와 다르다")], tells)

    assert [(each.index, each.rule, each.match) for each in found] == [(3, "D-14", "봉합")]


def test_fix_phrases_leaves_inline_code_alone():
    assert _fix_phrases("값을 확인하십시오. `하십시오` 는 그대로다") == "값을 확인한다. `하십시오` 는 그대로다"
    assert _fix_phrases("버그라고 볼 수 있다. 장애인 상황이다.") == "버그다. 장애이다."


# ---------- 파이프라인 ----------


async def test_patches_make_a_new_version_that_is_not_reviewed_again():
    tone = "첫 문장이다. 둘째 문장으로 이어진다."
    harness, draft = await _drive(POLISH_TONE=[polish_output((FIRST, tone))])

    assert draft.status == PostStatus.READY_TO_PUBLISH
    polished = draft.current_version
    assert (polished.version_number, polished.origin_cause, polished.passed_validation) == (
        2,
        RevisionCause.POLISH,
        True,
    )
    assert tone in polished.content_markdown
    assert (
        "값을 확인한다." in polished.content_markdown
    )  # 원문 그대로. 게이트(D-85)가 명령형을 막아 정규식이 고칠 문장이 초안에 남지 않는다
    assert draft.polish is not None
    assert draft.polish.applied == 1  # 문체 패치 하나뿐
    assert draft.polish.model_ids == ["fake-b-1"]  # 작성 벤더(fake-a)가 아닌 모델
    assert harness.llm.kinds.count("REVIEW_FULL") == 2
    # 전체 읽기와 수준 평가는 다듬은 본문을 본다
    final_read_call = next(
        call for call in await harness.calls.list_by_draft(draft.draft_id) if call.kind == "FINAL_READ"
    )
    assert tone in final_read_call.messages[1].content
    assert draft.final_read is not None and draft.final_read.target_version == 2


async def test_only_the_violating_patch_is_discarded():
    good = "첫 문장이다. 둘째 문장으로 이어진다."
    bad = "값을 3번 확인한다."  # 숫자와 인라인 코드를 바꿨다
    _, draft = await _drive(POLISH_TONE=[polish_output((FIRST, good), (SECOND, bad), (99, "없는 문단"))])

    assert good in draft.current_version.content_markdown
    assert draft.polish is not None
    assert {each.reason for each in draft.polish.discarded} == {"보존 대상 변경", "없는 문단"}


async def test_no_patches_make_no_new_version():
    plain = BODY.replace("값을 확인하십시오.", "값을 확인한다.")
    _, draft = await _drive(WRITE_DRAFT=[draft_output(plain)])

    assert len(draft.versions) == 1
    assert draft.polish is not None and draft.polish.applied == 0
    assert draft.status == PostStatus.READY_TO_PUBLISH


async def test_failed_polish_calls_are_skipped_and_never_stop_the_post():
    _, draft = await _drive(POLISH_TONE=[LLMTransportError("503"), LLMTransportError("503")])

    assert draft.status == PostStatus.READY_TO_PUBLISH
    assert draft.polish is not None
    assert draft.polish.skipped == ["POLISH_TONE: LLMRetriesExhaustedError"]


async def test_polished_body_that_fails_the_gate_at_the_end_is_discarded(monkeypatch):
    harness = PipelineHarness(_scripts(POLISH_TONE=[polish_output((FIRST, "첫 문장이다. 둘째 문장으로 이어진다."))]))
    await harness.seed_round()
    calls = {"count": 0}
    original = ValidationGate.check_markdown  # 게이트는 걸음마다 라운드 버전의 조절 값으로 만들어지므로 클래스를 바꾼다

    def failing_at_the_end(self, markdown, level):
        calls["count"] += 1
        if "이어진다" in markdown and calls["count"] > 2:  # 1: VALIDATING, 2: 패치 뒤 문단 검사, 3: 합친 본문 검사
            return ["본문이 너무 길다"]
        return original(self, markdown, level)

    monkeypatch.setattr(ValidationGate, "check_markdown", failing_at_the_end)

    draft = await harness.drive(A)

    assert draft.status == PostStatus.READY_TO_PUBLISH
    assert len(draft.versions) == 1  # 리뷰를 통과한 버전이 그대로 발행본이다
    assert draft.polish is not None
    assert (draft.polish.applied, draft.polish.gate_errors) == (0, ["본문이 너무 길다"])


async def test_tells_reach_the_polisher_from_min_count_and_are_counted_before_and_after():  # D-90
    body = BODY.replace("첫 문장이다. 둘째 문장이다.", "이 봉합은 임시다. 순서가 아니라 경계가 문제다.")
    patched = "이 우회는 임시다. 순서가 아니라 경계가 문제다."
    # 기본 패턴은 모두 min_count 1 이라 운영에서 넣을 수 있는 min_count 2 패턴을 하나 더한다
    contrast = TellPattern(id="C-8", label="부정 대구", pattern=r"아니라(?=[\s,])", min_count=2)
    harness = PipelineHarness(
        _scripts(WRITE_DRAFT=[draft_output(body)], POLISH_TONE=[polish_output((FIRST, patched))]),
        polish=PolishParameters(tell_patterns=[*DEFAULT_TELL_PATTERNS, contrast]),
    )
    await harness.seed_round()
    draft = await harness.drive(A)

    call = next(call for call in await harness.calls.list_by_draft(draft.draft_id) if call.kind == "POLISH_TONE")
    sent = call.messages[1].content
    assert '"rule": "D-14"' in sent and '"match": "봉합"' in sent
    assert '"rule": "C-8"' not in sent  # 부정 대구는 한 번뿐이라 min_count(2) 미만이다
    assert draft.polish is not None and draft.polish.applied == 1
    assert (draft.polish.tells_before, draft.polish.tells_after) == ({"D-14": 1, "C-8": 1}, {"C-8": 1})


async def test_long_paragraphs_are_reported_not_rewritten():
    long_paragraph = "긴 문장이다. " * 40
    _, draft = await _drive(WRITE_DRAFT=[draft_output(BODY.replace("트레이드오프\n", f"{long_paragraph}\n"))])

    assert draft.polish is not None
    assert len(draft.polish.long_paragraphs) == 1


async def test_note_names_the_polisher_and_summary_shows_the_diff():
    harness, draft = await _drive(POLISH_TONE=[polish_output((FIRST, "첫 문장이다. 둘째 문장으로 이어진다."))])
    publishing_round = await harness.round_service.get(draft.round_id)

    note = build_publish_note(draft, publishing_round)
    summary = build_escalation_summary(draft)

    assert len(draft.versions) == 2
    assert note.endswith("- 다듬기: fake-b-1\n")  # 다듬기 패치가 적용됐을 때만 줄이 생긴다
    assert (summary.final_version, summary.diff) == (2, "")
    assert "+첫 문장이다. 둘째 문장으로 이어진다." in summary.polish_diff


async def test_blocking_final_read_after_polish_escalates_with_the_polished_body_available():
    finding = {"kind": "BODY_CODE_CONTRADICTION", "section_id": "body-1", "quote": "확인한다", "comment": "어긋난다"}
    _, draft = await _drive(
        POLISH_TONE=[polish_output((FIRST, "첫 문장이다. 둘째 문장으로 이어진다."))],
        FINAL_READ=[final_read_output(finding)],
    )

    assert draft.status == PostStatus.ESCALATED
    assert draft.publishable_version is not None
    assert draft.publishable_version.origin_cause == RevisionCause.POLISH
