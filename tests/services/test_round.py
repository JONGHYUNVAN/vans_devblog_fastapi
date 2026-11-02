"""RoundService 테스트: 생성 검사, 완료, 중단 정리(시나리오 ⑨·⑩), reconcile."""

from datetime import UTC, datetime

import pytest

from app.core.exceptions import InvalidTransitionError
from app.models.enums import AgentSlot, EscalationReason, FailureCode, PostStatus, RoundAbortReason, RoundStatus
from app.models.post_draft import Publication
from app.models.publishing_round import PublishingRound
from app.repositories.agent_round import PublishingRoundRepository
from tests.factories import make_binding, make_category
from tests.fakes import FakeAsyncDatabase, PipelineHarness

pytestmark = pytest.mark.anyio

A, B, C = AgentSlot.A, AgentSlot.B, AgentSlot.C
BINDINGS = {
    A: make_binding(persona_id="architect"),
    B: make_binding(persona_id="systems_engineer"),
    C: make_binding(persona_id="troubleshooter"),
}


async def _publish(harness: PipelineHarness, slot: AgentSlot) -> None:
    draft = await harness.draft(slot)
    draft.status = PostStatus.PUBLISHED
    draft.publication = Publication(
        published_at=datetime.now(UTC), final_version=1, external_post_id="post-1", peer_review_note=""
    )
    await harness.drafts.save(draft)


async def test_create_round_validates_lens_matrix_and_rejects_second_topic_selection_round():
    harness = PipelineHarness()
    await harness.rounds.ensure_indexes()

    created = await harness.round_service.create_round(
        theme_keyword="mongodb", slot_bindings=BINDINGS, categories=[make_category()]
    )

    assert (created.status, created.round_seq, created.theme_keyword) == (RoundStatus.TOPIC_SELECTION, 1, "mongodb")
    with pytest.raises(InvalidTransitionError):
        await harness.round_service.create_round(
            theme_keyword=None, slot_bindings=BINDINGS, categories=[make_category()]
        )
    assert len(harness.database.agent_rounds.docs) == 1

    same_lens = BINDINGS | {B: make_binding(persona_id="architect")}
    with pytest.raises(ValueError):
        await harness.round_service.create_round(
            theme_keyword=None, slot_bindings=same_lens, categories=[make_category()]
        )
    with pytest.raises(ValueError):
        await harness.round_service.create_round(
            theme_keyword=None,
            slot_bindings=BINDINGS | {A: make_binding(persona_id="nope")},
            categories=[make_category()],
        )


async def test_close_if_complete_only_when_every_draft_is_terminal():
    harness = PipelineHarness()
    publishing_round = await harness.seed_round()
    for slot in (A, B):
        draft = await harness.draft(slot)
        draft.reject_by_abort(RoundAbortReason.MANUAL)
        await harness.drafts.save(draft)

    assert await harness.round_service.close_if_complete(publishing_round.round_id) is False
    await _publish(harness, C)
    assert await harness.round_service.close_if_complete(publishing_round.round_id) is True

    assert (await harness.rounds.get(publishing_round.round_id)).status == RoundStatus.COMPLETED


async def test_abort_round_rejects_escalated_and_failed_drafts_but_keeps_published():  # ⑨
    harness = PipelineHarness()
    publishing_round = await harness.seed_round()
    escalated = await harness.draft(A)
    escalated.escalate(EscalationReason.MAX_ITERATIONS_REACHED)
    await harness.drafts.save(escalated)
    failed = await harness.draft(B)
    failed.mark_failed(FailureCode.INTERNAL_ERROR, "boom")
    await harness.drafts.save(failed)
    await _publish(harness, C)
    await harness.round_service.reconcile()  # C 의 레지스트리를 PUBLISHED 로 맞춘다

    await harness.round_service.abort_round(publishing_round.round_id)

    assert (await harness.rounds.get(publishing_round.round_id)).status == RoundStatus.ABORTED
    statuses = {slot: (await harness.draft(slot)).status for slot in AgentSlot}
    assert statuses == {A: PostStatus.REJECTED, B: PostStatus.REJECTED, C: PostStatus.PUBLISHED}
    assert (await harness.draft(A)).abort_reason == RoundAbortReason.MANUAL
    registry = {entry.entry_id: entry.status for entry in await harness.topics.list_by_round("round-1")}
    assert registry == {"round-1:A": "RELEASED", "round-1:B": "RELEASED", "round-1:C": "PUBLISHED"}
    with pytest.raises(InvalidTransitionError):
        await harness.round_service.abort_round(publishing_round.round_id)


class _AbortBeforeSave(PublishingRoundRepository):
    """close_if_complete 가 읽고 저장하기 전에 중단이 끼어든다."""

    def __init__(self, database, harness_ref: list) -> None:
        super().__init__(database)
        self._harness_ref = harness_ref
        self.armed = False

    async def save(self, publishing_round: PublishingRound) -> None:
        if self.armed and publishing_round.status == RoundStatus.COMPLETED:
            self.armed = False
            await self._harness_ref[0].round_service.abort_round(publishing_round.round_id)
        await super().save(publishing_round)


async def test_abort_overlapping_close_if_complete_leaves_round_aborted():  # ⑩
    harness_ref: list = []
    database = FakeAsyncDatabase()
    harness = PipelineHarness(database=database, round_repository=_AbortBeforeSave(database, harness_ref))
    harness_ref.append(harness)
    publishing_round = await harness.seed_round()
    for slot in AgentSlot:
        await _publish(harness, slot)
    harness.rounds.armed = True

    assert await harness.round_service.close_if_complete(publishing_round.round_id) is False

    assert (await harness.rounds.get(publishing_round.round_id)).status == RoundStatus.ABORTED


async def test_reconcile_fixes_registry_and_leftover_drafts_of_aborted_round():
    harness = PipelineHarness()
    publishing_round = await harness.seed_round()
    await _publish(harness, A)
    rejected = await harness.draft(B)
    rejected.status = PostStatus.REJECTED
    await harness.drafts.save(rejected)
    # 중단이 라운드만 쓰고 죽었다
    publishing_round.abort(RoundAbortReason.MANUAL)
    await harness.rounds.save(publishing_round)

    await harness.round_service.reconcile()

    registry = {entry.entry_id: entry.status for entry in await harness.topics.list_by_round("round-1")}
    assert registry == {"round-1:A": "PUBLISHED", "round-1:B": "RELEASED", "round-1:C": "RELEASED"}
    leftover = await harness.draft(C)
    assert (leftover.status, leftover.abort_reason) == (PostStatus.REJECTED, RoundAbortReason.MANUAL)


async def test_reconcile_closes_in_progress_round_whose_drafts_all_ended():
    harness = PipelineHarness()
    publishing_round = await harness.seed_round()
    for slot in AgentSlot:
        await _publish(harness, slot)

    await harness.round_service.reconcile()

    assert (await harness.rounds.get(publishing_round.round_id)).status == RoundStatus.COMPLETED
