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


def _response(text: str, **fields) -> dict:
    return {
        "status": "completed",
        "output": [
            {"type": "reasoning", "summary": []},
            {
                "type": "message",
                "role": "assistant",
                "content": [{"type": "output_text", "text": text, "annotations": fields.pop("annotations", [])}],
            },
        ],
        "usage": {"input_tokens": 20, "output_tokens": 4},
        **fields,
    }


async def test_complete_builds_responses_request_and_reads_output_text():
    requests: list[httpx2.Request] = []

    def handler(request: httpx2.Request) -> httpx2.Response:
        requests.append(request)
        return httpx2.Response(200, json=_response('{"outcome": "UPHOLD_AUTHOR"}'))

    response = await _client(handler).complete(MESSAGES, SCHEMA, PARAMS, timeout=30)

    assert response.text == '{"outcome": "UPHOLD_AUTHOR"}'
    assert (response.usage.prompt_tokens, response.usage.completion_tokens) == (20, 4)
    assert (response.stop_reason, response.truncated) == ("completed", False)
    [request] = requests
    assert str(request.url) == "https://xai.test/v1/responses"
    assert request.headers["Authorization"] == "Bearer x-key"
    body = json.loads(request.content)
    assert (body["model"], body["temperature"], body["max_output_tokens"]) == ("grok-test", 0.2, 300)
    assert body["instructions"] == "너는 중재자다"
    assert body["input"] == [{"role": "user", "content": "판정하라"}]
    assert body["text"] == {"format": {"type": "json_schema", "name": "output", "schema": SCHEMA}}
    assert body["store"] is False
    assert "reasoning" not in body and "tools" not in body


async def test_effort_and_unset_temperature_are_sent_as_documented():
    requests: list[httpx2.Request] = []

    def handler(request: httpx2.Request) -> httpx2.Response:
        requests.append(request)
        return httpx2.Response(200, json=_response("{}"))

    params = LLMParams(model_id="grok-test", effort="xhigh", max_output_tokens=300)
    await _client(handler).complete(MESSAGES, SCHEMA, params, timeout=30)

    body = json.loads(requests[0].content)
    assert body["reasoning"] == {"effort": "xhigh"}
    assert "temperature" not in body


async def test_web_search_returns_text_and_citations_without_inline_links():
    requests: list[httpx2.Request] = []

    def handler(request: httpx2.Request) -> httpx2.Response:
        requests.append(request)
        annotation = {"type": "url_citation", "url": "https://docs.x.ai/a", "title": "1"}
        return httpx2.Response(
            200, json=_response("확인했다", annotations=[annotation], citations=["https://docs.x.ai/b"])
        )

    params = LLMParams(model_id="grok-test", max_output_tokens=300, web_search=True)
    response = await _client(handler).complete(MESSAGES, None, params, timeout=30)

    body = json.loads(requests[0].content)
    assert body["tools"] == [{"type": "web_search"}]
    assert body["max_turns"] == 3
    assert body["include"] == ["no_inline_citations"]
    assert "text" not in body
    assert [citation.url for citation in response.citations] == ["https://docs.x.ai/a", "https://docs.x.ai/b"]


async def test_incomplete_for_output_limit_is_reported_as_truncated():
    payload = _response('{"out', status="incomplete", incomplete_details={"reason": "max_output_tokens"})

    response = await _client(lambda request: httpx2.Response(200, json=payload)).complete(MESSAGES, SCHEMA, PARAMS, 30)

    assert (response.truncated, response.stop_reason) == (True, "max_output_tokens")


async def test_usage_also_reads_the_documented_example_field_names():
    payload = _response("{}") | {"usage": {"prompt_tokens": 7, "completion_tokens": 2}}

    response = await _client(lambda request: httpx2.Response(200, json=payload)).complete(MESSAGES, SCHEMA, PARAMS, 30)

    assert (response.usage.prompt_tokens, response.usage.completion_tokens) == (7, 2)


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
