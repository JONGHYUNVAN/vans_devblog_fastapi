"""xAI chat completions 클라이언트(OpenAI 호환). REST(httpx2) 로 부른다 (설계 06 문서 2절)."""

from typing import Any

import httpx2

from app.clients.llm.base import LLMMessage, LLMParams, LLMResponse, LLMUsage
from app.clients.llm.http import post_json

XAI_BASE_URL = "https://api.x.ai/v1"


class XAIClient:
    """response_format=json_schema 로 계약 JSON 을 받는다."""

    def __init__(
        self, api_key: str, base_url: str = XAI_BASE_URL, transport: httpx2.AsyncBaseTransport | None = None
    ) -> None:
        self._api_key = api_key
        self._base_url = base_url.rstrip("/")
        self._transport = transport

    async def complete(
        self, messages: list[LLMMessage], schema: dict[str, Any], params: LLMParams, timeout: float
    ) -> LLMResponse:
        body: dict[str, Any] = {
            "model": params.model_id,
            "messages": [{"role": message.role, "content": message.content} for message in messages],
            "temperature": params.temperature,
            "max_tokens": params.max_output_tokens,
            "response_format": {"type": "json_schema", "json_schema": {"name": "output", "schema": schema}},
        }
        data = await post_json(
            "xai",
            f"{self._base_url}/chat/completions",
            headers={"Authorization": f"Bearer {self._api_key}"},
            body=body,
            timeout=timeout,
            transport=self._transport,
        )
        choices = data.get("choices") or []
        text = ((choices[0].get("message") or {}).get("content") or "") if choices else ""
        usage = data.get("usage") or {}
        return LLMResponse(
            text=text,
            usage=LLMUsage(
                prompt_tokens=usage.get("prompt_tokens", 0), completion_tokens=usage.get("completion_tokens", 0)
            ),
        )
