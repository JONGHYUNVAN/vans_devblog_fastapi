"""PublishingRound 상태 전이 테스트 (설계 03 문서 3절)."""

import pytest
from pydantic import ValidationError

from app.core.exceptions import InvalidTransitionError
from app.models.enums import AgentSlot, RoundAbortReason, RoundStatus
from app.models.model_binding import ModelBinding
from app.models.publishing_round import LockedTopic, PublishingRound, TopicAttempt
from app.models.topic import CategoryOption
from tests.factories import make_topic


def _binding(persona_id: str) -> ModelBinding:
    return ModelBinding(provider="fake", model_id="fake-1", persona_id=persona_id, max_output_tokens=4000)


def _round(**overrides) -> PublishingRound:
    fields = {
        "round_id": "round-1",
        "round_seq": 1,
        "slot_bindings": {
            AgentSlot.A: _binding("architect"),
            AgentSlot.B: _binding("systems_engineer"),
            AgentSlot.C: _binding("troubleshooter"),
        },
        "categories": [
            CategoryOption(main_category_id="main-1", main_label="Backend", sub_category_id="sub-1", sub_label="DB")
        ],
    }
    return PublishingRound(**(fields | overrides))


def _locked_attempt(*slots: AgentSlot) -> TopicAttempt:
    return TopicAttempt(
        attempt_number=1,
        locked={
            slot: LockedTopic(slot=slot, source_attempt=1, proposal_index=0, proposal=make_topic()) for slot in slots
        },
    )


def test_round_starts_in_topic_selection():
    assert _round().status == RoundStatus.TOPIC_SELECTION


def test_round_rejects_missing_slot_binding():
    with pytest.raises(ValidationError, match="every slot"):
        _round(slot_bindings={AgentSlot.A: _binding("architect")})


def test_lock_topics_moves_to_in_progress_with_deterministic_draft_ids():
    publishing_round = _round()
    publishing_round.topic_attempts.append(_locked_attempt(*AgentSlot))

    publishing_round.lock_topics()

    assert publishing_round.status == RoundStatus.IN_PROGRESS
    assert publishing_round.draft_ids == ["round-1:A", "round-1:B", "round-1:C"]


def test_lock_topics_raises_when_a_slot_is_not_locked():
    publishing_round = _round()
    publishing_round.topic_attempts.append(_locked_attempt(AgentSlot.A, AgentSlot.B))

    with pytest.raises(InvalidTransitionError, match="every slot"):
        publishing_round.lock_topics()


def test_abort_records_reason():
    publishing_round = _round()

    publishing_round.abort(RoundAbortReason.TOPIC_ATTEMPTS_EXHAUSTED)

    assert publishing_round.status == RoundStatus.ABORTED
    assert publishing_round.abort_reason == RoundAbortReason.TOPIC_ATTEMPTS_EXHAUSTED


def test_complete_raises_after_abort():
    # 시나리오 ⑩: 중단이 먼저 ABORTED 를 쓰면 완료 확인이 COMPLETED 로 닫지 못한다 (07 문서 D-34)
    publishing_round = _round(status=RoundStatus.IN_PROGRESS)
    publishing_round.abort(RoundAbortReason.MANUAL)

    with pytest.raises(InvalidTransitionError):
        publishing_round.complete()


def test_abort_raises_when_already_completed():
    publishing_round = _round(status=RoundStatus.IN_PROGRESS)
    publishing_round.complete()

    with pytest.raises(InvalidTransitionError):
        publishing_round.abort(RoundAbortReason.MANUAL)
