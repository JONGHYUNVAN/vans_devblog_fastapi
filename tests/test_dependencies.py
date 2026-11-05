"""dependencies 조립 테스트: 벤더 키가 있는 provider 만 LLM 레지스트리에 들어가고, 발행 계정·PublishingService 가 조립된다."""

from app.clients.llm.google import GoogleClient
from app.clients.llm.xai import XAIClient
from app.core.config import Settings
from app.dependencies import (
    build_agent_call_service,
    build_draft_decision_service,
    build_llm_registry,
    build_pipeline_runner,
    build_pipeline_view_service,
    build_publisher,
    build_round_service,
)
from app.services.agent_calls import AgentCallService
from app.services.draft_decision import DraftDecisionService
from app.services.pipeline_views import PipelineViewService
from app.services.publishing import PublishingService
from tests.fakes import FakeAsyncDatabase


def test_build_round_service_gets_main_categories_from_settings():
    settings = Settings(_env_file=None, agent_main_categories="nestjs, jwt,")

    assert build_round_service(FakeAsyncDatabase(), settings)._main_categories == {"nestjs", "jwt"}


def test_default_main_categories_leave_out_mains_without_a_blog_page():
    values = Settings(_env_file=None).agent_main_category_values

    assert len(values) == 9
    assert values.isdisjoint({"jest", "cypress", "junit5", "git"})


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


def test_build_publisher_carries_only_complete_provider_accounts():
    settings = Settings(
        _env_file=None,
        post_be_url="https://post.test",
        user_service_url="https://user.test",
        anthropic_publisher_email="claude@example.com",
        anthropic_publisher_password="Password1!",
        google_publisher_email="gemini@example.com",  # 비밀번호 없음
    )

    publisher = build_publisher(settings)

    assert publisher is not None
    assert publisher.has_account("anthropic")
    assert not publisher.has_account("google")
    assert not publisher.has_account("xai")


def test_build_pipeline_runner_publishes_through_publishing_service_only_with_post_be_url():
    database = FakeAsyncDatabase()

    configured = build_pipeline_runner(database, Settings(_env_file=None, post_be_url="https://post.test"))
    without = build_pipeline_runner(database, Settings(_env_file=None))

    # 벤더별 계정 검사는 PublishingService 가 글마다 한다
    assert isinstance(configured._draft_pipeline._publisher, PublishingService)  # noqa: SLF001
    assert not isinstance(without._draft_pipeline._publisher, PublishingService)  # noqa: SLF001
    assert isinstance(build_draft_decision_service(database, Settings(_env_file=None)), DraftDecisionService)
    assert isinstance(build_pipeline_view_service(database, Settings(_env_file=None)), PipelineViewService)
    assert isinstance(build_agent_call_service(database, Settings(_env_file=None)), AgentCallService)
