"""KIS WebSocket 접속키(approval_key) 발급."""

import asyncio
import logging

import httpx2

from app.core.cache import KIS_APPROVAL_TTL, cache

logger = logging.getLogger(__name__)

_CACHE_KEY = "kis_approval_key"
_REQUEST_TIMEOUT = 10


class KISAuthClient:
    """approval_key 를 발급받아 23시간 재사용한다. 앱 키·시크릿이 없거나 발급에 실패하면 None (구독을 건너뛴다)."""

    def __init__(
        self,
        app_key: str,
        app_secret: str,
        rest_url: str,
        transport: httpx2.AsyncBaseTransport | None = None,
    ) -> None:
        self._app_key = app_key
        self._app_secret = app_secret
        self._rest_url = rest_url
        self._transport = transport
        # 동시에 들어온 구독이 키를 여러 번 발급받지 않게 한다
        self._lock = asyncio.Lock()

    async def get_approval_key(self) -> str | None:
        if not (self._app_key and self._app_secret):
            logger.warning("KIS_APP_KEY 또는 KIS_APP_SECRET 이 설정되지 않았다")
            return None

        async with self._lock:
            cached: str | None = cache.get(_CACHE_KEY)
            if cached:
                return cached
            try:
                async with httpx2.AsyncClient(transport=self._transport, timeout=_REQUEST_TIMEOUT) as client:
                    response = await client.post(
                        f"{self._rest_url}/oauth2/Approval",
                        json={
                            "grant_type": "client_credentials",
                            "appkey": self._app_key,
                            "secretkey": self._app_secret,
                        },
                    )
                    response.raise_for_status()
                    approval_key = response.json().get("approval_key")
            except (httpx2.HTTPError, ValueError) as exc:
                logger.error("approval_key 발급 실패: %s", exc)
                return None
            if not approval_key:
                logger.error("approval_key 발급 응답에 키가 없다")
                return None
            cache.set(_CACHE_KEY, approval_key, KIS_APPROVAL_TTL)
            logger.info("approval_key 발급")
            return approval_key
