"""AgentCallRepository 테스트. 영수증 append 와 글별 usage 합계를 확인한다."""

import bson
import pytest

from app.models.enums import CallKind
from app.repositories.agent_call import AgentCallRepository
from tests.factories import make_call
from tests.fakes import FakeAsyncDatabase

pytestmark = pytest.mark.anyio


async def test_add_then_get_round_trips_document():
    database = FakeAsyncDatabase()
    repository = AgentCallRepository(database)
    call = make_call()

    await repository.add(call)

    assert await repository.get(call.call_id) == call
    assert bson.encode(database.agent_calls.docs[0])


async def test_sum_usage_adds_every_attempt_including_failures():
    database = FakeAsyncDatabase()
    repository = AgentCallRepository(database)
    await repository.add(make_call(call_id="c1", prompt_tokens=100, completion_tokens=10))
    await repository.add(make_call(call_id="c2", prompt_tokens=200, completion_tokens=20, output_parsed_ok=False))
    await repository.add(make_call(call_id="c3", draft_id="round-1:B", prompt_tokens=999))

    usage = await repository.sum_usage("round-1:A")

    assert (usage.prompt_tokens, usage.completion_tokens, usage.total_tokens) == (300, 30, 330)


async def test_sum_usage_is_zero_without_calls():
    usage = await AgentCallRepository(FakeAsyncDatabase()).sum_usage("round-1:A")

    assert usage.total_tokens == 0


async def test_list_by_round_includes_topic_proposals_in_start_order():
    database = FakeAsyncDatabase()
    repository = AgentCallRepository(database)
    await repository.add(make_call(call_id="c2", started_second=1))
    await repository.add(make_call(call_id="c1", draft_id=None, kind=CallKind.PROPOSE_TOPIC))

    calls = await repository.list_by_round("round-1")

    assert [c.call_id for c in calls] == ["c1", "c2"]
    assert [c.call_id for c in await repository.list_by_draft("round-1:A")] == ["c2"]


async def test_lists_return_receipts_without_bodies_and_get_returns_them():
    # 라운드 영수증을 본문째 읽으면 소켓 제한을 넘겼다. 목록은 메시지·원문을 빼고 읽고, 본문은 get 으로 읽는다
    repository = AgentCallRepository(FakeAsyncDatabase())
    call = make_call(call_id="c1", prompt_tokens=100)
    assert call.messages and call.output_raw is not None
    await repository.add(call)

    [by_round] = await repository.list_by_round("round-1")
    [by_draft] = await repository.list_by_draft("round-1:A")

    for listed in (by_round, by_draft):
        assert (listed.messages, listed.output_raw) == ([], None)
        assert listed.model_dump(exclude={"messages", "output_raw"}) == call.model_dump(
            exclude={"messages", "output_raw"}
        )
    assert await repository.get("c1") == call
