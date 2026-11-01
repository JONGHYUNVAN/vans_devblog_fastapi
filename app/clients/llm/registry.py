"""provider 문자열 → LLM 클라이언트와 벤더별 동시 호출 세마포어 (설계 06 문서 2절, 04 문서 1절)."""

import asyncio
from collections.abc import Mapping

from app.clients.llm.base import LLMClient


class LLMClientRegistry:
    """lifespan 이 하나 만들어 호출 서비스에 넘긴다. 세마포어는 벤더마다 하나라 이벤트 루프 하나를 전제한다."""

    def __init__(self, clients: Mapping[str, LLMClient], concurrency_per_provider: int) -> None:
        if concurrency_per_provider <= 0:
            raise ValueError("concurrency_per_provider must be positive")
        self._clients = dict(clients)
        self._semaphores = {provider: asyncio.Semaphore(concurrency_per_provider) for provider in self._clients}

    @property
    def providers(self) -> list[str]:
        return list(self._clients)

    def get(self, provider: str) -> LLMClient:
        """없는 provider 는 설정 오류라 KeyError 를 그대로 둔다. 파이프라인은 INTERNAL_ERROR 로 멈춘다."""
        return self._clients[provider]

    def semaphore(self, provider: str) -> asyncio.Semaphore:
        return self._semaphores[provider]
