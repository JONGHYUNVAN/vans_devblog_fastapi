"""provider 문자열 → LLM 클라이언트와 벤더별 동시 호출 제한 (설계 06 문서 2절, 04 문서 1절·9절)."""

import asyncio
from collections.abc import AsyncIterator, Mapping
from contextlib import asynccontextmanager

from app.clients.llm.base import LLMClient


class ProviderGate:
    """벤더 하나의 동시 호출 제한. 429 뒤 cooldown 동안은 한 번에 1건만 보낸다 (04 문서 9절)."""

    def __init__(self, concurrency: int) -> None:
        self._semaphore = asyncio.Semaphore(concurrency)
        self._serial = asyncio.Lock()
        self._throttled_until = 0.0  # 이벤트 루프 시각

    @property
    def throttled(self) -> bool:
        return asyncio.get_running_loop().time() < self._throttled_until

    def throttle(self, seconds: float) -> None:
        """지금부터 seconds 동안 1건씩만 보낸다. 이미 줄어 있으면 끝나는 시각만 늦춘다."""
        self._throttled_until = max(self._throttled_until, asyncio.get_running_loop().time() + seconds)

    @asynccontextmanager
    async def slot(self) -> AsyncIterator[None]:
        async with self._semaphore:
            if self.throttled:
                async with self._serial:
                    yield
            else:
                yield


class LLMClientRegistry:
    """lifespan 이 하나 만들어 호출 서비스에 넘긴다. 게이트는 벤더마다 하나라 이벤트 루프 하나를 전제한다."""

    def __init__(self, clients: Mapping[str, LLMClient], concurrency_per_provider: int) -> None:
        if concurrency_per_provider <= 0:
            raise ValueError("concurrency_per_provider must be positive")
        self._clients = dict(clients)
        self._gates = {provider: ProviderGate(concurrency_per_provider) for provider in self._clients}

    @property
    def providers(self) -> list[str]:
        return list(self._clients)

    def get(self, provider: str) -> LLMClient:
        """없는 provider 는 설정 오류라 KeyError 를 그대로 둔다. 파이프라인은 INTERNAL_ERROR 로 멈춘다."""
        return self._clients[provider]

    def gate(self, provider: str) -> ProviderGate:
        return self._gates[provider]
