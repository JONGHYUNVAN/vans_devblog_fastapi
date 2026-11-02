"""CLI 보조 함수 테스트: `agents start-round --wait` 의 종료 조건."""

from cli.__main__ import _is_waiting_on_human


def _status(round_status: str, *drafts: tuple[str, bool]) -> dict:
    return {
        "status": round_status,
        "drafts": [{"status": status, "awaits_approval": awaits} for status, awaits in drafts],
    }


def test_wait_ends_on_round_end_or_when_every_draft_waits_on_a_human():
    assert _is_waiting_on_human(_status("COMPLETED"))
    assert _is_waiting_on_human(_status("ABORTED"))
    assert not _is_waiting_on_human(_status("TOPIC_SELECTION"))
    assert not _is_waiting_on_human(_status("IN_PROGRESS"))
    assert _is_waiting_on_human(
        _status("IN_PROGRESS", ("READY_TO_PUBLISH", True), ("ESCALATED", False), ("FAILED", False))
    )
    assert _is_waiting_on_human(_status("IN_PROGRESS", ("PUBLISHED", False), ("REJECTED", False)))


def test_wait_keeps_polling_while_the_runner_still_publishes():
    # 승인됐거나 토글이 꺼진 READY_TO_PUBLISH 는 러너가 발행 중이다 (06 문서 3절 CLI 표)
    assert not _is_waiting_on_human(_status("IN_PROGRESS", ("READY_TO_PUBLISH", False), ("PUBLISHED", False)))
    assert not _is_waiting_on_human(_status("IN_PROGRESS", ("IN_REVIEW", False), ("ESCALATED", False)))
