"""Google Gemini generateContent 클라이언트. REST(httpx2) 로 부른다 (설계 06 문서 2절)."""

from typing import Any

import httpx2

from app.clients.llm.base import LLMMessage, LLMParams, LLMResponse, LLMUsage
from app.clients.llm.http import post_json

GOOGLE_BASE_URL = "https://generativelanguage.googleapis.com/v1beta"
_ROLES = {"user": "user", "assistant": "model"}


class GoogleClient:
    """responseMimeType=application/json + responseJsonSchema 로 계약 JSON 을 받는다."""

    def __init__(
        self, api_key: str, base_url: str = GOOGLE_BASE_URL, transport: httpx2.AsyncBaseTransport | None = None
    ) -> None:
        self._api_key = api_key
        self._base_url = base_url.rstrip("/")
        self._transport = transport

    async def complete(
        self, messages: list[LLMMessage], schema: dict[str, Any], params: LLMParams, timeout: float
    ) -> LLMResponse:
        system = "\n\n".join(message.content for message in messages if message.role == "system")
        body: dict[str, Any] = {
            "contents": [
                {"role": _ROLES[message.role], "parts": [{"text": message.content}]}
                for message in messages
                if message.role != "system"
            ],
            "generationConfig": {
                "temperature": params.temperature,
                "maxOutputTokens": params.max_output_tokens,
                "responseMimeType": "application/json",
                "responseJsonSchema": schema,
            },
        }
        if system:
            body["systemInstruction"] = {"parts": [{"text": system}]}
        data = await post_json(
            "google",
            f"{self._base_url}/models/{params.model_id}:generateContent",
            headers={"x-goog-api-key": self._api_key},
            body=body,
            timeout=timeout,
            transport=self._transport,
        )
        candidates = data.get("candidates") or []
        parts = (candidates[0].get("content") or {}).get("parts", []) if candidates else []
        text = "".join(part.get("text", "") for part in parts)
        usage = data.get("usageMetadata") or {}
        # 생각(thinking) 토큰도 과금되므로 completion 에 더한다
        completion = usage.get("candidatesTokenCount", 0) + usage.get("thoughtsTokenCount", 0)
        return LLMResponse(
            text=text, usage=LLMUsage(prompt_tokens=usage.get("promptTokenCount", 0), completion_tokens=completion)
        )
