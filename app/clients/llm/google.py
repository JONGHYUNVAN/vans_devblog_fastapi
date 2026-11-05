"""Google Gemini generateContent 클라이언트. REST(httpx2) 로 부른다 (설계 06 문서 2절)."""

from typing import Any

import httpx2

from app.clients.llm.base import LLMCitation, LLMMessage, LLMParams, LLMResponse, LLMUsage
from app.clients.llm.http import post_json

GOOGLE_BASE_URL = "https://generativelanguage.googleapis.com/v1beta"
_ROLES = {"user": "user", "assistant": "model"}


class GoogleClient:
    """responseMimeType=application/json + responseJsonSchema 로 계약 JSON 을 받는다.

    effort 는 thinkingConfig.thinkingLevel 이다. 응답 이어 받기(CONTINUATION)는 다루지 않으므로 비스트리밍 상한 32,768 을 넘지 않는다
    (라운드 생성이 검사한다). 출력 한도는 생각 토큰과 공유한다. 생각만 하다 끝나면 본문이 비고 finishReason 이 MAX_TOKENS 다.
    """

    def __init__(
        self, api_key: str, base_url: str = GOOGLE_BASE_URL, transport: httpx2.AsyncBaseTransport | None = None
    ) -> None:
        self._api_key = api_key
        self._base_url = base_url.rstrip("/")
        self._transport = transport

    async def complete(
        self, messages: list[LLMMessage], schema: dict[str, Any] | None, params: LLMParams, timeout: float
    ) -> LLMResponse:
        system = "\n\n".join(message.content for message in messages if message.role == "system")
        generation_config: dict[str, Any] = {"maxOutputTokens": params.max_output_tokens}
        if params.temperature is not None:
            generation_config["temperature"] = params.temperature
        if params.effort is not None:
            generation_config["thinkingConfig"] = {"thinkingLevel": params.effort.upper()}
        if schema is not None:
            generation_config["responseMimeType"] = "application/json"
            generation_config["responseJsonSchema"] = schema
        body: dict[str, Any] = {
            "contents": [
                {"role": _ROLES[message.role], "parts": [{"text": message.content}]}
                for message in messages
                if message.role != "system"
            ],
            "generationConfig": generation_config,
        }
        if system:
            body["systemInstruction"] = {"parts": [{"text": system}]}
        if params.web_search:
            body["tools"] = [{"google_search": {}}]
        data = await post_json(
            "google",
            f"{self._base_url}/models/{params.model_id}:generateContent",
            headers={"x-goog-api-key": self._api_key},
            body=body,
            timeout=timeout,
            transport=self._transport,
        )
        candidates = data.get("candidates") or []
        candidate = candidates[0] if candidates else {}
        parts = (candidate.get("content") or {}).get("parts", [])
        text = "".join(part.get("text", "") for part in parts)
        usage = data.get("usageMetadata") or {}
        # 생각(thinking) 토큰도 과금되므로 completion 에 더한다
        completion = usage.get("candidatesTokenCount", 0) + usage.get("thoughtsTokenCount", 0)
        finish_reason = candidate.get("finishReason", "")
        return LLMResponse(
            text=text,
            usage=LLMUsage(prompt_tokens=usage.get("promptTokenCount", 0), completion_tokens=completion),
            stop_reason=finish_reason,
            truncated=finish_reason == "MAX_TOKENS",
            citations=_citations(candidate),
        )


def _citations(candidate: dict[str, Any]) -> list[LLMCitation]:
    """groundingChunks 의 uri 는 리다이렉트 주소이고 title 이 원본 도메인이다."""
    chunks = (candidate.get("groundingMetadata") or {}).get("groundingChunks") or []
    found: dict[str, LLMCitation] = {}
    for chunk in chunks:
        web = chunk.get("web") or {}
        if web.get("uri"):
            found.setdefault(web["uri"], LLMCitation(url=web["uri"], title=web.get("title", "")))
    return list(found.values())
