"""dependencies 조립 테스트: 벤더 키가 있는 provider 만 LLM 레지스트리에 들어가고, 발행 계정·PublishingService 가 조립된다."""

from app.clients.llm.google import GoogleClient
from app.clients.llm.xai import XAIClient
from app.core.config import Settings
from app.dependencies import build_draft_decision_service, build_llm_registry, build_pipeline_runner, build_publisher
from app.services.draft_decision import DraftDecisionService
from app.services.publishing import PublishingService
from tests.fakes import FakeAsyncDatabase


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


def test_build_publisher_carries_the_publishing_account():
    settings = Settings(
        _env_file=None,
        post_be_url="https://post.test",
        user_service_url="https://user.test",
        publisher_email="bot@example.com",
        publisher_password="Password1!",
    )

    publisher = build_publisher(settings)

    assert publisher is not None
    assert (publisher._user_service_url, publisher._email, publisher._password) == (  # noqa: SLF001
        "https://user.test",
        "bot@example.com",
        "Password1!",
    )


def test_build_pipeline_runner_publishes_through_publishing_service_only_when_fully_configured():
    database = FakeAsyncDatabase()
    account = {"user_service_url": "https://user.test", "publisher_email": "bot@x.io", "publisher_password": "pw"}

    configured = build_pipeline_runner(database, Settings(_env_file=None, post_be_url="https://post.test", **account))
    url_only = build_pipeline_runner(database, Settings(_env_file=None, post_be_url="https://post.test"))
    without = build_pipeline_runner(database, Settings(_env_file=None))

    assert isinstance(configured._draft_pipeline._publisher, PublishingService)  # noqa: SLF001
    # 계정이 비면 발행 의도를 저장하기 전에 멈춘다(재개 때 PUBLISH_UNCERTAIN 으로 격리되지 않게)
    assert not isinstance(url_only._draft_pipeline._publisher, PublishingService)  # noqa: SLF001
    assert not isinstance(without._draft_pipeline._publisher, PublishingService)  # noqa: SLF001
    assert isinstance(build_draft_decision_service(database, Settings(_env_file=None)), DraftDecisionService)
