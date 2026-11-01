"""PublishingRoundRepository 테스트. 가짜 컬렉션으로 질의·저장 조건을 확인한다."""

import bson
import pytest

from app.core.exceptions import ConcurrentUpdateError, InvalidTransitionError
from app.models.enums import RoundStatus
from app.repositories.agent_round import ONE_TOPIC_SELECTION_INDEX, PublishingRoundRepository
from tests.factories import make_round
from tests.fakes import FakeAsyncDatabase

pytestmark = pytest.mark.anyio


async def test_add_then_get_round_trips_document():
    database = FakeAsyncDatabase()
    publishing_round = make_round()

    await PublishingRoundRepository(database).add(publishing_round)
    loaded = await PublishingRoundRepository(database).get("round-1")

    assert loaded == publishing_round
    assert bson.encode(database.agent_rounds.docs[0])  # StrEnum 키·값과 aware datetime 이 BSON 으로 인코딩된다


async def test_get_returns_none_when_missing():
    assert await PublishingRoundRepository(FakeAsyncDatabase()).get("nope") is None


async def test_add_raises_invalid_transition_when_duplicate():
    database = FakeAsyncDatabase()
    repository = PublishingRoundRepository(database)
    await repository.add(make_round())

    with pytest.raises(InvalidTransitionError):
        await repository.add(make_round())

    assert len(database.agent_rounds.docs) == 1


async def test_list_by_status_filters_and_sorts_by_created_at():
    database = FakeAsyncDatabase()
    repository = PublishingRoundRepository(database)
    await repository.add(make_round(round_id="round-2", round_seq=2))
    await repository.add(make_round(round_id="round-1"))
    aborted = make_round(round_id="round-3", round_seq=3)
    aborted.status = RoundStatus.ABORTED
    await repository.add(aborted)

    rounds = await repository.list_by_status(RoundStatus.TOPIC_SELECTION)

    assert [r.round_id for r in rounds] == ["round-2", "round-1"]


async def test_save_increments_revision_when_unchanged():
    database = FakeAsyncDatabase()
    repository = PublishingRoundRepository(database)
    publishing_round = make_round()
    await repository.add(publishing_round)

    publishing_round.theme_keyword = "mongodb"
    await repository.save(publishing_round)

    assert publishing_round.revision == 1
    loaded = await repository.get("round-1")
    assert (loaded.revision, loaded.theme_keyword) == (1, "mongodb")
    assert loaded.updated_at >= loaded.created_at


async def test_save_raises_concurrent_update_and_restores_revision_when_changed():
    database = FakeAsyncDatabase()
    repository = PublishingRoundRepository(database)
    publishing_round = make_round()
    await repository.add(publishing_round)
    stale = await repository.get("round-1")
    await repository.save(publishing_round)  # revision 0 → 1

    with pytest.raises(ConcurrentUpdateError):
        await repository.save(stale)

    assert stale.revision == 0


async def test_ensure_indexes_declares_partial_unique_topic_selection_index():
    database = FakeAsyncDatabase()

    await PublishingRoundRepository(database).ensure_indexes()

    [_, (keys, options)] = database.agent_rounds.indexes
    assert keys == "status"
    assert options["unique"] is True
    assert options["partialFilterExpression"] == {"status": "TOPIC_SELECTION"}
    assert options["name"] == ONE_TOPIC_SELECTION_INDEX
