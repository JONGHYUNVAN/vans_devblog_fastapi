"""GoogleClient 테스트. HTTP 는 httpx2.MockTransport 로 대신한다."""

import json

import httpx2
import pytest

from app.clients.llm.base import LLMMessage, LLMParams
from app.clients.llm.google import GoogleClient
from app.core.exceptions import LLMTransportError

pytestmark = pytest.mark.anyio

MESSAGES = [
    LLMMessage(role="system", content="너는 리뷰어다"),
    LLMMessage(role="user", content="검토하라"),
    LLMMessage(role="assistant", content="{}"),
    LLMMessage(role="user", content="다시"),
]
SCHEMA = {"type": "object", "properties": {"verdict": {"type": "string"}}}
PARAMS = LLMParams(model_id="gemini-test", temperature=0.2, max_output_tokens=800)


def _client(handler) -> GoogleClient:
    return GoogleClient("g-key", base_url="https://gemini.test/v1beta", transport=httpx2.MockTransport(handler))


async def test_complete_builds_generate_content_request_and_reads_candidate_text():
    requests: list[httpx2.Request] = []

    def handler(request: httpx2.Request) -> httpx2.Response:
        requests.append(request)
        return httpx2.Response(
            200,
            json={
                "candidates": [
                    {"content": {"role": "model", "parts": [{"text": '{"verdict": '}, {"text": '"APPROVE"}'}]}}
                ],
                "usageMetadata": {"promptTokenCount": 30, "candidatesTokenCount": 5, "thoughtsTokenCount": 10},
            },
        )

    response = await _client(handler).complete(MESSAGES, SCHEMA, PARAMS, timeout=30)

    assert response.text == '{"verdict": "APPROVE"}'
    assert (response.usage.prompt_tokens, response.usage.completion_tokens) == (30, 15)
    [request] = requests
    assert str(request.url) == "https://gemini.test/v1beta/models/gemini-test:generateContent"
    assert request.headers["x-goog-api-key"] == "g-key"
    body = json.loads(request.content)
    assert body["systemInstruction"] == {"parts": [{"text": "너는 리뷰어다"}]}
    assert [content["role"] for content in body["contents"]] == ["user", "model", "user"]
    assert body["generationConfig"] == {
        "temperature": 0.2,
        "maxOutputTokens": 800,
        "responseMimeType": "application/json",
        "responseJsonSchema": SCHEMA,
    }


@pytest.mark.parametrize("status", [429, 503])
async def test_retryable_statuses_and_network_errors_raise_transport_error(status):
    with pytest.raises(LLMTransportError) as info:
        await _client(lambda request: httpx2.Response(status, text="busy")).complete(MESSAGES, SCHEMA, PARAMS, 30)
    assert info.value.rate_limited is (status == 429)

    def handler(request: httpx2.Request) -> httpx2.Response:
        raise httpx2.ConnectError("down")

    with pytest.raises(LLMTransportError):
        await _client(handler).complete(MESSAGES, SCHEMA, PARAMS, timeout=30)


async def test_bad_request_raises_value_error():
    client = _client(lambda request: httpx2.Response(400, json={"error": {"message": "schema"}}))

    with pytest.raises(ValueError, match="rejected"):
        await client.complete(MESSAGES, SCHEMA, PARAMS, timeout=30)


async def test_empty_candidates_return_empty_text():
    client = _client(lambda request: httpx2.Response(200, json={"candidates": [], "usageMetadata": {}}))

    response = await client.complete(MESSAGES, SCHEMA, PARAMS, timeout=30)

    assert (response.text, response.usage.prompt_tokens) == ("", 0)
