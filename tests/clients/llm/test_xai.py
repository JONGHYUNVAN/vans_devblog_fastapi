"""XAIClient 테스트. HTTP 는 httpx2.MockTransport 로 대신한다."""

import json

import httpx2
import pytest

from app.clients.llm.base import LLMMessage, LLMParams
from app.clients.llm.xai import XAIClient
from app.core.exceptions import LLMTransportError

pytestmark = pytest.mark.anyio

MESSAGES = [LLMMessage(role="system", content="너는 중재자다"), LLMMessage(role="user", content="판정하라")]
SCHEMA = {"type": "object", "properties": {"outcome": {"type": "string"}}}
PARAMS = LLMParams(model_id="grok-test", temperature=0.2, max_output_tokens=300)


def _client(handler) -> XAIClient:
    return XAIClient("x-key", base_url="https://xai.test/v1", transport=httpx2.MockTransport(handler))


async def test_complete_builds_chat_completion_request_and_reads_content():
    requests: list[httpx2.Request] = []

    def handler(request: httpx2.Request) -> httpx2.Response:
        requests.append(request)
        return httpx2.Response(
            200,
            json={
                "choices": [{"message": {"role": "assistant", "content": '{"outcome": "UPHOLD_AUTHOR"}'}}],
                "usage": {"prompt_tokens": 20, "completion_tokens": 4},
            },
        )

    response = await _client(handler).complete(MESSAGES, SCHEMA, PARAMS, timeout=30)

    assert response.text == '{"outcome": "UPHOLD_AUTHOR"}'
    assert (response.usage.prompt_tokens, response.usage.completion_tokens) == (20, 4)
    [request] = requests
    assert str(request.url) == "https://xai.test/v1/chat/completions"
    assert request.headers["Authorization"] == "Bearer x-key"
    body = json.loads(request.content)
    assert (body["model"], body["temperature"], body["max_tokens"]) == ("grok-test", 0.2, 300)
    assert body["messages"] == [{"role": m.role, "content": m.content} for m in MESSAGES]
    assert body["response_format"] == {"type": "json_schema", "json_schema": {"name": "output", "schema": SCHEMA}}


async def test_server_error_raises_transport_error_and_client_error_raises_value_error():
    with pytest.raises(LLMTransportError):
        await _client(lambda request: httpx2.Response(502, text="bad gateway")).complete(
            MESSAGES, SCHEMA, PARAMS, timeout=30
        )
    with pytest.raises(ValueError):
        await _client(lambda request: httpx2.Response(401, text="no key")).complete(MESSAGES, SCHEMA, PARAMS, 30)


async def test_non_json_body_raises_value_error():
    client = _client(lambda request: httpx2.Response(200, text="<html>"))

    with pytest.raises(ValueError, match="non-JSON"):
        await client.complete(MESSAGES, SCHEMA, PARAMS, timeout=30)
