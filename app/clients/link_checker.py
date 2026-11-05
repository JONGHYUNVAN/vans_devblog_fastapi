"""본문 링크의 응답 코드 확인. 네트워크 결과라 게이트에 넣지 않고 사실 확인 입력으로만 쓴다.

본문은 LLM 이 쓴 글이라 링크를 믿지 않는다. 공인 주소가 아니면 요청하지 않고, 리다이렉트를 따라가지 않으며, 본문을 읽지 않는다.
검사와 요청이 DNS 를 따로 해석하므로 DNS 리바인딩은 막지 못한다. 드러나는 것은 응답 코드뿐이다.
"""

import asyncio
import ipaddress
import logging
import socket
from urllib.parse import urlparse

import httpx2

logger = logging.getLogger(__name__)

_MAX_LINKS = 20
_TIMEOUT_S = 5.0
_HEAD_NOT_ALLOWED = 405


class LinkChecker:
    """check 는 URL 마다 응답 코드를 돌려준다. 연결 실패나 요청하지 않은 주소는 None 이다. 예외를 던지지 않는다."""

    def __init__(self, transport: httpx2.AsyncBaseTransport | None = None, require_public: bool = True) -> None:
        self._transport = transport
        self._require_public = require_public

    async def check(self, urls: list[str]) -> dict[str, int | None]:
        targets = [url for url in urls if url.startswith(("http://", "https://"))][:_MAX_LINKS]
        async with httpx2.AsyncClient(transport=self._transport, timeout=_TIMEOUT_S, follow_redirects=False) as client:
            statuses = await asyncio.gather(*(self._status(client, url) for url in targets))
        return dict(zip(targets, statuses, strict=True))

    async def _status(self, client: httpx2.AsyncClient, url: str) -> int | None:
        try:
            if self._require_public and not await _resolves_to_public_address(url):
                return None
            response = await client.head(url)
            if response.status_code == _HEAD_NOT_ALLOWED:
                async with client.stream("GET", url) as streamed:
                    return streamed.status_code
            return response.status_code
        except (httpx2.HTTPError, httpx2.InvalidURL, OSError, ValueError) as exc:
            logger.info("link check failed for %s: %s", url, type(exc).__name__)
            return None


async def _resolves_to_public_address(url: str) -> bool:
    host = urlparse(url).hostname
    if not host:
        return False
    addresses = await asyncio.get_running_loop().getaddrinfo(host, None, type=socket.SOCK_STREAM)
    return bool(addresses) and all(ipaddress.ip_address(info[4][0]).is_global for info in addresses)
