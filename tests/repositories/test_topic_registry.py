"""TopicRegistryRepository 테스트. 멱등 추가, 회피 목록 조회, 상태 변경을 확인한다."""

from datetime import UTC, datetime, timedelta

import bson
import pytest

from app.models.enums import TopicRegistryStatus
from app.repositories.topic_registry import TopicRegistryRepository
from tests.factories import make_registry_entry
from tests.fakes import FakeAsyncDatabase

pytestmark = pytest.mark.anyio


async def test_add_if_missing_is_idempotent():
    database = FakeAsyncDatabase()
    repository = TopicRegistryRepository(database)
    entry = make_registry_entry()

    assert await repository.add_if_missing(entry) is True
    assert await repository.add_if_missing(make_registry_entry(title="다른 제목")) is False
    assert (await repository.get(entry.entry_id)).title == entry.title
    assert bson.encode(database.agent_topics.docs[0])


async def test_list_active_since_excludes_released_and_old_entries():
    database = FakeAsyncDatabase()
    repository = TopicRegistryRepository(database)
    now = datetime.now(UTC)
    await repository.add_if_missing(make_registry_entry(entry_id="r1:A", created_at=now - timedelta(days=1)))
    await repository.add_if_missing(make_registry_entry(entry_id="r1:B", created_at=now - timedelta(days=400)))
    released = make_registry_entry(entry_id="r1:C", created_at=now)
    released.status = TopicRegistryStatus.RELEASED
    await repository.add_if_missing(released)
    published = make_registry_entry(entry_id="r2:A", created_at=now - timedelta(hours=1))
    published.status = TopicRegistryStatus.PUBLISHED
    await repository.add_if_missing(published)

    entries = await repository.list_active_since(now - timedelta(days=180))

    assert [e.entry_id for e in entries] == ["r2:A", "r1:A"]
    assert [e.entry_id for e in await repository.list_active()] == ["r2:A", "r1:A", "r1:B"]


async def test_set_status_updates_and_reports_match():
    database = FakeAsyncDatabase()
    repository = TopicRegistryRepository(database)
    await repository.add_if_missing(make_registry_entry())
    published_at = datetime.now(UTC)

    assert await repository.set_status("round-1:A", TopicRegistryStatus.PUBLISHED, published_at) is True
    assert await repository.set_status("missing", TopicRegistryStatus.RELEASED) is False

    entry = await repository.get("round-1:A")
    assert (entry.status, entry.published_at) == (TopicRegistryStatus.PUBLISHED, published_at)
