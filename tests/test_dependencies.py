"""dependencies 조립 테스트: 벤더 키가 있는 provider 만 LLM 레지스트리에 들어간다."""

from app.clients.llm.google import GoogleClient
from app.clients.llm.xai import XAIClient
from app.core.config import Settings
from app.dependencies import build_llm_registry, build_publisher


def test_build_llm_registry_includes_only_providers_with_keys():
    settings = Settings(_env_file=None, google_api_key="g", xai_api_key="x")

    registry = build_llm_registry(settings)

    assert registry.providers == ["google", "xai"]
    assert isinstance(registry.get("google"), GoogleClient) and isinstance(registry.get("xai"), XAIClient)


def test_build_llm_registry_is_empty_without_keys():
    assert build_llm_registry(Settings(_env_file=None)).providers == []


def test_build_publisher_requires_post_be_url():
    assert build_publisher(Settings(_env_file=None)) is None
    assert build_publisher(Settings(_env_file=None, post_be_url="https://post.test")) is not None
