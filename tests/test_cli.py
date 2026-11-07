"""CLI 보조 함수 테스트: `agents start-round --wait` 의 종료 조건."""

from app.schemas.agent_pipeline import DraftProgress, RoundProgress
from cli.__main__ import _is_waiting_on_human


def _progress(round_status: str, *drafts: tuple[str, bool]) -> RoundProgress:
    return RoundProgress(
        round_id="round-1",
        status=round_status,
        abort_reason=None,
        topic_attempts=1,
        drafts=[
            DraftProgress(
                draft_id=f"round-1:{index}",
                status=status,
                awaits_approval=awaits,
                iteration=0,
                versions=1,
                title=None,
                escalation_reason=None,
                failure=None,
                external_post_id=None,
                tokens=0,
            )
            for index, (status, awaits) in enumerate(drafts)
        ],
    )


def test_wait_ends_on_round_end_or_when_every_draft_waits_on_a_human():
    assert _is_waiting_on_human(_progress("COMPLETED"))
    assert _is_waiting_on_human(_progress("ABORTED"))
    assert not _is_waiting_on_human(_progress("TOPIC_SELECTION"))
    assert not _is_waiting_on_human(_progress("IN_PROGRESS"))
    assert _is_waiting_on_human(
        _progress("IN_PROGRESS", ("READY_TO_PUBLISH", True), ("ESCALATED", False), ("FAILED", False))
    )
    assert _is_waiting_on_human(_progress("IN_PROGRESS", ("PUBLISHED", False), ("REJECTED", False)))


def test_wait_keeps_polling_while_the_runner_still_publishes():
    # 승인됐거나 토글이 꺼진 READY_TO_PUBLISH 는 러너가 발행 중이다 (06 문서 3절 CLI 표)
    assert not _is_waiting_on_human(_progress("IN_PROGRESS", ("READY_TO_PUBLISH", False), ("PUBLISHED", False)))
    assert not _is_waiting_on_human(_progress("IN_PROGRESS", ("IN_REVIEW", False), ("ESCALATED", False)))


def test_split_csv_trims_and_drops_empty_items():
    from cli.__main__ import _split_csv

    assert _split_csv("db, backend,,llm ") == ["db", "backend", "llm"]
    assert _split_csv(None) is None and _split_csv("") is None
