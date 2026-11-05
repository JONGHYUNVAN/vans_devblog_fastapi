"""xAI Responses API 클라이언트. REST(httpx2) 로 부른다 (설계 06 문서 2절). /chat/completions 는 xAI 가 legacy 로 표시했다."""

from typing import Any

import httpx2

from app.clients.llm.base import LLMCitation, LLMMessage, LLMParams, LLMResponse, LLMUsage
from app.clients.llm.http import post_json

XAI_BASE_URL = "https://api.x.ai/v1"


class XAIClient:
    """text.format=json_schema 로 계약 JSON 을 받는다. effort 는 reasoning.effort 다(끌 수 없고 기본 high).

    max_output_tokens 는 보이는 출력만 세므로 추론이 한도를 먹지 않는다. 호출을 서버에 저장하지 않는다(store=false).
    """

    def __init__(
        self, api_key: str, base_url: str = XAI_BASE_URL, transport: httpx2.AsyncBaseTransport | None = None
    ) -> None:
        self._api_key = api_key
        self._base_url = base_url.rstrip("/")
        self._transport = transport

    async def complete(
        self, messages: list[LLMMessage], schema: dict[str, Any] | None, params: LLMParams, timeout: float
    ) -> LLMResponse:
        system = "\n\n".join(message.content for message in messages if message.role == "system")
        body: dict[str, Any] = {
            "model": params.model_id,
            "input": [
                {"role": message.role, "content": message.content} for message in messages if message.role != "system"
            ],
            "max_output_tokens": params.max_output_tokens,
            "store": False,
        }
        if system:
            body["instructions"] = system
        if params.temperature is not None:
            body["temperature"] = params.temperature
        if params.effort is not None:
            body["reasoning"] = {"effort": params.effort}
        if schema is not None:
            body["text"] = {"format": {"type": "json_schema", "name": "output", "schema": schema}}
        if params.web_search:
            body["tools"] = [{"type": "web_search"}]
            body["include"] = [
                "no_inline_citations"
            ]  # 본문에 [[1]](url) 이 끼면 다음 단계가 읽기 나쁘다. 출처는 필드로 받는다
        data = await post_json(
            "xai",
            f"{self._base_url}/responses",
            headers={"Authorization": f"Bearer {self._api_key}"},
            body=body,
            timeout=timeout,
            transport=self._transport,
        )
        contents = [
            content
            for item in data.get("output") or []
            if item.get("type") == "message"
            for content in item.get("content") or []
            if content.get("type") == "output_text"
        ]
        usage = data.get("usage") or {}
        status = data.get("status", "")
        incomplete_reason = (data.get("incomplete_details") or {}).get("reason", "")
        return LLMResponse(
            text="".join(content.get("text", "") for content in contents),
            # 문서의 스키마는 input_tokens, 문서의 응답 예시는 prompt_tokens 다. 둘 다 받는다
            usage=LLMUsage(
                prompt_tokens=usage.get("input_tokens", usage.get("prompt_tokens", 0)),
                completion_tokens=usage.get("output_tokens", usage.get("completion_tokens", 0)),
            ),
            stop_reason=incomplete_reason or status,
            truncated=status == "incomplete" and "max_output_tokens" in incomplete_reason,
            citations=_citations(contents, data.get("citations") or []),
        )


def _citations(contents: list[dict[str, Any]], listed: list[Any]) -> list[LLMCitation]:
    """본문 annotations 의 url_citation 과 응답의 citations 목록을 합친다. 위치 정보는 쓰지 않는다."""
    found: dict[str, LLMCitation] = {}
    for content in contents:
        for annotation in content.get("annotations") or []:
            if annotation.get("type") == "url_citation" and annotation.get("url"):
                found.setdefault(
                    annotation["url"], LLMCitation(url=annotation["url"], title=annotation.get("title", ""))
                )
    for each in listed:
        url = each if isinstance(each, str) else (each.get("url") if isinstance(each, dict) else None)
        if url:
            found.setdefault(url, LLMCitation(url=url))
    return list(found.values())
