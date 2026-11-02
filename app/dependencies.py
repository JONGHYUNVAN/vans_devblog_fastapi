"""라우터가 Depends 로 받는 객체를 조립한다. 연결은 lifespan 이 app.state 에 만들어 둔 것을 쓴다.

CLI 는 open_clients 로 연결을 직접 열고 같은 get_*_service 함수로 서비스를 만든다.
"""

import secrets
from collections.abc import Iterator
from contextlib import contextmanager
from functools import partial
from typing import Annotated

from elasticsearch import Elasticsearch
from fastapi import BackgroundTasks, Depends, Header, Request
from pymongo import MongoClient
from pymongo.asynchronous.database import AsyncDatabase

from app.clients.es import create_es, is_es_reachable
from app.clients.kis.stream import KISStreamManager
from app.clients.llm.anthropic import AnthropicClient
from app.clients.llm.base import LLMClient
from app.clients.llm.google import GoogleClient
from app.clients.llm.registry import LLMClientRegistry
from app.clients.llm.xai import XAIClient
from app.clients.mongo import create_mongo_client, is_mongo_reachable
from app.clients.publisher.post_be import PostBEPublisher
from app.core.config import Settings, get_settings
from app.core.exceptions import AuthenticationError
from app.models.post_draft import PostDraft
from app.models.publishing_round import PublishingRound
from app.repositories.agent_call import AgentCallRepository
from app.repositories.agent_round import PublishingRoundRepository
from app.repositories.popular_search import PopularSearchRepository
from app.repositories.post import PostRepository
from app.repositories.post_draft import PostDraftRepository
from app.repositories.post_index import PostIndexRepository
from app.repositories.topic_registry import TopicRegistryRepository
from app.services.agent_calls import AgentCallService
from app.services.draft_decision import DraftDecisionService
from app.services.draft_pipeline import DraftPipelineService, DraftPublisher
from app.services.health import HealthService
from app.services.index_setup import IndexSetupService
from app.services.indexing import IndexingService
from app.services.kis import KISService
from app.services.publishing import PublishingService
from app.services.round import RoundService
from app.services.runner import PipelineRunner
from app.services.search import SearchService
from app.services.sync import SyncService
from app.services.topic_selection import TopicSelectionService
from app.services.validation import ValidationGate


@contextmanager
def open_clients(settings: Settings) -> Iterator[tuple[Elasticsearch, MongoClient]]:
    """ES·MongoDB 클라이언트를 만들고 블록이 끝나면 닫는다. 앱 lifespan 과 CLI 가 쓴다."""
    es = create_es(settings)
    mongo_client = create_mongo_client(settings)
    try:
        yield es, mongo_client
    finally:
        es.close()
        mongo_client.close()


def get_es(request: Request) -> Elasticsearch:
    return request.app.state.es


def get_mongo_client(request: Request) -> MongoClient:
    return request.app.state.mongo_client


def get_kis_stream(request: Request) -> KISStreamManager:
    return request.app.state.kis_stream


def verify_internal_key(
    settings: Annotated[Settings, Depends(get_settings)],
    x_internal_key: Annotated[str, Header()] = "",
) -> None:
    """X-Internal-Key 가 INTERNAL_API_KEY 와 같은지 확인한다. 서버에 키가 설정되지 않았으면 모두 거절한다."""
    _verify_key(x_internal_key, settings.internal_api_key)


def verify_agents_key(
    settings: Annotated[Settings, Depends(get_settings)],
    x_internal_key: Annotated[str, Header()] = "",
) -> None:
    """에이전트 내부 API. X-Internal-Key 가 AGENTS_INTERNAL_API_KEY 와 같은지 확인한다 (설계 07 문서 D-44)."""
    _verify_key(x_internal_key, settings.agents_internal_api_key)


def _verify_key(provided: str, expected: str) -> None:
    if not expected or not secrets.compare_digest(provided.encode(), expected.encode()):
        raise AuthenticationError("Invalid or missing X-Internal-Key header")


def get_async_database(request: Request, settings: Annotated[Settings, Depends(get_settings)]) -> AsyncDatabase:
    """에이전트 파이프라인 컬렉션이 있는 DB. lifespan 이 만든 비동기 클라이언트를 쓴다 (설계 07 문서 D-43)."""
    return request.app.state.async_mongo_client[settings.mongodb_db_name]


def get_draft_repository(database: Annotated[AsyncDatabase, Depends(get_async_database)]) -> PostDraftRepository:
    return PostDraftRepository(database)


def get_agent_call_repository(
    database: Annotated[AsyncDatabase, Depends(get_async_database)],
) -> AgentCallRepository:
    return AgentCallRepository(database)


def get_round_service(
    database: Annotated[AsyncDatabase, Depends(get_async_database)],
    settings: Annotated[Settings, Depends(get_settings)],
) -> RoundService:
    return build_round_service(database, settings)


def get_draft_decision_service(
    database: Annotated[AsyncDatabase, Depends(get_async_database)],
    settings: Annotated[Settings, Depends(get_settings)],
) -> DraftDecisionService:
    return build_draft_decision_service(database, settings)


def get_health_service(
    es: Annotated[Elasticsearch, Depends(get_es)],
    mongo_client: Annotated[MongoClient, Depends(get_mongo_client)],
) -> HealthService:
    return HealthService(
        is_es_reachable=partial(is_es_reachable, es),
        is_mongo_reachable=partial(is_mongo_reachable, mongo_client),
    )


def get_search_service(
    es: Annotated[Elasticsearch, Depends(get_es)],
    background_tasks: BackgroundTasks,
) -> SearchService:
    return SearchService(
        post_index_repository=PostIndexRepository(es),
        popular_search_repository=PopularSearchRepository(es),
        run_in_background=background_tasks.add_task,
    )


def get_indexing_service(es: Annotated[Elasticsearch, Depends(get_es)]) -> IndexingService:
    return IndexingService(post_index_repository=PostIndexRepository(es))


def get_sync_service(
    es: Annotated[Elasticsearch, Depends(get_es)],
    mongo_client: Annotated[MongoClient, Depends(get_mongo_client)],
    settings: Annotated[Settings, Depends(get_settings)],
) -> SyncService:
    return SyncService(
        post_repository=PostRepository(mongo_client[settings.mongodb_db_name]),
        post_index_repository=PostIndexRepository(es),
        is_es_reachable=partial(is_es_reachable, es),
        is_mongo_reachable=partial(is_mongo_reachable, mongo_client),
    )


def get_index_setup_service(es: Elasticsearch) -> IndexSetupService:
    return IndexSetupService(popular_search_repository=PopularSearchRepository(es))


async def ensure_agent_indexes(database: AsyncDatabase) -> None:
    """에이전트 파이프라인 컬렉션 4개의 인덱스를 만든다. 있으면 그대로 둔다. lifespan 이 부른다."""
    await PublishingRoundRepository(database).ensure_indexes()
    await PostDraftRepository(database).ensure_indexes()
    await AgentCallRepository(database).ensure_indexes()
    await TopicRegistryRepository(database).ensure_indexes()


def build_llm_registry(settings: Settings) -> LLMClientRegistry:
    """키가 있는 벤더만 넣는다. 바인딩이 키 없는 provider 를 가리키면 호출이 KeyError 라 러너가 INTERNAL_ERROR 로 멈춘다."""
    clients: dict[str, LLMClient] = {}
    if settings.anthropic_api_key:
        clients["anthropic"] = AnthropicClient(settings.anthropic_api_key.get_secret_value())
    if settings.google_api_key:
        clients["google"] = GoogleClient(settings.google_api_key.get_secret_value())
    if settings.xai_api_key:
        clients["xai"] = XAIClient(settings.xai_api_key.get_secret_value())
    return LLMClientRegistry(clients, concurrency_per_provider=settings.llm_concurrency_per_provider)


def build_publisher(settings: Settings) -> PostBEPublisher | None:
    """POST_BE_URL 이 없으면 None. 라운드 생성이 카테고리를 읽을 수 없어 ValueError 로 거절한다.

    발행 계정(USER_SERVICE_URL, PUBLISHER_EMAIL, PUBLISHER_PASSWORD)이 비어 있으면 발행 호출이 ValueError 라 러너가 INTERNAL_ERROR 로 멈춘다.
    """
    if not settings.post_be_url:
        return None
    return PostBEPublisher(
        settings.post_be_url,
        settings.publish_timeout_s,
        user_service_url=settings.user_service_url,
        email=settings.publisher_email,
        password=settings.publisher_password.get_secret_value() if settings.publisher_password else "",
    )


def build_draft_decision_service(database: AsyncDatabase, settings: Settings) -> DraftDecisionService:
    """라우터와 CLI 결정 명령이 같은 조립을 쓴다."""
    return DraftDecisionService(
        draft_repository=PostDraftRepository(database),
        agent_call_repository=AgentCallRepository(database),
        topic_registry_repository=TopicRegistryRepository(database),
        round_service=build_round_service(database, settings),
        settings=settings,
    )


def build_round_service(database: AsyncDatabase, settings: Settings) -> RoundService:
    return RoundService(
        PublishingRoundRepository(database),
        PostDraftRepository(database),
        TopicRegistryRepository(database),
        publisher=build_publisher(settings),
    )


class _UnconfiguredDraftPublisher:
    """발행 설정(POST_BE_URL 또는 발행 계정)이 비어 있다. 발행 단계에 닿은 글을 FAILED(INTERNAL_ERROR) 로 멈춘다.

    발행 의도를 저장하기 전에 멈춰야 설정을 고치고 재개했을 때 결과 불명(PUBLISH_UNCERTAIN)으로 격리되지 않는다.
    """

    async def publish(self, draft: PostDraft, publishing_round: PublishingRound) -> None:
        raise ValueError(
            "Post BE publishing is not configured (POST_BE_URL, USER_SERVICE_URL, PUBLISHER_EMAIL, PUBLISHER_PASSWORD)"
        )

    async def drain(self) -> None:
        return None


def build_pipeline_runner(database: AsyncDatabase, settings: Settings) -> PipelineRunner:
    """lifespan 과 CLI `agents run` 이 같은 조립으로 러너를 만든다. 러너는 프로세스에 하나만 둔다(설계 07 문서 Q-04)."""
    round_repository = PublishingRoundRepository(database)
    draft_repository = PostDraftRepository(database)
    topic_registry_repository = TopicRegistryRepository(database)
    publisher = build_publisher(settings)
    round_service = RoundService(round_repository, draft_repository, topic_registry_repository, publisher=publisher)
    agent_call_service = AgentCallService(AgentCallRepository(database), build_llm_registry(settings), settings)
    account_ready = bool(settings.user_service_url and settings.publisher_email and settings.publisher_password)
    draft_publisher: DraftPublisher = (
        PublishingService(
            draft_repository=draft_repository,
            topic_registry_repository=topic_registry_repository,
            publisher=publisher,
            settings=settings,
        )
        if publisher is not None and account_ready
        else _UnconfiguredDraftPublisher()
    )
    return PipelineRunner(
        round_repository=round_repository,
        draft_repository=draft_repository,
        round_service=round_service,
        topic_selection_service=TopicSelectionService(
            round_repository=round_repository,
            draft_repository=draft_repository,
            topic_registry_repository=topic_registry_repository,
            round_service=round_service,
            agent_call_service=agent_call_service,
            settings=settings,
        ),
        draft_pipeline_service=DraftPipelineService(
            draft_repository=draft_repository,
            round_service=round_service,
            agent_call_service=agent_call_service,
            gate=ValidationGate(min_chars=settings.draft_min_chars, max_chars=settings.draft_max_chars),
            publisher=draft_publisher,
            settings=settings,
        ),
        settings=settings,
    )


def get_kis_service(
    kis_stream: Annotated[KISStreamManager, Depends(get_kis_stream)],
    settings: Annotated[Settings, Depends(get_settings)],
) -> KISService:
    return KISService(kis_stream=kis_stream, is_configured=settings.kis_configured)
