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


async def test_unset_temperature_and_effort_are_not_sent_and_effort_maps_to_thinking_level():
    requests: list[httpx2.Request] = []

    def handler(request: httpx2.Request) -> httpx2.Response:
        requests.append(request)
        return httpx2.Response(
            200, json={"candidates": [{"content": {"parts": [{"text": "{}"}]}, "finishReason": "STOP"}]}
        )

    client = _client(handler)
    await client.complete(MESSAGES, SCHEMA, LLMParams(model_id="gemini-test", max_output_tokens=800), timeout=30)
    await client.complete(MESSAGES, SCHEMA, LLMParams(model_id="gemini-test", effort="high", max_output_tokens=800), 30)

    plain, with_effort = (json.loads(request.content)["generationConfig"] for request in requests)
    assert "temperature" not in plain and "thinkingConfig" not in plain
    assert with_effort["thinkingConfig"] == {"thinkingLevel": "HIGH"}


async def test_max_tokens_finish_reason_is_truncated_even_with_an_empty_body():
    payload = {
        "candidates": [{"content": {"parts": []}, "finishReason": "MAX_TOKENS"}],
        "usageMetadata": {"thoughtsTokenCount": 800},
    }

    response = await _client(lambda request: httpx2.Response(200, json=payload)).complete(MESSAGES, SCHEMA, PARAMS, 30)

    assert (response.text, response.truncated, response.stop_reason) == ("", True, "MAX_TOKENS")
    assert response.usage.completion_tokens == 800


async def test_web_search_sends_the_tool_without_a_schema_and_reads_grounding_chunks():
    requests: list[httpx2.Request] = []

    def handler(request: httpx2.Request) -> httpx2.Response:
        requests.append(request)
        chunks = [
            {"web": {"uri": "https://grounding.test/redirect/1", "title": "docs.example.com"}},
            {"retrievedContext": {}},
        ]
        return httpx2.Response(
            200,
            json={
                "candidates": [
                    {
                        "content": {"parts": [{"text": "확인했다"}]},
                        "finishReason": "STOP",
                        "groundingMetadata": {"groundingChunks": chunks},
                    }
                ]
            },
        )

    params = LLMParams(model_id="gemini-test", max_output_tokens=800, web_search=True)
    response = await _client(handler).complete(MESSAGES, None, params, timeout=30)

    body = json.loads(requests[0].content)
    assert body["tools"] == [{"google_search": {}}]
    assert "responseJsonSchema" not in body["generationConfig"]
    assert [(citation.url, citation.title) for citation in response.citations] == [
        ("https://grounding.test/redirect/1", "docs.example.com")
    ]
