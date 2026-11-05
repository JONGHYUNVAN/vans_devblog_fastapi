"""AnthropicClient 테스트. SDK 의 HTTP 는 httpx2.MockTransport 로 대신한다."""

import json

import anthropic
import httpx2
import pytest

from app.clients.llm.anthropic import AnthropicClient
from app.clients.llm.base import LLMMessage, LLMParams
from app.core.exceptions import LLMTransportError

pytestmark = pytest.mark.anyio

MESSAGES = [LLMMessage(role="system", content="너는 작성자다"), LLMMessage(role="user", content="써라")]
SCHEMA = {"type": "object", "properties": {"title": {"type": "string"}}, "required": ["title"], "title": "Out"}
PARAMS = LLMParams(model_id="claude-test", temperature=0.7, max_output_tokens=500)


def _message(text: str) -> dict:
    return {
        "id": "msg_1",
        "type": "message",
        "role": "assistant",
        "model": "claude-test",
        "content": [{"type": "text", "text": text}],
        "stop_reason": "end_turn",
        "stop_sequence": None,
        "usage": {"input_tokens": 12, "output_tokens": 7},
    }


def _client(handler) -> AnthropicClient:
    return AnthropicClient(
        "sk-test", http_client=anthropic.DefaultAsyncHttpxClient(transport=httpx2.MockTransport(handler))
    )


async def test_complete_sends_structured_output_request_and_maps_usage():
    requests: list[httpx2.Request] = []

    def handler(request: httpx2.Request) -> httpx2.Response:
        requests.append(request)
        return httpx2.Response(200, json=_message('{"title": "제목"}'))

    response = await _client(handler).complete(MESSAGES, SCHEMA, PARAMS, timeout=30)

    assert response.text == '{"title": "제목"}'
    assert (response.usage.prompt_tokens, response.usage.completion_tokens) == (12, 7)
    [request] = requests
    assert request.url.path == "/v1/messages"
    # 키는 SDK 가 Authorization 헤더로 보낸다
    assert "authorization" in request.headers and request.headers["anthropic-version"]
    body = json.loads(request.content)
    assert (body["model"], body["max_tokens"], body["system"]) == ("claude-test", 500, "너는 작성자다")
    assert body["messages"] == [{"role": "user", "content": "써라"}]
    assert body["output_config"]["format"]["type"] == "json_schema"
    assert body["output_config"]["format"]["schema"]["required"] == ["title"]
    assert "temperature" not in body  # 현행 API 버전은 sampling 파라미터를 거부한다


@pytest.mark.parametrize("status", [429, 500, 529])
async def test_retryable_statuses_raise_transport_error(status):
    client = _client(lambda request: httpx2.Response(status, json={"error": {"type": "x", "message": "busy"}}))

    with pytest.raises(LLMTransportError) as info:
        await client.complete(MESSAGES, SCHEMA, PARAMS, timeout=30)

    assert info.value.rate_limited is (status == 429)


async def test_timeout_raises_transport_error():
    def handler(request: httpx2.Request) -> httpx2.Response:
        raise httpx2.ReadTimeout("slow")

    with pytest.raises(LLMTransportError):
        await _client(handler).complete(MESSAGES, SCHEMA, PARAMS, timeout=30)


async def test_bad_request_raises_value_error_without_retry_semantics():
    client = _client(
        lambda request: httpx2.Response(400, json={"error": {"type": "invalid_request_error", "message": "bad schema"}})
    )

    with pytest.raises(ValueError, match="bad schema"):
        await client.complete(MESSAGES, SCHEMA, PARAMS, timeout=30)


def _with(**fields) -> dict:
    return _message("{}") | fields


async def test_effort_goes_to_output_config_and_temperature_is_never_sent():
    requests: list[httpx2.Request] = []

    def handler(request: httpx2.Request) -> httpx2.Response:
        requests.append(request)
        return httpx2.Response(200, json=_message("{}"))

    params = LLMParams(model_id="claude-test", temperature=0.7, effort="high", max_output_tokens=500)
    await _client(handler).complete(MESSAGES, SCHEMA, params, timeout=30)

    body = json.loads(requests[0].content)
    assert body["output_config"]["effort"] == "high"
    assert "format" in body["output_config"]
    assert "temperature" not in body


async def test_max_tokens_stop_reason_is_truncated():
    response = await _client(lambda request: httpx2.Response(200, json=_with(stop_reason="max_tokens"))).complete(
        MESSAGES, SCHEMA, PARAMS, timeout=30
    )

    assert (response.truncated, response.stop_reason) == (True, "max_tokens")


def _search_result() -> dict:
    return {
        "type": "web_search_tool_result",
        "tool_use_id": "srv_1",
        "content": [
            {"type": "web_search_result", "url": "https://docs.test/a", "title": "A", "encrypted_content": "x"}
        ],
    }


def _cited_text() -> dict:
    citation = {
        "type": "web_search_result_location",
        "url": "https://docs.test/a",
        "title": "A",
        "encrypted_index": "x",
        "cited_text": "근거",
    }
    return {"type": "text", "text": "문서와 일치한다", "citations": [citation]}


async def test_web_search_sends_the_tool_without_a_format_and_reads_citations():
    requests: list[httpx2.Request] = []

    def handler(request: httpx2.Request) -> httpx2.Response:
        requests.append(request)
        return httpx2.Response(
            200, json=_with(content=[{"type": "text", "text": "찾아본다. "}, _search_result(), _cited_text()])
        )

    params = LLMParams(model_id="claude-test", max_output_tokens=500, web_search=True)
    response = await _client(handler).complete(MESSAGES, None, params, timeout=30)

    body = json.loads(requests[0].content)
    # 코드 실행 경유(기본값)는 citations 를 주지 않아 직접 호출로 고정한다
    assert body["tools"] == [{"type": "web_search_20260318", "name": "web_search", "allowed_callers": ["direct"]}]
    assert "output_config" not in body  # 인용과 구조화 출력을 함께 쓰면 400 이다
    assert response.text == "찾아본다. 문서와 일치한다"
    assert [(citation.url, citation.title) for citation in response.citations] == [("https://docs.test/a", "A")]


async def test_pause_turn_resends_the_response_and_adds_usage():
    requests: list[httpx2.Request] = []

    def handler(request: httpx2.Request) -> httpx2.Response:
        requests.append(request)
        if len(requests) == 1:
            return httpx2.Response(
                200, json=_with(stop_reason="pause_turn", content=[{"type": "text", "text": "찾는 중"}])
            )
        return httpx2.Response(200, json=_with(content=[_cited_text()]))

    params = LLMParams(model_id="claude-test", max_output_tokens=500, web_search=True)
    response = await _client(handler).complete(MESSAGES, None, params, timeout=30)

    second = json.loads(requests[1].content)
    assert [message["role"] for message in second["messages"]] == [
        "user",
        "assistant",
    ]  # "계속" 같은 메시지를 넣지 않는다
    assert response.text == "찾는 중문서와 일치한다"  # 이어 받은 응답들의 블록을 모두 쓴다
    assert [citation.url for citation in response.citations] == ["https://docs.test/a"]
    assert (response.usage.prompt_tokens, response.usage.completion_tokens) == (24, 14)


async def test_still_paused_after_the_continuation_limit_is_a_transport_error_not_a_partial_success():
    paused = _with(stop_reason="pause_turn", content=[{"type": "text", "text": "찾는 중"}])
    params = LLMParams(model_id="claude-test", max_output_tokens=500, web_search=True)

    with pytest.raises(LLMTransportError, match="still paused"):
        await _client(lambda request: httpx2.Response(200, json=paused)).complete(MESSAGES, None, params, timeout=30)


async def test_search_tool_error_inside_a_200_is_a_transport_error():
    failed = {
        "type": "web_search_tool_result",
        "tool_use_id": "srv_1",
        "content": {"type": "web_search_tool_result_error", "error_code": "unavailable"},
    }

    def handler(request: httpx2.Request) -> httpx2.Response:
        return httpx2.Response(200, json=_with(content=[failed, {"type": "text", "text": "못 찾았다"}]))

    params = LLMParams(model_id="claude-test", max_output_tokens=500, web_search=True)
    with pytest.raises(LLMTransportError, match="web search failed"):
        await _client(handler).complete(MESSAGES, None, params, timeout=30)
