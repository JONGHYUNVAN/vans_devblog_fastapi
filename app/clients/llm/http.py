"""REST 로 부르는 벤더(google·xai)가 같이 쓰는 JSON POST 와 실패 분류 (설계 06 문서 2절 clients/llm)."""

from typing import Any

import httpx2

from app.core.exceptions import LLMTransportError

_RATE_LIMITED = 429


async def post_json(
    vendor: str,
    url: str,
    *,
    headers: dict[str, str],
    body: dict[str, Any],
    timeout: float,
    transport: httpx2.AsyncBaseTransport | None,
) -> dict[str, Any]:
    """응답 JSON 을 돌려준다.

    네트워크·타임아웃·429·5xx 는 LLMTransportError(재시도 가능), 그 밖의 4xx 와 JSON 이 아닌 본문은 ValueError(설정 오류).
    """
    try:
        async with httpx2.AsyncClient(transport=transport, timeout=timeout) as client:
            response = await client.post(url, headers=headers, json=body)
    except httpx2.HTTPError as exc:
        raise LLMTransportError(f"{vendor}: {type(exc).__name__}: {exc}") from exc
    if response.status_code == _RATE_LIMITED or response.status_code >= 500:
        raise LLMTransportError(
            f"{vendor}: HTTP {response.status_code}: {response.text[:200]}",
            rate_limited=response.status_code == _RATE_LIMITED,
        )
    if response.status_code >= 400:
        raise ValueError(f"{vendor} rejected the request (HTTP {response.status_code}): {response.text[:500]}")
    try:
        return response.json()
    except ValueError as exc:
        raise ValueError(f"{vendor} returned a non-JSON body: {response.text[:200]}") from exc
