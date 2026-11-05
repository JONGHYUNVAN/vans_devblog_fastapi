"""Anthropic Messages API 클라이언트. 공식 SDK(anthropic, httpx2 기반)의 async 인터페이스를 쓴다 (설계 06 문서 2절)."""

import time
from typing import Any

import anthropic
import httpx2

from app.clients.llm.base import LLMCitation, LLMMessage, LLMParams, LLMResponse, LLMUsage
from app.core.exceptions import LLMTransportError

_WEB_SEARCH_TOOL = {"type": "web_search_20260209", "name": "web_search"}
_MAX_CONTINUATIONS = 5  # 긴 검색이 pause_turn 으로 멈췄을 때 이어 가는 횟수


class AnthropicClient:
    """구조화 출력(output_config.format = json_schema)으로 계약 JSON 을 받는다.

    재시도는 AgentCallService 가 하므로 SDK 재시도는 끈다. 현행 API 버전(2026-10-21)은 temperature 를 400 으로 거부해 보내지 않는다.
    effort 는 output_config.effort 로 보낸다. 웹 검색은 인용이 항상 켜져 있어 구조화 출력과 함께 쓰면 400 이다. 서비스가 schema=None 으로 부른다.
    http_client 는 테스트가 MockTransport 를 끼울 때 쓴다.
    """

    def __init__(self, api_key: str, http_client: httpx2.AsyncClient | None = None) -> None:
        self._client = anthropic.AsyncAnthropic(api_key=api_key, max_retries=0, http_client=http_client)

    async def complete(
        self, messages: list[LLMMessage], schema: dict[str, Any] | None, params: LLMParams, timeout: float
    ) -> LLMResponse:
        system = "\n\n".join(message.content for message in messages if message.role == "system")
        chat: list[Any] = [
            {"role": message.role, "content": message.content} for message in messages if message.role != "system"
        ]
        output_config: dict[str, Any] = {}
        if schema is not None:
            output_config["format"] = {"type": "json_schema", "schema": anthropic.transform_schema(schema)}
        if params.effort is not None:
            output_config["effort"] = params.effort
        request: dict[str, Any] = {"model": params.model_id, "max_tokens": params.max_output_tokens}
        if output_config:
            request["output_config"] = output_config
        if system:
            request["system"] = system
        if params.web_search:
            request["tools"] = [_WEB_SEARCH_TOOL]

        prompt_tokens = completion_tokens = 0
        blocks: list[Any] = []
        deadline = time.monotonic() + timeout  # 이어 받기까지 합쳐 호출 하나의 제한 시간이다
        for _ in range(_MAX_CONTINUATIONS + 1):
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise LLMTransportError("anthropic: timed out across pause_turn continuations")
            response = await self._create(request | {"messages": chat, "timeout": remaining})
            prompt_tokens += response.usage.input_tokens
            completion_tokens += response.usage.output_tokens
            blocks += response.content
            if response.stop_reason != "pause_turn":
                break
            # 서버 측 검색 루프가 멈췄다. 응답을 그대로 붙여 다시 보내면 이어 간다. "계속" 같은 사용자 메시지는 넣지 않는다
            chat = [*chat, {"role": "assistant", "content": response.content}]
        else:
            raise LLMTransportError(f"anthropic: still paused after {_MAX_CONTINUATIONS} continuations")
        _raise_if_search_failed(blocks, params)
        return LLMResponse(
            text="".join(block.text for block in blocks if block.type == "text"),
            usage=LLMUsage(prompt_tokens=prompt_tokens, completion_tokens=completion_tokens),
            stop_reason=response.stop_reason or "",
            truncated=response.stop_reason == "max_tokens",
            citations=_citations(blocks),
        )

    async def _create(self, request: dict[str, Any]) -> Any:
        try:
            return await self._client.messages.create(**request)
        except anthropic.APIConnectionError as exc:  # 타임아웃(APITimeoutError) 포함
            raise LLMTransportError(f"anthropic: {type(exc).__name__}: {exc}") from exc
        except anthropic.APIStatusError as exc:
            if exc.status_code == 429 or exc.status_code >= 500:
                raise LLMTransportError(
                    f"anthropic: HTTP {exc.status_code}: {exc.message}", rate_limited=exc.status_code == 429
                ) from exc
            raise ValueError(f"anthropic rejected the request (HTTP {exc.status_code}): {exc.message}") from exc


def _raise_if_search_failed(blocks: list[Any], params: LLMParams) -> None:
    """검색 도구 오류는 HTTP 200 안의 결과 블록으로 온다. 성공한 검색이 하나도 없으면 호출 실패로 본다."""
    if not params.web_search:
        return
    results = [block for block in blocks if block.type == "web_search_tool_result"]
    errors = [block for block in results if not isinstance(block.content, list)]
    if results and len(errors) == len(results):
        raise LLMTransportError(f"anthropic: web search failed: {errors[0].content.error_code}")


def _citations(blocks: list[Any]) -> list[LLMCitation]:
    found: dict[str, LLMCitation] = {}
    for block in blocks:
        for citation in getattr(block, "citations", None) or []:
            url = getattr(citation, "url", None)
            if url:
                found.setdefault(url, LLMCitation(url=url, title=getattr(citation, "title", "") or ""))
    return list(found.values())
