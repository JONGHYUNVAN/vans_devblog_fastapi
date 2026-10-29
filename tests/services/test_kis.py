"""KISService 테스트."""

from collections.abc import AsyncIterator
from typing import Any

import pytest

from app.core.exceptions import InvalidSymbolError, KISNotConfiguredError
from app.services.kis import IsDisconnected, KISService, StreamEvent
from tests.fakes import FakeKISStream

pytestmark = pytest.mark.anyio

TRADE = {"symbol": "005930", "price": 58300}
ORDERBOOK = {"symbol": "005930", "totalAskVolume": 55000}


def _service(stream: FakeKISStream, is_configured: bool = True, **intervals: float) -> KISService:
    return KISService(stream, is_configured, snapshot_wait=0, **intervals)


def _disconnect_after(checks: int) -> IsDisconnected:
    """checks 번은 연결돼 있다고 답하고 그 뒤로는 끊겼다고 답한다."""
    remaining = checks

    async def is_disconnected() -> bool:
        nonlocal remaining
        remaining -= 1
        return remaining < 0

    return is_disconnected


async def _collect(events: AsyncIterator[StreamEvent]) -> list[tuple[str, Any]]:
    return [event async for event in events]


async def test_get_snapshot_returns_latest_quotes():
    stream = FakeKISStream({"trade": TRADE, "orderbook": None, "updatedAt": "2026-10-29T06:30:00+00:00"})

    snapshot = await _service(stream).get_snapshot("005930")

    assert snapshot.model_dump(by_alias=True) == {
        "success": True,
        "data": {
            "symbol": "005930",
            "name": "삼성전자",
            "trade": TRADE,
            "orderbook": None,
            "updatedAt": "2026-10-29T06:30:00+00:00",
        },
    }
    assert stream.ensure_calls == 0


async def test_get_snapshot_waits_once_then_returns_nulls():
    stream = FakeKISStream()

    snapshot = await _service(stream).get_snapshot("123456")

    assert (snapshot.data.name, snapshot.data.trade, snapshot.data.updated_at) == ("123456", None, None)
    assert stream.ensure_calls == 1


@pytest.mark.parametrize("symbol", ["12345", "00593A", "0059300"])
async def test_get_snapshot_raises_invalid_symbol(symbol):
    with pytest.raises(InvalidSymbolError):
        await _service(FakeKISStream()).get_snapshot(symbol)


async def test_get_snapshot_raises_when_not_configured():
    with pytest.raises(KISNotConfiguredError):
        await _service(FakeKISStream(), is_configured=False).get_snapshot("005930")


async def test_open_stream_sends_connected_snapshot_then_live_events_and_unsubscribes():
    stream = FakeKISStream({"trade": TRADE, "orderbook": None, "updatedAt": "now"})
    events = _service(stream, ping_interval=100).open_stream("005930", _disconnect_after(1))

    first = await anext(events)
    stream.queues["005930"].put_nowait(("orderbook", ORDERBOOK))
    rest = await _collect(events)

    assert [first, *rest] == [("connected", {"symbol": "005930"}), ("trade", TRADE), ("orderbook", ORDERBOOK)]
    assert stream.unsubscribed == ["005930"]


async def test_open_stream_pings_on_interval():
    events = _service(FakeKISStream(), ping_interval=0).open_stream("005930", _disconnect_after(2))

    assert await _collect(events) == [("connected", {"symbol": "005930"}), ("ping", {}), ("ping", {})]


async def test_open_stream_only_pings_when_not_configured():
    stream = FakeKISStream()
    service = _service(stream, is_configured=False, degraded_ping_interval=0)

    events = await _collect(service.open_stream("005930", _disconnect_after(2)))

    assert events == [("ping", {}), ("ping", {})]
    assert stream.queues == {}


async def test_open_stream_raises_invalid_symbol_before_streaming():
    with pytest.raises(InvalidSymbolError):
        _service(FakeKISStream()).open_stream("bad", _disconnect_after(0))
