"""KIS 실시간 WebSocket 연결 관리. 체결가·호가를 받아 최신값을 두고 구독자 큐로 나눠 준다."""

import asyncio
import base64
import contextlib
import json
import logging
from collections import defaultdict
from collections.abc import Coroutine
from datetime import UTC, datetime
from typing import Any

from Crypto.Cipher import AES
from Crypto.Util.Padding import unpad
from websockets.asyncio.client import ClientConnection, connect

from app.clients.kis.auth import KISAuthClient
from app.clients.kis.constants import (
    KIS_REST_URL_MOCK,
    KIS_REST_URL_REAL,
    KIS_WS_URL_MOCK,
    KIS_WS_URL_REAL,
    TR_ID_ORDERBOOK,
    TR_ID_TRADE,
)
from app.clients.kis.parser import parse_orderbook, parse_trade
from app.core.config import Settings

logger = logging.getLogger(__name__)

QuoteEvent = tuple[str, dict[str, Any]]  # ("trade" | "orderbook", 데이터)

_MAX_RECONNECTS = 10
_RECONNECT_DELAY = 5
_OPEN_TIMEOUT = 30
_CLOSE_TIMEOUT = 10
_QUEUE_SIZE = 100
_SUBSCRIBE = "1"
_UNSUBSCRIBE = "2"


class KISStreamManager:
    """KIS WebSocket 연결 하나를 앱 전체가 나눠 쓴다. lifespan 이 만들어 app.state 에 둔다.

    모든 메서드는 앱 이벤트 루프 안에서만 부른다(락이 필요 없다). 연결은 첫 구독 때 시작하고, 끊기면 5초 뒤
    다시 연결한다. 연속 실패가 10번을 넘으면 멈추고, 다음 구독이 다시 시작한다.
    KIS 에는 구독자가 있는 종목만 구독한다. 구독자가 0명이 돼도 연결은 유지한다 (Django 동작 그대로).
    """

    def __init__(self, auth_client: KISAuthClient, ws_url: str, reconnect_delay: float = _RECONNECT_DELAY) -> None:
        self._auth_client = auth_client
        self._ws_url = ws_url
        self._reconnect_delay = reconnect_delay
        self._ws: ClientConnection | None = None
        self._task: asyncio.Task[None] | None = None
        self._background: set[asyncio.Task[None]] = set()
        self._queues: defaultdict[str, set[asyncio.Queue[QuoteEvent]]] = defaultdict(set)
        self._trades: dict[str, dict[str, Any]] = {}
        self._orderbooks: dict[str, dict[str, Any]] = {}
        self._aes_keys: dict[str, tuple[str, str]] = {}  # tr_id → (iv, key). 구독 응답으로 받는다

    def subscribe(self, symbol: str) -> asyncio.Queue[QuoteEvent]:
        """종목의 실시간 이벤트를 받을 큐. 구독자가 느려 큐가 차면 이벤트를 버린다 (Django 와 같다)."""
        queue: asyncio.Queue[QuoteEvent] = asyncio.Queue(maxsize=_QUEUE_SIZE)
        is_first = not self._queues[symbol]
        self._queues[symbol].add(queue)
        self.ensure_connected()
        if is_first:
            self._spawn(self._send_subscription(symbol, _SUBSCRIBE))
        return queue

    def unsubscribe(self, symbol: str, queue: asyncio.Queue[QuoteEvent]) -> None:
        """큐를 뺀다. 종목의 마지막 구독자였으면 KIS 구독도 해제한다."""
        queues = self._queues.get(symbol)
        if queues is None or queue not in queues:
            return
        queues.discard(queue)
        if not queues:
            del self._queues[symbol]
            self._spawn(self._send_subscription(symbol, _UNSUBSCRIBE))

    def get_snapshot(self, symbol: str) -> dict[str, Any] | None:
        """마지막으로 받은 체결가·호가. 둘 다 없으면 None. updatedAt 은 데이터 시각이 아니라 조회 시각이다 (계약)."""
        trade = self._trades.get(symbol)
        orderbook = self._orderbooks.get(symbol)
        if trade is None and orderbook is None:
            return None
        return {"trade": trade, "orderbook": orderbook, "updatedAt": datetime.now(UTC).isoformat()}

    def ensure_connected(self) -> None:
        """연결 태스크가 없거나 끝났으면 시작한다."""
        if self._task is None or self._task.done():
            self._task = asyncio.create_task(self._run(), name="kis-websocket")

    async def close(self) -> None:
        """연결을 정상 종료(1000)하고 진행 중인 전송을 멈춘다. lifespan 종료 때 부른다."""
        for task in self._background:
            task.cancel()
        # 태스크를 먼저 취소하면 websockets 가 1011 로 끊는다. 먼저 닫아야 KIS 세션이 깔끔히 끝나 재배포 후 바로 다시 붙는다.
        if self._ws is not None:
            await self._ws.close()
        if self._task is not None:
            self._task.cancel()
        await asyncio.gather(*self._background, *([self._task] if self._task else []), return_exceptions=True)
        self._task = None

    async def _run(self) -> None:
        failures = 0
        while True:
            try:
                async with connect(
                    self._ws_url,
                    ping_interval=None,  # KIS 는 자체 PINGPONG 메시지를 쓴다
                    ping_timeout=None,
                    open_timeout=_OPEN_TIMEOUT,
                    close_timeout=_CLOSE_TIMEOUT,
                ) as ws:
                    self._ws = ws
                    failures = 0
                    logger.info("KIS WebSocket 연결")
                    for symbol in list(self._queues):
                        await self._send_subscription(symbol, _SUBSCRIBE)
                    async for raw in ws:
                        await self._on_message(raw)
                logger.info("KIS WebSocket 이 연결을 닫았다")
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # 연결 경계: 어떤 실패든 재연결로 처리한다
                failures += 1
                if failures > _MAX_RECONNECTS:
                    logger.error("KIS WebSocket 재연결 %d회 실패. 다음 구독 때 다시 시작한다: %s", _MAX_RECONNECTS, exc)
                    return
                logger.warning("KIS WebSocket 끊김 (%d/%d): %s", failures, _MAX_RECONNECTS, exc)
            finally:
                self._ws = None
            # Django 버전은 서버가 정상 종료하면 기다리지 않고 바로 다시 붙었다. 끊길 때마다 쉬어서 재접속 폭주를 막는다.
            await asyncio.sleep(self._reconnect_delay)

    async def _send_subscription(self, symbol: str, tr_type: str) -> None:
        """체결가·호가 구독(tr_type "1") 또는 해제("2"). 연결이 없으면 건너뛴다 — 연결되면 구독자 종목을 다시 구독한다."""
        if self._ws is None:
            return
        approval_key = await self._auth_client.get_approval_key()
        ws = self._ws
        if not approval_key or ws is None:
            return
        # 키를 기다리는 사이 구독자가 다시 생기거나 모두 떠났으면 보내지 않는다 (해제·구독 순서가 뒤바뀌지 않게)
        if (tr_type == _SUBSCRIBE) != (symbol in self._queues):
            return
        for tr_id in (TR_ID_TRADE, TR_ID_ORDERBOOK):
            message = {
                "header": {"approval_key": approval_key, "custtype": "P", "tr_type": tr_type, "content-type": "utf-8"},
                "body": {"input": {"tr_id": tr_id, "tr_key": symbol}},
            }
            try:
                await ws.send(json.dumps(message))
            except Exception as exc:  # 연결이 끊기는 중이면 재연결 때 다시 구독한다
                logger.warning("KIS 구독 메시지 전송 실패 (%s %s): %s", tr_id, symbol, exc)
                return

    async def _on_message(self, raw: str | bytes) -> None:
        """제어 메시지(JSON)와 데이터 메시지("암호화여부|tr_id|건수|본문")를 나눠 처리한다. 잘못된 메시지는 로그만 남긴다."""
        text = raw.decode() if isinstance(raw, bytes) else raw
        try:
            if text.startswith("{"):
                await self._on_control(json.loads(text), text)
            else:
                self._on_data(text)
        except Exception as exc:  # 메시지 하나가 연결 전체를 끊지 않게 한다
            logger.error("KIS 메시지 처리 오류: %s | %s", exc, text[:200])

    async def _on_control(self, message: dict[str, Any], text: str) -> None:
        header = message.get("header", {})
        tr_id = header.get("tr_id", "")
        if tr_id == "PINGPONG":
            if self._ws is not None:
                await self._ws.send(text)  # 받은 그대로 돌려줘야 KIS 가 연결을 유지한다
            return
        output = message.get("body", {}).get("output") or {}
        if output.get("iv") and output.get("key"):
            self._aes_keys[tr_id] = (output["iv"], output["key"])
        logger.info("KIS 구독 응답: tr_id=%s msg=%s", tr_id, message.get("body", {}).get("msg1"))

    def _on_data(self, text: str) -> None:
        parts = text.split("|")
        if len(parts) < 4:
            return
        is_encrypted, tr_id, body = parts[0] == "1", parts[1], parts[3]
        if is_encrypted:
            aes_key = self._aes_keys.get(tr_id)
            if aes_key is None:
                logger.warning("암호화 데이터를 받았지만 AES 키가 없다: tr_id=%s", tr_id)
                return
            body = _decrypt(body, *aes_key)

        if tr_id == TR_ID_TRADE:
            trade = parse_trade(body)
            if trade:
                self._trades[trade["symbol"]] = trade
                self._publish(trade["symbol"], ("trade", trade))
        elif tr_id == TR_ID_ORDERBOOK:
            orderbook = parse_orderbook(body)
            if orderbook:
                self._orderbooks[orderbook["symbol"]] = orderbook
                self._publish(orderbook["symbol"], ("orderbook", orderbook))

    def _publish(self, symbol: str, event: QuoteEvent) -> None:
        for queue in self._queues.get(symbol, ()):
            with contextlib.suppress(asyncio.QueueFull):
                queue.put_nowait(event)

    def _spawn(self, coroutine: Coroutine[Any, Any, None]) -> None:
        # 태스크 참조를 쥐고 있어야 끝나기 전에 GC 되지 않는다
        task = asyncio.create_task(coroutine)
        self._background.add(task)
        task.add_done_callback(self._background.discard)


def create_kis_stream(settings: Settings) -> KISStreamManager:
    """설정(KIS_IS_REAL)에 맞는 실전·모의 서버로 매니저를 만든다. 연결은 첫 구독 때 한다."""
    rest_url, ws_url = (
        (KIS_REST_URL_REAL, KIS_WS_URL_REAL) if settings.kis_is_real else (KIS_REST_URL_MOCK, KIS_WS_URL_MOCK)
    )
    auth_client = KISAuthClient(settings.kis_app_key, settings.kis_app_secret, rest_url)
    return KISStreamManager(auth_client, ws_url)


def _decrypt(ciphertext_b64: str, iv: str, key: str) -> str:
    """AES-256-CBC + PKCS7. 키·IV 는 구독 응답으로 받은 문자열 그대로다."""
    cipher = AES.new(key.encode(), AES.MODE_CBC, iv.encode())
    return unpad(cipher.decrypt(base64.b64decode(ciphertext_b64)), AES.block_size).decode()
