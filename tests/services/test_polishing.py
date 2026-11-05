"""다듬기 테스트: 안전장치, 패치 적용, 실패가 글을 멈추지 않는지, 다듬은 버전의 발행 (설계 04 문서 발행 전 단계)."""

import pytest

from app.core.exceptions import LLMTransportError
from app.models.enums import AgentSlot, PostStatus, RevisionCause
from app.services.pipeline_views import build_escalation_summary
from app.services.polishing import _Document, _fix_phrases, _format_violation, _only_breaks_differ, _tone_violation
from app.services.publishing import build_peer_review_note
from tests.factories import assess_level_output, draft_output, final_read_output, polish_output, review_output
from tests.fakes import PipelineHarness

pytestmark = pytest.mark.anyio

A = AgentSlot.A
BODY = (
    "<!-- sec:intro -->\n첫 문장이다. 둘째 문장이다.\n\n<!-- sec:body-1 -->\n값을 확인하십시오. `retry` 를 2번 호출하지 않는다.\n\n"
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


def test_only_breaks_differ_allows_splitting_a_paragraph_but_not_other_edits():
    assert _only_breaks_differ("문장 하나. 문장 둘.", "문장 하나.\n\n문장 둘.")
    assert _only_breaks_differ("문장 하나.\n문장 둘.", "문장 하나. 문장 둘.")
    assert not _only_breaks_differ("문장 하나. 문장 둘.", "문장 하나.문장 둘.")  # 공백만 지운 것은 뜻이 바뀔 수 있다
    assert not _only_breaks_differ("막지 않는다", "막는다")
    assert not _only_breaks_differ("a  b", "a b")


def test_format_patch_cannot_break_inline_code_across_a_blank_line():
    # 코드 스팬 안의 공백을 빈 줄로 바꾸면 글자는 같아도 코드가 쪼개진다
    assert _format_violation("`a b` 다", "`a\n\nb` 다") is not None


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


def test_format_patch_cannot_split_bold_or_link_text():
    assert _format_violation("**굵은 글** 이다", "**굵은\n\n글** 이다") == "서식 변경"
    assert _format_violation("[링크 글](https://a.test) 이다", "[링크\n\n글](https://a.test) 이다") == "서식 변경"
    assert _format_violation("첫 문장이다. 둘째 문장이다.", "첫 문장이다.\n\n둘째 문장이다.") is None


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
    assert "값을 확인한다." in polished.content_markdown  # 정규식 패치
    assert draft.polish is not None
    assert draft.polish.applied == 2
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


async def test_format_patch_splits_a_paragraph_without_moving_later_indexes():
    split = "첫 문장이다.\n\n둘째 문장이다."
    harness, draft = await _drive(
        POLISH_FORMAT=[polish_output((FIRST, split))],
        POLISH_TONE=lambda call_input: polish_output(
            (call_input["paragraphs"][SECOND]["index"], "값을 확인한다. `retry` 를 2번 호출하지 않는다.")
        ),
    )

    assert split in draft.current_version.content_markdown
    assert draft.polish is not None
    assert draft.polish.model_ids == ["fake-a-1"]  # 줄바꿈은 벤더 제약이 없고, 문체 패치는 내용이 같아 적용되지 않았다
    assert harness.llm.all_kinds.count("POLISH_TONE") == 1


async def test_no_patches_make_no_new_version():
    plain = BODY.replace("값을 확인하십시오.", "값을 확인한다.")
    _, draft = await _drive(WRITE_DRAFT=[draft_output(plain)])

    assert len(draft.versions) == 1
    assert draft.polish is not None and draft.polish.applied == 0
    assert draft.status == PostStatus.READY_TO_PUBLISH


async def test_failed_polish_calls_are_skipped_and_never_stop_the_post():
    _, draft = await _drive(
        POLISH_FORMAT=[LLMTransportError("503"), LLMTransportError("503")],
        POLISH_TONE=[KeyError("no-such-provider")],
    )

    assert draft.status == PostStatus.READY_TO_PUBLISH
    assert draft.polish is not None
    assert draft.polish.skipped == ["POLISH_FORMAT: LLMRetriesExhaustedError", "POLISH_TONE: KeyError"]


async def test_polished_body_that_fails_the_gate_at_the_end_is_discarded(monkeypatch):
    harness = PipelineHarness(_scripts(POLISH_TONE=[polish_output((FIRST, "첫 문장이다. 둘째 문장으로 이어진다."))]))
    await harness.seed_round()
    calls = {"count": 0}
    original = harness.draft_pipeline._gate.check_markdown  # noqa: SLF001

    def failing_at_the_end(markdown, level):
        calls["count"] += 1
        if "이어진다" in markdown and "값을 확인한다" in markdown and calls["count"] > 3:
            return ["본문이 너무 길다"]
        return original(markdown, level)

    monkeypatch.setattr(harness.draft_pipeline._gate, "check_markdown", failing_at_the_end)  # noqa: SLF001

    draft = await harness.drive(A)

    assert draft.status == PostStatus.READY_TO_PUBLISH
    assert len(draft.versions) == 1  # 리뷰를 통과한 버전이 그대로 발행본이다
    assert draft.polish is not None
    assert (draft.polish.applied, draft.polish.gate_errors) == (0, ["본문이 너무 길다"])


async def test_long_paragraphs_are_reported_not_rewritten():
    long_paragraph = "긴 문장이다. " * 40
    _, draft = await _drive(WRITE_DRAFT=[draft_output(BODY.replace("트레이드오프\n", f"{long_paragraph}\n"))])

    assert draft.polish is not None
    assert len(draft.polish.long_paragraphs) == 1


async def test_note_does_not_count_the_polished_version_and_summary_shows_the_diff():
    harness, draft = await _drive(POLISH_TONE=[polish_output((FIRST, "첫 문장이다. 둘째 문장으로 이어진다."))])
    publishing_round = await harness.round_service.get(draft.round_id)

    note = build_peer_review_note(draft, publishing_round)
    summary = build_escalation_summary(draft)

    assert len(draft.versions) == 2
    assert "- 버전: v1 → v1" in note
    assert "fake-b-1 다듬기" in note
    assert (summary.final_version, summary.diff) == (2, "")
    assert "+첫 문장이다. 둘째 문장으로 이어진다." in summary.polish_diff


async def test_blocking_final_read_after_polish_escalates_with_the_polished_body_available():
    finding = {"kind": "BODY_CODE_CONTRADICTION", "section_id": "body-1", "quote": "확인한다", "comment": "어긋난다"}
    _, draft = await _drive(FINAL_READ=[final_read_output(finding)])

    assert draft.status == PostStatus.ESCALATED
    assert draft.publishable_version is not None
    assert draft.publishable_version.origin_cause == RevisionCause.POLISH
