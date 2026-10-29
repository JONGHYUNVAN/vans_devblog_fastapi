"""KIS 실시간 시세: 스냅샷 조회와 SSE 이벤트 생성."""

import asyncio
import logging
from collections.abc import AsyncIterator, Awaitable, Callable
from typing import Any

from app.clients.kis.constants import KR_STOCK_MAP
from app.clients.kis.stream import KISStreamManager
from app.core.exceptions import InvalidSymbolError, KISNotConfiguredError
from app.schemas.kis import Snapshot, SnapshotData

logger = logging.getLogger(__name__)

SNAPSHOT_WAIT = 5  # 초
PING_INTERVAL = 15
DEGRADED_PING_INTERVAL = 30
_DISCONNECT_CHECK_INTERVAL = 1

StreamEvent = tuple[str, dict[str, Any]]  # (이벤트 이름, 데이터)
IsDisconnected = Callable[[], Awaitable[bool]]


class KISService:
    """KISStreamManager 의 최신값과 구독 큐를 스냅샷 응답·SSE 이벤트로 바꾼다.

    대기·ping 간격은 테스트가 줄일 수 있게 생성자로 받는다.
    """

    def __init__(
        self,
        kis_stream: KISStreamManager,
        is_configured: bool,
        snapshot_wait: float = SNAPSHOT_WAIT,
        ping_interval: float = PING_INTERVAL,
        degraded_ping_interval: float = DEGRADED_PING_INTERVAL,
    ) -> None:
        self._kis_stream = kis_stream
        self._is_configured = is_configured
        self._snapshot_wait = snapshot_wait
        self._ping_interval = ping_interval
        self._degraded_ping_interval = degraded_ping_interval

    async def get_snapshot(self, symbol: str) -> Snapshot:
        """종목의 최신 체결가·호가. 없으면 연결을 보장하고 5초 기다린 뒤 한 번 더 본다. 그래도 없으면 null 들로 응답한다.

        종목코드가 6자리 숫자가 아니면 InvalidSymbolError, KIS 가 설정되지 않았으면 KISNotConfiguredError.
        """
        _check_symbol(symbol)
        if not self._is_configured:
            raise KISNotConfiguredError("KIS OpenAPI가 설정되지 않았습니다.")

        data = self._kis_stream.get_snapshot(symbol)
        if data is None:
            # KIS 구독은 SSE 구독자가 있는 종목만 한다. 그래서 구독자가 없는 종목은 기다려도 데이터가 오지 않는다 (Django 동작 그대로).
            self._kis_stream.ensure_connected()
            await asyncio.sleep(self._snapshot_wait)
            data = self._kis_stream.get_snapshot(symbol) or {}
        return Snapshot(
            data=SnapshotData(
                symbol=symbol,
                name=KR_STOCK_MAP.get(symbol, symbol),
                trade=data.get("trade"),
                orderbook=data.get("orderbook"),
                updated_at=data.get("updatedAt"),
            )
        )

    def open_stream(self, symbol: str, is_disconnected: IsDisconnected) -> AsyncIterator[StreamEvent]:
        """종목의 SSE 이벤트 스트림. 종목코드가 잘못되면 스트림을 열기 전에 InvalidSymbolError.

        connected → (최신값이 있으면) trade·orderbook → 실시간 이벤트, 15초마다 ping.
        KIS 가 설정되지 않았으면 30초마다 ping 만 보낸다 (프론트가 연결 오류로 보지 않게 하는 Django 동작).
        """
        _check_symbol(symbol)
        if not self._is_configured:
            logger.warning("KIS 미설정: ping 만 보내는 스트림 (symbol=%s)", symbol)
            return self._ping_only(is_disconnected)
        return self._quote_events(symbol, is_disconnected)

    async def _quote_events(self, symbol: str, is_disconnected: IsDisconnected) -> AsyncIterator[StreamEvent]:
        queue = self._kis_stream.subscribe(symbol)
        try:
            yield "connected", {"symbol": symbol}
            snapshot = self._kis_stream.get_snapshot(symbol)
            if snapshot and snapshot["trade"]:
                yield "trade", snapshot["trade"]
            if snapshot and snapshot["orderbook"]:
                yield "orderbook", snapshot["orderbook"]

            loop = asyncio.get_running_loop()
            next_ping = loop.time() + self._ping_interval
            while not await is_disconnected():
                # 이벤트를 기다리되 1초마다(또는 ping 시각에) 깨어나 연결 끊김을 확인한다
                timeout = max(0.0, min(_DISCONNECT_CHECK_INTERVAL, next_ping - loop.time()))
                try:
                    event = await asyncio.wait_for(queue.get(), timeout=timeout)
                except TimeoutError:
                    event = None
                if event is not None:
                    yield event
                if loop.time() >= next_ping:
                    yield "ping", {}
                    next_ping = loop.time() + self._ping_interval
        finally:
            self._kis_stream.unsubscribe(symbol, queue)
            logger.info("SSE 연결 종료: symbol=%s", symbol)

    async def _ping_only(self, is_disconnected: IsDisconnected) -> AsyncIterator[StreamEvent]:
        loop = asyncio.get_running_loop()
        while not await is_disconnected():
            yield "ping", {}
            next_ping = loop.time() + self._degraded_ping_interval
            while loop.time() < next_ping:
                if await is_disconnected():
                    return
                await asyncio.sleep(min(_DISCONNECT_CHECK_INTERVAL, next_ping - loop.time()))


def _check_symbol(symbol: str) -> None:
    if not (len(symbol) == 6 and symbol.isdigit()):
        raise InvalidSymbolError("유효하지 않은 종목코드입니다. 6자리 숫자를 입력하세요.")
