"""Anthropic Messages API 클라이언트. 공식 SDK(anthropic, httpx2 기반)의 async 인터페이스를 쓴다 (설계 06 문서 2절)."""

from typing import Any

import anthropic
import httpx2

from app.clients.llm.base import LLMMessage, LLMParams, LLMResponse, LLMUsage
from app.core.exceptions import LLMTransportError


class AnthropicClient:
    """구조화 출력(output_config.format = json_schema)으로 계약 JSON 을 받는다.

    재시도는 AgentCallService 가 하므로 SDK 재시도는 끈다. 현행 API 버전(2026-10-21)은 temperature 를 거부해 보내지 않는다.
    http_client 는 테스트가 MockTransport 를 끼울 때 쓴다.
    """

    def __init__(self, api_key: str, http_client: httpx2.AsyncClient | None = None) -> None:
        self._client = anthropic.AsyncAnthropic(api_key=api_key, max_retries=0, http_client=http_client)

    async def complete(
        self, messages: list[LLMMessage], schema: dict[str, Any], params: LLMParams, timeout: float
    ) -> LLMResponse:
        system = "\n\n".join(message.content for message in messages if message.role == "system")
        chat: list[Any] = [
            {"role": message.role, "content": message.content} for message in messages if message.role != "system"
        ]
        request: dict[str, Any] = {
            "model": params.model_id,
            "max_tokens": params.max_output_tokens,
            "messages": chat,
            "output_config": {"format": {"type": "json_schema", "schema": anthropic.transform_schema(schema)}},
            "timeout": timeout,
        }
        if system:
            request["system"] = system
        try:
            response = await self._client.messages.create(**request)
        except anthropic.APIConnectionError as exc:  # 타임아웃(APITimeoutError) 포함
            raise LLMTransportError(f"anthropic: {type(exc).__name__}: {exc}") from exc
        except anthropic.APIStatusError as exc:
            if exc.status_code == 429 or exc.status_code >= 500:
                raise LLMTransportError(
                    f"anthropic: HTTP {exc.status_code}: {exc.message}", rate_limited=exc.status_code == 429
                ) from exc
            raise ValueError(f"anthropic rejected the request (HTTP {exc.status_code}): {exc.message}") from exc
        text = "".join(block.text for block in response.content if block.type == "text")
        return LLMResponse(
            text=text,
            usage=LLMUsage(prompt_tokens=response.usage.input_tokens, completion_tokens=response.usage.output_tokens),
        )
