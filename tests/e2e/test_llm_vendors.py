"""실제 벤더에 계약 출력 스키마 6종을 보내 구조화 출력이 호환되는지 본다. 기본 실행에서 빠진다 (로드맵 5단계).

사용: ANTHROPIC_API_KEY=... GOOGLE_API_KEY=... XAI_API_KEY=... pytest -m e2e tests/e2e/test_llm_vendors.py
모델은 E2E_ANTHROPIC_MODEL·E2E_GOOGLE_MODEL·E2E_XAI_MODEL 로 바꾼다. 키가 없는 벤더는 건너뛴다.
호출 수는 6 × 벤더 수다. 평평한 스키마가 아니라 실제 계약($defs/$ref, anyOf, default, min/maxLength, min/maxItems)을 보낸다.
"""

import json
import os
from collections.abc import Callable
from functools import partial

import pytest
from pydantic import BaseModel

from app.agents.contracts import (
    ArbitrateOutput,
    DraftOutput,
    ProposeTopicOutput,
    ReviewOutput,
    RevisionOutput,
    SpotCheckOutput,
)
from app.clients.llm.anthropic import AnthropicClient
from app.clients.llm.base import LLMClient, LLMMessage, LLMParams
from app.clients.llm.google import GoogleClient
from app.clients.llm.xai import XAIClient

pytestmark = [pytest.mark.e2e, pytest.mark.anyio]

_TIMEOUT = 180
CONTRACTS: list[type[BaseModel]] = [
    ProposeTopicOutput,
    DraftOutput,
    RevisionOutput,
    ReviewOutput,
    SpotCheckOutput,
    ArbitrateOutput,
]


def _messages(schema: type[BaseModel]) -> list[LLMMessage]:
    # 구조화 출력이 지원하지 않는 제약(길이·개수)은 벤더가 떼어 낼 수 있어 스키마 본문을 프롬프트에도 넣는다
    return [
        LLMMessage(
            role="system",
            content="너는 기술 블로그 파이프라인의 에이전트다. 아래 JSON 스키마에 맞는 예시 객체 하나만 출력한다. "
            "배열 길이·문자열 길이 제약을 지키고, 내용은 한국어로 짧게 쓴다. 설명이나 마크다운 펜스를 붙이지 않는다.",
        ),
        LLMMessage(
            role="user",
            content="주제: MongoDB 낙관적 잠금. 스키마:\n" + json.dumps(schema.model_json_schema(), ensure_ascii=False),
        ),
    ]


def _clients() -> list[tuple[str, Callable[[], LLMClient], str]]:
    """클라이언트는 테스트마다 만든다. 이벤트 루프가 테스트마다 달라 공유하면 anthropic SDK 연결이 'Event loop is closed' 로 실패한다."""
    found = []
    if key := os.environ.get("ANTHROPIC_API_KEY"):
        found.append(
            ("anthropic", partial(AnthropicClient, key), os.environ.get("E2E_ANTHROPIC_MODEL", "claude-opus-5-5"))
        )
    if key := os.environ.get("GOOGLE_API_KEY"):
        found.append(("google", partial(GoogleClient, key), os.environ.get("E2E_GOOGLE_MODEL", "gemini-2.5-pro")))
    if key := os.environ.get("XAI_API_KEY"):
        found.append(("xai", partial(XAIClient, key), os.environ.get("E2E_XAI_MODEL", "grok-4")))
    return found


@pytest.mark.parametrize("schema", CONTRACTS, ids=lambda schema: schema.__name__)
@pytest.mark.parametrize(
    ("provider", "make_client", "model_id"), _clients(), ids=lambda value: value if isinstance(value, str) else ""
)
async def test_vendor_accepts_each_contract_schema_and_returns_matching_json(provider, make_client, model_id, schema):
    params = LLMParams(model_id=model_id, temperature=0.7, max_output_tokens=4000)

    response = await make_client().complete(_messages(schema), schema.model_json_schema(), params, timeout=_TIMEOUT)

    schema.model_validate_json(response.text)
    assert response.usage.prompt_tokens > 0 and response.usage.completion_tokens > 0


def test_at_least_one_vendor_key_is_present():
    if not _clients():
        pytest.skip("벤더 키(ANTHROPIC_API_KEY, GOOGLE_API_KEY, XAI_API_KEY)가 없다")
