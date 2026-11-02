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

    with pytest.raises(LLMTransportError):
        await client.complete(MESSAGES, SCHEMA, PARAMS, timeout=30)


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
