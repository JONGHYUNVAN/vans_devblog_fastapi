"""실제 벤더에 1회 호출해 구조화 출력 계약이 호환되는지 본다. 기본 실행에서 빠진다 (로드맵 5단계).

사용: ANTHROPIC_API_KEY=... GOOGLE_API_KEY=... XAI_API_KEY=... pytest -m e2e tests/e2e/test_llm_vendors.py
모델은 E2E_ANTHROPIC_MODEL·E2E_GOOGLE_MODEL·E2E_XAI_MODEL 로 바꾼다. 키가 없는 벤더는 건너뛴다.
"""

import os

import pytest
from pydantic import BaseModel

from app.clients.llm.anthropic import AnthropicClient
from app.clients.llm.base import LLMClient, LLMMessage, LLMParams
from app.clients.llm.google import GoogleClient
from app.clients.llm.xai import XAIClient

pytestmark = [pytest.mark.e2e, pytest.mark.anyio]

_TIMEOUT = 120


class Answer(BaseModel):
    title: str
    keywords: list[str]


MESSAGES = [
    LLMMessage(role="system", content="너는 기술 블로그 작성자다. JSON 객체 하나만 출력한다."),
    LLMMessage(role="user", content="MongoDB 낙관적 잠금에 대한 글 제목 하나와 키워드 세 개를 제안하라."),
]


def _clients() -> list[tuple[str, LLMClient, str]]:
    found = []
    if key := os.environ.get("ANTHROPIC_API_KEY"):
        found.append(("anthropic", AnthropicClient(key), os.environ.get("E2E_ANTHROPIC_MODEL", "claude-opus-5-5")))
    if key := os.environ.get("GOOGLE_API_KEY"):
        found.append(("google", GoogleClient(key), os.environ.get("E2E_GOOGLE_MODEL", "gemini-2.5-pro")))
    if key := os.environ.get("XAI_API_KEY"):
        found.append(("xai", XAIClient(key), os.environ.get("E2E_XAI_MODEL", "grok-4")))
    return found


@pytest.mark.parametrize(
    ("provider", "client", "model_id"), _clients(), ids=lambda value: value if isinstance(value, str) else ""
)
async def test_vendor_returns_schema_compatible_json(provider, client, model_id):
    params = LLMParams(model_id=model_id, temperature=0.7, max_output_tokens=1000)

    response = await client.complete(MESSAGES, Answer.model_json_schema(), params, timeout=_TIMEOUT)

    answer = Answer.model_validate_json(response.text)
    assert answer.title and len(answer.keywords) >= 1
    assert response.usage.prompt_tokens > 0 and response.usage.completion_tokens > 0


def test_at_least_one_vendor_key_is_present():
    if not _clients():
        pytest.skip("벤더 키(ANTHROPIC_API_KEY, GOOGLE_API_KEY, XAI_API_KEY)가 없다")
