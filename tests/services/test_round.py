"""RoundService 테스트: 생성 검사, 완료, 중단 정리(시나리오 ⑨·⑩), reconcile."""

from datetime import UTC, datetime

import pytest

from app.clients.publisher.base import PostCategory
from app.core.exceptions import InvalidTransitionError, PublishFailedError
from app.models.enums import (
    AgentSlot,
    EscalationReason,
    FailureCode,
    PostStatus,
    ReviewLens,
    RoundAbortReason,
    RoundStatus,
)
from app.models.post_draft import Publication
from app.models.publishing_round import PublishingRound
from app.repositories.agent_round import PublishingRoundRepository
from app.services.round import RoundService, category_options
from tests.factories import FAKE_VENDORS, make_binding, make_category, route_documents
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


async def test_create_round_validates_personas_and_rejects_second_topic_selection_round():
    harness = PipelineHarness()
    await harness.seed_routes()
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


class FakePublisher:
    def __init__(self, categories: list[PostCategory] | None = None, error: Exception | None = None) -> None:
        self.categories = categories or []
        self.error = error

    async def list_categories(self) -> list[PostCategory]:
        if self.error:
            raise self.error
        return self.categories


CATEGORIES = [
    PostCategory(id="m1", value="backend", label="Backend"),
    PostCategory(id="m2", value="old", label="Old", is_active=False),
    PostCategory(id="s1", parent_id="m1", value="db", label="Database"),
    PostCategory(id="s2", parent_id="m1", value="off", label="Off", is_active=False),
    PostCategory(id="s3", parent_id="m2", value="orphan", label="Orphan"),
]


def test_category_options_pairs_active_subs_under_active_mains_in_order():
    assert [(option.main_label, option.sub_category_id) for option in category_options(CATEGORIES)] == [
        ("Backend", "s1")
    ]


async def test_start_round_snapshots_post_be_categories():
    harness = PipelineHarness()
    await harness.seed_routes()
    service = RoundService(
        harness.rounds,
        harness.drafts,
        harness.topics,
        publisher=FakePublisher(CATEGORIES),
        route_repository=harness.routes,
        providers=["fake", *FAKE_VENDORS.values()],
    )

    publishing_round = await service.start_round(theme_keyword="mongodb", slot_bindings=BINDINGS)

    assert [option.sub_category_id for option in publishing_round.categories] == ["s1"]
    assert (await harness.rounds.get(publishing_round.round_id)) is not None


async def test_start_round_does_not_create_round_when_categories_fail_or_are_empty():
    harness = PipelineHarness()
    failing = RoundService(
        harness.rounds, harness.drafts, harness.topics, publisher=FakePublisher(error=PublishFailedError("down"))
    )
    with pytest.raises(PublishFailedError):
        await failing.start_round(theme_keyword=None, slot_bindings=BINDINGS)

    empty = RoundService(harness.rounds, harness.drafts, harness.topics, publisher=FakePublisher([CATEGORIES[0]]))
    with pytest.raises(ValueError):
        await empty.start_round(theme_keyword=None, slot_bindings=BINDINGS)

    without_publisher = RoundService(harness.rounds, harness.drafts, harness.topics)
    with pytest.raises(ValueError):
        await without_publisher.start_round(theme_keyword=None, slot_bindings=BINDINGS)
    assert harness.database.agent_rounds.docs == []


async def test_create_round_validates_raw_settings_bindings():
    """요청의 A/B 값은 원문 dict 로 받아 여기서 검증한다."""
    harness = PipelineHarness()
    await harness.seed_routes()
    service = harness.round_service
    raw = {slot: binding.model_dump() for slot, binding in BINDINGS.items()}

    publishing_round = await service.create_round(theme_keyword=None, slot_bindings=raw, categories=[make_category()])

    assert publishing_round.slot_bindings == BINDINGS
    with pytest.raises(ValueError):  # 모르는 슬롯
        await service.create_round(
            theme_keyword=None, slot_bindings={"D": raw["A"], **raw}, categories=[make_category()]
        )
    with pytest.raises(ValueError):  # ModelBinding 검증 실패(pydantic)
        await service.create_round(
            theme_keyword=None, slot_bindings={**raw, "A": {"provider": "x"}}, categories=[make_category()]
        )


# ---------- 라우트 스냅샷 ----------


async def test_create_round_snapshots_routes_and_fills_slot_bindings_from_write_routes():
    harness = PipelineHarness()
    await harness.seed_routes()

    created = await harness.round_service.create_round(theme_keyword=None, categories=[make_category()])

    assert created.routing is not None
    assert created.slot_bindings[A].provider == "fake-a"
    assert (created.slot_bindings[A].model_id, created.slot_bindings[A].persona_id) == ("fake-a-1", "architect")
    stored = await harness.rounds.get(created.round_id)
    assert stored is not None and stored.routing == created.routing


async def test_later_route_changes_do_not_touch_a_running_round():
    harness = PipelineHarness()
    await harness.seed_routes()
    created = await harness.round_service.create_round(theme_keyword=None, categories=[make_category()])

    documents = route_documents()
    next(each for each in documents if each["_id"] == "WRITE_DRAFT:architect")["candidates"][0]["model_id"] = "new"
    await harness.routes.replace_all(documents)

    stored = await harness.rounds.get(created.round_id)
    assert stored is not None
    assert stored.routing.candidates("WRITE_DRAFT:architect")[0].model_id == "fake-a-1"


async def test_create_round_fails_without_routes_and_leaves_no_document():
    harness = PipelineHarness()

    with pytest.raises(ValueError, match="SLOTS"):
        await harness.round_service.create_round(theme_keyword=None, categories=[make_category()])
    assert harness.database.agent_rounds.docs == []


async def test_create_round_fails_when_a_vendor_key_is_missing_for_a_needed_route():
    harness = PipelineHarness()
    await harness.seed_routes()
    service = RoundService(
        harness.rounds, harness.drafts, harness.topics, route_repository=harness.routes, providers=["fake-a", "fake-b"]
    )

    with pytest.raises(ValueError, match="has no candidate"):
        await service.create_round(theme_keyword=None, categories=[make_category()])


async def test_override_with_a_provider_without_a_key_is_refused():
    harness = PipelineHarness()
    await harness.seed_routes()

    with pytest.raises(ValueError, match="no API key"):
        await harness.round_service.create_round(
            theme_keyword=None,
            slot_bindings={"A": make_binding(provider="nokey", persona_id="architect")},
            categories=[make_category()],
        )


async def test_override_changes_only_that_slot():
    harness = PipelineHarness()
    await harness.seed_routes()

    created = await harness.round_service.create_round(
        theme_keyword=None,
        slot_bindings={"A": make_binding(provider="fake-b", model_id="ab", persona_id="architect")},
        categories=[make_category()],
    )

    assert (created.slot_bindings[A].provider, created.slot_bindings[A].model_id) == ("fake-b", "ab")
    assert created.slot_bindings[B].provider == "fake-b"


async def test_evidence_is_kept_on_the_round():
    harness = PipelineHarness()
    await harness.seed_routes()

    created = await harness.round_service.create_round(
        theme_keyword=None, categories=[make_category()], evidence="git log"
    )

    assert created.evidence == "git log"


async def test_build_draft_gives_each_lens_its_own_reviewer():
    harness = PipelineHarness()
    publishing_round = await harness.seed_round()

    draft = await harness.draft(A)

    assert [assignment.primary_lens for assignment in draft.reviewer_assignments] == list(ReviewLens)
    assert {assignment.slot for assignment in draft.reviewer_assignments} == {None}
    assert publishing_round.routing is not None


async def test_seed_routes_refuses_a_broken_file():
    harness = PipelineHarness()
    broken = [each for each in route_documents() if each["_id"] != "ARBITRATE"]

    with pytest.raises(ValueError, match="ARBITRATE"):
        await harness.round_service.seed_routes(broken)
    assert await harness.round_service.list_route_documents() == []

    await harness.round_service.seed_routes(route_documents())
    assert len(await harness.round_service.list_route_documents()) == len(route_documents())
