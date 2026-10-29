"""KISStreamManager 테스트. 로컬 WebSocket 서버를 KIS 대신 띄운다 (외부 네트워크 없음)."""

import asyncio
import base64
import json
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager

import pytest
from Crypto.Cipher import AES
from Crypto.Util.Padding import pad
from websockets.asyncio.server import ServerConnection, serve

from app.clients.kis.constants import TR_ID_ORDERBOOK, TR_ID_TRADE
from app.clients.kis.stream import KISStreamManager
from tests.fakes import kis_orderbook_body, kis_trade_body

pytestmark = pytest.mark.anyio

PINGPONG = json.dumps({"header": {"tr_id": "PINGPONG", "datetime": "20261029153000"}})


class FakeAuthClient:
    async def get_approval_key(self) -> str:
        return "fake_key"


@asynccontextmanager
async def _kis_server(handler: Callable[[ServerConnection], Awaitable[None]]) -> AsyncIterator[KISStreamManager]:
    """handler 로 응답하는 로컬 서버와 그 서버에 붙는 매니저."""
    async with serve(handler, "127.0.0.1", 0) as server:
        port = server.sockets[0].getsockname()[1]
        manager = KISStreamManager(FakeAuthClient(), f"ws://127.0.0.1:{port}", reconnect_delay=0.05)
        try:
            yield manager
        finally:
            await manager.close()


def _subscription(message: str) -> tuple[str, str, str]:
    data = json.loads(message)
    return data["header"]["tr_type"], data["body"]["input"]["tr_id"], data["body"]["input"]["tr_key"]


def _encrypt(text: str, key: str, iv: str) -> str:
    cipher = AES.new(key.encode(), AES.MODE_CBC, iv.encode())
    return base64.b64encode(cipher.encrypt(pad(text.encode(), AES.block_size))).decode()


async def test_subscribe_sends_subscription_answers_pingpong_and_relays_trade():
    received: list[str] = []
    echoed: list[str] = []

    async def handler(ws: ServerConnection) -> None:
        received.extend([await ws.recv(), await ws.recv()])
        await ws.send(PINGPONG)
        echoed.append(await ws.recv())
        await ws.send(f"0|{TR_ID_TRADE}|001|{kis_trade_body()}")
        await ws.wait_closed()

    async with _kis_server(handler) as manager:
        queue = manager.subscribe("005930")
        event_type, trade = await asyncio.wait_for(queue.get(), timeout=5)
        snapshot = manager.get_snapshot("005930")

    assert [_subscription(message) for message in received] == [
        ("1", TR_ID_TRADE, "005930"),
        ("1", TR_ID_ORDERBOOK, "005930"),
    ]
    assert json.loads(received[0])["header"]["approval_key"] == "fake_key"
    assert echoed == [PINGPONG]
    assert (event_type, trade["price"]) == ("trade", 58300)
    assert snapshot is not None
    assert (snapshot["trade"], snapshot["orderbook"]) == (trade, None)


async def test_decrypts_encrypted_data_and_skips_malformed_messages():
    key, iv = "k" * 32, "i" * 16

    async def handler(ws: ServerConnection) -> None:
        await ws.recv()
        await ws.recv()
        response = {
            "header": {"tr_id": TR_ID_TRADE},
            "body": {"msg1": "SUBSCRIBE SUCCESS", "output": {"iv": iv, "key": key}},
        }
        await ws.send(json.dumps(response))
        await ws.send(f"1|{TR_ID_TRADE}|001|{_encrypt(kis_trade_body(), key, iv)}")
        await ws.send("not json and no pipes")
        await ws.send(f"0|{TR_ID_ORDERBOOK}|001|{kis_orderbook_body()}")
        await ws.wait_closed()

    async with _kis_server(handler) as manager:
        queue = manager.subscribe("005930")
        first = await asyncio.wait_for(queue.get(), timeout=5)
        second = await asyncio.wait_for(queue.get(), timeout=5)

    assert (first[0], first[1]["price"]) == ("trade", 58300)
    assert (second[0], second[1]["totalAskVolume"]) == ("orderbook", 55000)


async def test_unsubscribes_from_kis_only_when_last_subscriber_leaves():
    received: list[str] = []
    subscribed = asyncio.Event()
    unsubscribed = asyncio.Event()

    async def handler(ws: ServerConnection) -> None:
        async for message in ws:
            received.append(message)
            if len(received) == 2:
                subscribed.set()
            if len(received) == 4:
                unsubscribed.set()

    async with _kis_server(handler) as manager:
        first = manager.subscribe("005930")
        second = manager.subscribe("005930")
        await asyncio.wait_for(subscribed.wait(), timeout=5)
        manager.unsubscribe("005930", first)
        await asyncio.sleep(0.1)
        assert len(received) == 2
        manager.unsubscribe("005930", second)
        await asyncio.wait_for(unsubscribed.wait(), timeout=5)

    assert [_subscription(message)[0] for message in received] == ["1", "1", "2", "2"]


async def test_reconnects_and_resubscribes_after_server_closes():
    connections = 0
    resubscribed = asyncio.Event()

    async def handler(ws: ServerConnection) -> None:
        nonlocal connections
        connections += 1
        await ws.recv()
        if connections == 1:
            await ws.close()
            return
        resubscribed.set()
        await ws.wait_closed()

    async with _kis_server(handler) as manager:
        manager.subscribe("005930")
        await asyncio.wait_for(resubscribed.wait(), timeout=5)

    assert connections == 2


async def test_get_snapshot_none_before_any_data():
    manager = KISStreamManager(FakeAuthClient(), "ws://127.0.0.1:9")

    assert manager.get_snapshot("005930") is None
