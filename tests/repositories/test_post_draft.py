"""PostDraftRepository 테스트. 결정론적 ID 의 멱등 생성과 revision 잠금을 확인한다."""

import bson
import pytest

from app.core.exceptions import ConcurrentUpdateError
from app.models.enums import AgentSlot, PostStatus
from app.repositories.post_draft import PostDraftRepository
from tests.factories import make_draft, write_next_version
from tests.fakes import FakeAsyncDatabase

pytestmark = pytest.mark.anyio


async def test_add_if_missing_then_get_round_trips_document_with_versions():
    database = FakeAsyncDatabase()
    repository = PostDraftRepository(database)
    draft = make_draft()
    write_next_version(draft)

    assert await repository.add_if_missing(draft) is True
    loaded = await repository.get(draft.draft_id)

    assert loaded == draft
    assert bson.encode(database.agent_drafts.docs[0])


async def test_add_if_missing_returns_false_and_keeps_existing_document():
    database = FakeAsyncDatabase()
    repository = PostDraftRepository(database)
    original = make_draft()
    await repository.add_if_missing(original)
    rewritten = make_draft(token_limit=5)

    assert await repository.add_if_missing(rewritten) is False
    assert (await repository.get(original.draft_id)).token_limit == original.token_limit


async def test_list_by_round_orders_by_slot():
    database = FakeAsyncDatabase()
    repository = PostDraftRepository(database)
    for slot in (AgentSlot.C, AgentSlot.A, AgentSlot.B):
        await repository.add_if_missing(make_draft(slot))

    drafts = await repository.list_by_round("round-1")

    assert [d.author_slot for d in drafts] == [AgentSlot.A, AgentSlot.B, AgentSlot.C]


async def test_list_by_status_filters_several_statuses():
    database = FakeAsyncDatabase()
    repository = PostDraftRepository(database)
    failed = make_draft(AgentSlot.A)
    failed.status = PostStatus.FAILED
    escalated = make_draft(AgentSlot.B)
    escalated.status = PostStatus.ESCALATED
    await repository.add_if_missing(failed)
    await repository.add_if_missing(escalated)
    await repository.add_if_missing(make_draft(AgentSlot.C))

    drafts = await repository.list_by_status(PostStatus.FAILED, PostStatus.ESCALATED)

    assert {d.author_slot for d in drafts} == {AgentSlot.A, AgentSlot.B}


async def test_save_conflict_raises_and_restores_revision():
    database = FakeAsyncDatabase()
    repository = PostDraftRepository(database)
    draft = make_draft()
    await repository.add_if_missing(draft)
    stale = await repository.get(draft.draft_id)
    write_next_version(draft)
    await repository.save(draft)
    assert draft.revision == 1

    with pytest.raises(ConcurrentUpdateError):
        await repository.save(stale)

    assert stale.revision == 0
    assert (await repository.get(draft.draft_id)).status == PostStatus.IN_REVIEW
