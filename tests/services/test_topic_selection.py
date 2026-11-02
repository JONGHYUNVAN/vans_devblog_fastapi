"""TopicSelectionService 테스트 (설계 06 문서 6절 topic_selection 행)."""

import pytest

from app.core.exceptions import LLMTransportError
from app.models.enums import AgentSlot, PostStatus, RoundAbortReason, RoundStatus
from app.models.publishing_round import PublishingRound
from app.repositories.agent_round import PublishingRoundRepository
from tests.factories import make_registry_entry, make_round, proposals_output
from tests.fakes import FakeAsyncDatabase, PipelineHarness, call_input_of

pytestmark = pytest.mark.anyio

A, B, C = AgentSlot.A, AgentSlot.B, AgentSlot.C

# 서로 겹치지 않는 제안 세트 (슬롯마다 3개)
DISTINCT = {
    A: proposals_output(("A 하나", ["a1", "a2", "a3"]), ("A 둘", ["a4", "a5", "a6"]), ("A 셋", ["a7", "a8", "a9"])),
    B: proposals_output(("B 하나", ["b1", "b2", "b3"]), ("B 둘", ["b4", "b5", "b6"]), ("B 셋", ["b7", "b8", "b9"])),
    C: proposals_output(("C 하나", ["c1", "c2", "c3"]), ("C 둘", ["c4", "c5", "c6"]), ("C 셋", ["c7", "c8", "c9"])),
}


def _by_slot(outputs: dict[AgentSlot, str | Exception]):
    """PROPOSE_TOPIC 은 슬롯 순으로 gather 되므로 입력의 페르소나로 슬롯을 가려 답한다."""
    by_persona = {"구조적 아키텍트": A, "시스템 엔지니어": B, "실용주의 트러블슈터": C}

    def build(call_input):
        outcome = outputs[by_persona[call_input["persona"]["display_name"]]]
        if isinstance(outcome, Exception):
            raise outcome
        return outcome

    return build


async def _drive(harness: PipelineHarness, round_id: str = "round-1") -> PublishingRound:
    await harness.topic_selection.drive(round_id)
    publishing_round = await harness.rounds.get(round_id)
    assert publishing_round is not None
    return publishing_round


async def test_no_conflict_locks_first_choices_and_commits_drafts_and_registry():
    harness = PipelineHarness({"PROPOSE_TOPIC": _by_slot(DISTINCT)})
    await harness.rounds.add(make_round())

    publishing_round = await _drive(harness)

    assert publishing_round.status == RoundStatus.IN_PROGRESS
    assert publishing_round.draft_ids == ["round-1:A", "round-1:B", "round-1:C"]
    [attempt] = publishing_round.topic_attempts
    assert {slot: locked.proposal_index for slot, locked in attempt.locked.items()} == {A: 0, B: 0, C: 0}
    drafts = await harness.drafts.list_by_round("round-1")
    assert [(draft.status, draft.token_limit, draft.topic.title) for draft in drafts] == [
        (PostStatus.TOPIC_LOCKED, harness.settings.draft_token_limit, "A 하나"),
        (PostStatus.TOPIC_LOCKED, harness.settings.draft_token_limit, "B 하나"),
        (PostStatus.TOPIC_LOCKED, harness.settings.draft_token_limit, "C 하나"),
    ]
    assert [(entry.entry_id, entry.status) for entry in await harness.topics.list_by_round("round-1")] == [
        ("round-1:A", "RESERVED"),
        ("round-1:B", "RESERVED"),
        ("round-1:C", "RESERVED"),
    ]
    assert harness.llm.kinds == ["PROPOSE_TOPIC"] * 3
    assert all(call.topic_attempt == 1 for call in await harness.calls.list_by_round("round-1"))


async def test_pairwise_conflict_picks_lowest_rank_sum_with_round_seq_tie_break():
    # A#0 과 B#0 이 키워드로 겹친다. 순위 합 1 인 조합은 (A#1,B#0,C#0) 과 (A#0,B#1,C#0). round_seq=1 이면 B 가 우선 슬롯
    a = proposals_output(("A 하나", ["x", "y", "z"]), ("A 둘", ["a4", "a5", "a6"]), ("A 셋", ["a7", "a8", "a9"]))
    b = proposals_output(("B 하나", ["x", "y", "q"]), ("B 둘", ["b4", "b5", "b6"]), ("B 셋", ["b7", "b8", "b9"]))
    harness = PipelineHarness({"PROPOSE_TOPIC": _by_slot({A: a, B: b, C: DISTINCT[C]})})
    await harness.rounds.add(make_round(round_seq=1))

    publishing_round = await _drive(harness)

    [attempt] = publishing_round.topic_attempts
    assert {slot: locked.proposal_index for slot, locked in attempt.locked.items()} == {A: 1, B: 0, C: 0}
    assert [
        (conflict.slot, conflict.proposal_index, conflict.other_slot, conflict.kind) for conflict in attempt.conflicts
    ] == [
        (A, 0, B, "keyword"),
        (B, 0, A, "keyword"),
    ]

    harness = PipelineHarness({"PROPOSE_TOPIC": _by_slot({A: a, B: b, C: DISTINCT[C]})})
    await harness.rounds.add(make_round(round_seq=3))  # 3 % 3 == 0 → A 가 우선 슬롯

    publishing_round = await _drive(harness)

    assert {slot: locked.proposal_index for slot, locked in publishing_round.topic_attempts[0].locked.items()} == {
        A: 0,
        B: 1,
        C: 0,
    }


async def test_registry_conflict_reproposes_only_that_slot_keeping_locked_snapshots():
    taken = proposals_output(
        ("기존 글", ["old1", "old2", "old3"]),
        ("기존 글 둘", ["old4", "old5", "old6"]),
        ("기존 셋", ["old7", "old8", "old9"]),
    )
    harness = PipelineHarness(
        {"PROPOSE_TOPIC": [_by_slot({A: DISTINCT[A], B: DISTINCT[B], C: taken})] * 3 + [DISTINCT[C]]}
    )
    for index, (title, keywords) in enumerate(
        [
            ("기존 글", ["old1", "old2", "old3"]),
            ("기존 글 둘", ["old4", "old5", "old6"]),
            ("기존 셋", ["old7", "old8", "old9"]),
        ]
    ):
        await harness.topics.add_if_missing(make_registry_entry(f"round-0:{index}", title, keywords=keywords))
    await harness.rounds.add(make_round())

    publishing_round = await _drive(harness)

    assert publishing_round.status == RoundStatus.IN_PROGRESS
    first, second = publishing_round.topic_attempts
    assert set(first.locked) == {A, B} and set(first.proposals) == {A, B, C}
    assert {conflict.registry_title for conflict in first.conflicts} == {"기존 글", "기존 글 둘", "기존 셋"}
    assert set(second.proposals) == {C} and second.attempt_number == 2
    assert second.locked[A] == first.locked[A] and second.locked[A].source_attempt == 1
    assert second.locked[C].source_attempt == 2
    assert harness.llm.kinds == ["PROPOSE_TOPIC"] * 4
    retry_input = call_input_of(harness.llm.calls[3].messages)
    assert retry_input["attempt_number"] == 2
    assert "기존 글" in retry_input["conflict_feedback"]
    assert {topic["title"] for topic in retry_input["avoid_topics"]} >= {"A 하나", "B 하나", "기존 글"}


async def test_attempt_limit_aborts_without_registry_entries():
    taken = proposals_output(
        ("기존 글", ["old1", "old2", "old3"]),
        ("기존 글 둘", ["old4", "old5", "old6"]),
        ("기존 셋", ["old7", "old8", "old9"]),
    )
    harness = PipelineHarness({"PROPOSE_TOPIC": [_by_slot({A: DISTINCT[A], B: DISTINCT[B], C: taken})] * 3 + [taken]})
    await harness.topics.add_if_missing(make_registry_entry("round-0:0", "기존 글", keywords=["old1", "old2", "old3"]))
    await harness.topics.add_if_missing(
        make_registry_entry("round-0:1", "기존 글 둘", keywords=["old4", "old5", "old6"])
    )
    await harness.topics.add_if_missing(make_registry_entry("round-0:2", "기존 셋", keywords=["old7", "old8", "old9"]))
    await harness.rounds.add(make_round())

    publishing_round = await _drive(harness)

    assert (publishing_round.status, publishing_round.abort_reason) == (
        RoundStatus.ABORTED,
        RoundAbortReason.TOPIC_ATTEMPTS_EXHAUSTED,
    )
    assert len(publishing_round.topic_attempts) == 2
    assert await harness.topics.list_by_round("round-1") == []
    assert await harness.drafts.list_by_round("round-1") == []


async def test_proposal_retries_exhausted_waits_for_others_then_aborts_llm_failed():
    harness = PipelineHarness(
        {"PROPOSE_TOPIC": _by_slot({A: LLMTransportError("503"), B: DISTINCT[B], C: DISTINCT[C]})}
    )
    await harness.rounds.add(make_round())

    publishing_round = await _drive(harness)

    assert (publishing_round.status, publishing_round.abort_reason) == (
        RoundStatus.ABORTED,
        RoundAbortReason.LLM_FAILED,
    )
    [attempt] = publishing_round.topic_attempts
    assert set(attempt.proposals) == {B, C}
    assert harness.llm.kinds.count("PROPOSE_TOPIC") == 4  # A 2회(재시도 상한) + B + C


async def test_resuming_a_fully_locked_round_writes_the_same_ids_without_proposing_again():
    harness = PipelineHarness({"PROPOSE_TOPIC": _by_slot(DISTINCT)})
    await harness.rounds.add(make_round())
    await _drive(harness)
    publishing_round = await harness.rounds.get("round-1")
    # 확정 쓰기 도중 죽은 라운드: 글·레지스트리는 있는데 라운드는 아직 TOPIC_SELECTION
    publishing_round.status = RoundStatus.TOPIC_SELECTION
    publishing_round.draft_ids = []
    await harness.rounds.save(publishing_round)
    harness.llm.scripts = {}

    publishing_round = await _drive(harness)

    assert publishing_round.status == RoundStatus.IN_PROGRESS
    assert len(await harness.drafts.list_by_round("round-1")) == 3
    assert len(await harness.topics.list_by_round("round-1")) == 3
    assert harness.llm.kinds == ["PROPOSE_TOPIC"] * 3


class _AbortBeforeLock(PublishingRoundRepository):
    """확정의 라운드 저장 직전에 중단이 끼어든다."""

    def __init__(self, database, harness_ref: list) -> None:
        super().__init__(database)
        self._harness_ref = harness_ref

    async def save(self, publishing_round: PublishingRound) -> None:
        if publishing_round.status == RoundStatus.IN_PROGRESS and self._harness_ref:
            harness = self._harness_ref.pop()
            await harness.round_service.abort_round(publishing_round.round_id)
        await super().save(publishing_round)


async def test_abort_during_commit_does_not_resurrect_cleaned_documents():
    harness_ref: list = []
    database = FakeAsyncDatabase()
    harness = PipelineHarness(
        {"PROPOSE_TOPIC": _by_slot(DISTINCT)},
        database=database,
        round_repository=_AbortBeforeLock(database, harness_ref),
    )
    harness_ref.append(harness)
    await harness.rounds.add(make_round())

    publishing_round = await _drive(harness)

    assert (publishing_round.status, publishing_round.abort_reason) == (RoundStatus.ABORTED, RoundAbortReason.MANUAL)
    assert {draft.status for draft in await harness.drafts.list_by_round("round-1")} == {PostStatus.REJECTED}
    assert {entry.status for entry in await harness.topics.list_by_round("round-1")} == {"RELEASED"}
