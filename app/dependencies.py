"""라우터가 Depends 로 받는 객체를 조립한다. 연결은 lifespan 이 app.state 에 만들어 둔 것을 쓴다.

CLI 는 open_clients·open_async_database 로 연결을 직접 열고 같은 get_*·build_* 함수로 서비스를 만든다.
"""

import secrets
from collections.abc import AsyncIterator, Callable, Iterator
from contextlib import asynccontextmanager, contextmanager
from functools import partial
from pathlib import Path
from typing import Annotated, Any

from elasticsearch import Elasticsearch
from fastapi import BackgroundTasks, Depends, Header, Request
from pydantic import SecretStr
from pymongo import MongoClient
from pymongo.asynchronous.database import AsyncDatabase
from pymongo.database import Database

from app.clients.es import create_es, is_es_reachable
from app.clients.github import GitHubClient
from app.clients.kis.stream import KISStreamManager
from app.clients.link_checker import LinkChecker
from app.clients.llm.anthropic import AnthropicClient
from app.clients.llm.base import LLMClient
from app.clients.llm.google import GoogleClient
from app.clients.llm.registry import LLMClientRegistry
from app.clients.llm.xai import XAIClient
from app.clients.mongo import create_async_mongo_client, create_mongo_client, is_mongo_reachable
from app.clients.openapi import OpenApiClient
from app.clients.publisher.base import PublisherAccount
from app.clients.publisher.post_be import PostBEPublisher
from app.core.config import Settings, get_settings
from app.core.exceptions import AuthenticationError
from app.repositories.agent_call import AgentCallRepository
from app.repositories.agent_round import PublishingRoundRepository
from app.repositories.code_graph import CodeGraphRepository
from app.repositories.code_graph_repo_config import CodeGraphRepoConfigRepository
from app.repositories.expository_run import ExpositoryRunRepository
from app.repositories.glossary import GlossaryRepository
from app.repositories.model_route import ModelRouteRepository
from app.repositories.pipeline_version import PipelineVersionRepository
from app.repositories.popular_search import PopularSearchRepository
from app.repositories.post import PostRepository
from app.repositories.post_draft import PostDraftRepository
from app.repositories.post_index import PostIndexRepository
from app.repositories.post_provenance import PostProvenanceRepository
from app.repositories.post_ref import PostRefRepository
from app.repositories.topic_registry import TopicRegistryRepository
from app.services.agent_calls import AgentCallService
from app.services.code_graph import AnalysisTracker, CodeGraphRepoConfigService, CodeGraphService, RunInBackground
from app.services.derived_records import DerivedRecordsWriter
from app.services.draft_decision import DraftDecisionService
from app.services.draft_pipeline import DraftPipelineService, DraftPublisher
from app.services.expository import ExpositoryPipeline
from app.services.glossary import GlossaryService
from app.services.glossary_views import GlossaryViewService
from app.services.health import HealthService
from app.services.index_setup import IndexSetupService
from app.services.indexing import IndexingService
from app.services.kis import KISService
from app.services.pipeline_versions import PipelineVersionService
from app.services.pipeline_views import PipelineViewService
from app.services.provenance import ProvenanceService
from app.services.publishing import PublishingService, UnconfiguredDraftPublisher
from app.services.round import RoundService
from app.services.runner import PipelineRunner
from app.services.search import SearchService
from app.services.service_graph import ServiceGraphService
from app.services.sync import SyncService
from app.services.topic_selection import TopicSelectionService


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
    """요청 앱 상태의 ES 클라이언트(lifespan 이 만든다)."""
    return request.app.state.es


def get_mongo_client(request: Request) -> MongoClient:
    """요청 앱 상태의 동기 Mongo 클라이언트(검색·동기화·코드 그래프)."""
    return request.app.state.mongo_client


def get_kis_stream(request: Request) -> KISStreamManager:
    """요청 앱 상태의 KIS WebSocket 매니저."""
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


@asynccontextmanager
async def open_async_database(settings: Settings) -> AsyncIterator[AsyncDatabase]:
    """에이전트 파이프라인용 비동기 Mongo 연결을 열고 블록이 끝나면 닫는다. CLI 가 쓴다 (설계 07 문서 D-43)."""
    async_mongo_client = create_async_mongo_client(settings)
    try:
        yield async_mongo_client[settings.mongodb_db_name]
    finally:
        await async_mongo_client.close()


def get_round_service(
    database: Annotated[AsyncDatabase, Depends(get_async_database)],
    settings: Annotated[Settings, Depends(get_settings)],
) -> RoundService:
    """라우터용 RoundService. 조립은 build_round_service 와 같다."""
    return build_round_service(database, settings)


def get_draft_decision_service(
    database: Annotated[AsyncDatabase, Depends(get_async_database)],
    settings: Annotated[Settings, Depends(get_settings)],
) -> DraftDecisionService:
    """라우터용 DraftDecisionService(사람 결정)."""
    return build_draft_decision_service(database, settings)


def get_pipeline_view_service(
    database: Annotated[AsyncDatabase, Depends(get_async_database)],
    settings: Annotated[Settings, Depends(get_settings)],
) -> PipelineViewService:
    """라우터용 PipelineViewService(읽기 전용)."""
    return build_pipeline_view_service(database, settings)


def get_pipeline_version_service(
    database: Annotated[AsyncDatabase, Depends(get_async_database)],
) -> PipelineVersionService:
    """라우터용 PipelineVersionService. 내부 API 로 버전을 넣는다(09 문서 D-89). created_by 는 api 다."""
    return PipelineVersionService(PipelineVersionRepository(database), created_by="api")


def get_health_service(
    es: Annotated[Elasticsearch, Depends(get_es)],
    mongo_client: Annotated[MongoClient, Depends(get_mongo_client)],
) -> HealthService:
    """헬스체크 서비스. 연결 확인 함수를 부분 적용해 넘긴다."""
    return HealthService(
        is_es_reachable=partial(is_es_reachable, es),
        is_mongo_reachable=partial(is_mongo_reachable, mongo_client),
    )


def get_search_service(
    es: Annotated[Elasticsearch, Depends(get_es)],
    background_tasks: BackgroundTasks,
) -> SearchService:
    """검색 서비스. 인기 검색어 기록은 응답 뒤 백그라운드 태스크로 돈다."""
    return SearchService(
        post_index_repository=PostIndexRepository(es),
        popular_search_repository=PopularSearchRepository(es),
        run_in_background=background_tasks.add_task,
    )


def get_indexing_service(es: Annotated[Elasticsearch, Depends(get_es)]) -> IndexingService:
    """색인 서비스(내부 API 가 쓴다)."""
    return IndexingService(post_index_repository=PostIndexRepository(es))


def get_sync_service(
    es: Annotated[Elasticsearch, Depends(get_es)],
    mongo_client: Annotated[MongoClient, Depends(get_mongo_client)],
    settings: Annotated[Settings, Depends(get_settings)],
) -> SyncService:
    """동기화 서비스. 라우터와 CLI sync-posts 가 같이 쓴다."""
    return SyncService(
        post_repository=PostRepository(mongo_client[settings.mongodb_db_name]),
        post_index_repository=PostIndexRepository(es),
        is_es_reachable=partial(is_es_reachable, es),
        is_mongo_reachable=partial(is_mongo_reachable, mongo_client),
    )


def get_index_setup_service(es: Elasticsearch) -> IndexSetupService:
    """인기 검색어 인덱스 설정 서비스. lifespan 과 CLI setup-popular 가 쓴다."""
    return IndexSetupService(popular_search_repository=PopularSearchRepository(es))


def get_code_graph_service(
    request: Request,
    mongo_client: Annotated[MongoClient, Depends(get_mongo_client)],
    settings: Annotated[Settings, Depends(get_settings)],
    background_tasks: BackgroundTasks,
) -> CodeGraphService:
    """라우터용 CodeGraphService. 분석은 백그라운드 태스크로 돌리고 진행 상태는 앱 상태의 tracker 에 남는다."""
    return build_code_graph_service(
        mongo_client,
        settings,
        run_in_background=background_tasks.add_task,
        tracker=request.app.state.code_graph_tracker,
    )


def build_code_graph_service(
    mongo_client: MongoClient,
    settings: Settings,
    run_in_background: RunInBackground,
    tracker: AnalysisTracker | None = None,
) -> CodeGraphService:
    """라우터와 CLI `code-graph` 가 같은 조립을 쓴다. CLI 는 분석을 바로 돌리는 run_in_background 를 넘긴다."""
    return CodeGraphService(
        repository=CodeGraphRepository(mongo_client[settings.mongodb_db_name]),
        github_client=GitHubClient(
            timeout_s=settings.code_graph_download_timeout_s,
            max_bytes=settings.code_graph_max_tarball_mb * 1024 * 1024,
        ),
        repos=build_code_graph_repo_config_service(mongo_client, settings).load(),
        run_in_background=run_in_background,
        tracker=tracker,
    )


def build_code_graph_repo_config_service(mongo_client: MongoClient, settings: Settings) -> CodeGraphRepoConfigService:
    """저장소 설정은 요청마다 읽어 재시작 없이 반영한다 (G-35). CLI `code-graph seed-repos` 도 이것을 쓴다."""
    return CodeGraphRepoConfigService(CodeGraphRepoConfigRepository(mongo_client[settings.mongodb_db_name]))


def get_service_graph_service(
    request: Request,
    mongo_client: Annotated[MongoClient, Depends(get_mongo_client)],
    settings: Annotated[Settings, Depends(get_settings)],
) -> ServiceGraphService:
    """라우터용 ServiceGraphService. 이 앱의 OpenAPI 는 HTTP 없이 app.openapi 로 받는다(G-13)."""
    return build_service_graph_service(mongo_client, settings, self_openapi=request.app.openapi)


def build_service_graph_service(
    mongo_client: MongoClient, settings: Settings, self_openapi: Callable[[], dict[str, Any]]
) -> ServiceGraphService:
    """라우터와 CLI `code-graph services/flow` 가 같은 조립을 쓴다. self_openapi 는 이 앱의 app.openapi 다(HTTP 없이, G-13)."""
    return ServiceGraphService(
        repository=CodeGraphRepository(mongo_client[settings.mongodb_db_name]),
        openapi_client=OpenApiClient(timeout_s=settings.code_graph_openapi_timeout_s),
        repos=build_code_graph_repo_config_service(mongo_client, settings).load(),
        self_openapi=self_openapi,
        setting_value=partial(_setting_value, settings),
    )


def _setting_value(settings: Settings, name: str) -> str:
    # OpenApiSource 의 base_setting·password_setting 이 가리키는 Settings 필드. 없는 이름은 빈 값(기능만 꺼진다)
    value = getattr(settings, name, None)
    if isinstance(value, SecretStr):
        return value.get_secret_value()
    return value if isinstance(value, str) else ""


def ensure_code_graph_indexes(database: Database) -> None:
    """code_graphs 컬렉션 인덱스를 만든다. 있으면 그대로 둔다. lifespan 과 CLI `code-graph analyze` 가 부른다."""
    CodeGraphRepository(database).ensure_indexes()


async def ensure_agent_indexes(database: AsyncDatabase) -> None:
    """에이전트 파이프라인 컬렉션의 인덱스를 만든다(용어 사전·제작 기록 포함). 있으면 그대로 둔다. lifespan 이 부른다."""
    await PublishingRoundRepository(database).ensure_indexes()
    await PostDraftRepository(database).ensure_indexes()
    await AgentCallRepository(database).ensure_indexes()
    await TopicRegistryRepository(database).ensure_indexes()
    await GlossaryRepository(database).ensure_indexes()
    await PostProvenanceRepository(database).ensure_indexes()
    await PipelineVersionRepository(database).ensure_indexes()


def build_pipeline_version_service(database: AsyncDatabase) -> PipelineVersionService:
    """파이프라인 버전(프롬프트·라우트·페르소나·조절 값) 읽기·생성 (설계 09 문서). 러너·결정·조회·CLI `agents pipeline` 이 같이 쓴다."""
    return PipelineVersionService(PipelineVersionRepository(database))


def available_providers(settings: Settings) -> list[str]:
    """API 키가 있는 벤더. 라운드 스냅샷이 키 없는 후보를 뺀다."""
    keys = {"anthropic": settings.anthropic_api_key, "google": settings.google_api_key, "xai": settings.xai_api_key}
    return [provider for provider, key in keys.items() if key]


def build_llm_registry(settings: Settings) -> LLMClientRegistry:
    """키가 있는 벤더만 넣는다. 라운드 생성 뒤 키가 사라지면 호출이 KeyError 라 러너가 INTERNAL_ERROR 로 멈춘다."""
    clients: dict[str, LLMClient] = {}
    if settings.anthropic_api_key:
        clients["anthropic"] = AnthropicClient(settings.anthropic_api_key.get_secret_value())
    if settings.google_api_key:
        clients["google"] = GoogleClient(settings.google_api_key.get_secret_value())
    if settings.xai_api_key:
        clients["xai"] = XAIClient(settings.xai_api_key.get_secret_value())
    return LLMClientRegistry(clients, concurrency_per_provider=settings.llm_concurrency_per_provider)


def build_publisher_accounts(settings: Settings) -> dict[str, PublisherAccount]:
    """이메일과 비밀번호가 다 있는 벤더만 넣는다."""
    candidates = {
        "anthropic": (settings.anthropic_publisher_email, settings.anthropic_publisher_password),
        "google": (settings.google_publisher_email, settings.google_publisher_password),
        "xai": (settings.xai_publisher_email, settings.xai_publisher_password),
    }
    return {
        provider: PublisherAccount(email=email, password=password)
        for provider, (email, password) in candidates.items()
        if email and password
    }


def build_publisher(settings: Settings) -> PostBEPublisher | None:
    """POST_BE_URL 이 없으면 None. 라운드 생성이 카테고리를 읽을 수 없어 ValueError 로 거절한다.

    글을 쓴 벤더의 계정(또는 USER_SERVICE_URL)이 없으면 PublishingService 가 ValueError 를 던져 러너가 INTERNAL_ERROR 로 멈춘다.
    """
    if not settings.post_be_url:
        return None
    return PostBEPublisher(
        settings.post_be_url,
        settings.publish_timeout_s,
        user_service_url=settings.user_service_url,
        accounts=build_publisher_accounts(settings),
    )


def build_expository_pipeline(database: AsyncDatabase, settings: Settings, run_dir: Path) -> ExpositoryPipeline:
    """설명문 파이프라인 러너. 프롬프트·단계별 모델은 최신 파이프라인 버전에서 읽고 호출은 영수증에 남긴다 (09 문서 D-99).

    단계 결과는 run_dir 에 쓴다. Post BE 는 1단계 카테고리 목록에만 쓴다.
    """
    return ExpositoryPipeline(
        calls=build_agent_call_service(database, settings),
        versions=build_pipeline_version_service(database),
        providers=available_providers(settings),
        repository=ExpositoryRunRepository(run_dir),
        publisher=build_publisher(settings),
        main_categories=settings.agent_main_category_values,
    )


def build_derived_records_writer(database: AsyncDatabase, settings: Settings) -> DerivedRecordsWriter:
    """발행된 글의 post_glossary·post_provenance 따라 쓰기와 용어 자동 승인 (설계 08 문서). 발행·결정·정리·CLI 가 같이 쓴다."""
    glossary_repository = GlossaryRepository(database)
    return DerivedRecordsWriter(
        draft_repository=PostDraftRepository(database),
        round_repository=PublishingRoundRepository(database),
        agent_call_repository=AgentCallRepository(database),
        glossary_repository=glossary_repository,
        provenance_repository=PostProvenanceRepository(database),
        # 용어 서비스는 자동 승인(approve_all)만 쓴다. LLM 이 필요 없다
        glossary_service=GlossaryService(repository=glossary_repository, settings=settings),
    )


def build_glossary_service(database: AsyncDatabase, settings: Settings) -> GlossaryService:
    """러너(추출 단계)와 CLI `agents glossary` 가 같이 쓴다. LLM 레지스트리는 키 있는 벤더만 든다."""
    return GlossaryService(
        repository=GlossaryRepository(database),
        settings=settings,
        agent_call_service=build_agent_call_service(database, settings),
        draft_repository=PostDraftRepository(database),
        round_repository=PublishingRoundRepository(database),
        version_service=build_pipeline_version_service(database),
    )


def build_glossary_view_service(database: AsyncDatabase, settings: Settings) -> GlossaryViewService:
    """용어 사전 공개 읽기 서비스(08 문서 2.6절). 라우터와 CLI 가 같이 쓴다."""
    return GlossaryViewService(repository=GlossaryRepository(database), post_ref_repository=PostRefRepository(database))


def build_provenance_service(database: AsyncDatabase, settings: Settings) -> ProvenanceService:
    """제작 기록 공개 조회·단건 보충 서비스(08 문서 3절)."""
    return ProvenanceService(
        repository=PostProvenanceRepository(database),
        draft_repository=PostDraftRepository(database),
        round_repository=PublishingRoundRepository(database),
        agent_call_repository=AgentCallRepository(database),
    )


def get_glossary_view_service(
    database: Annotated[AsyncDatabase, Depends(get_async_database)],
    settings: Annotated[Settings, Depends(get_settings)],
) -> GlossaryViewService:
    """라우터용 GlossaryViewService."""
    return build_glossary_view_service(database, settings)


def get_provenance_service(
    database: Annotated[AsyncDatabase, Depends(get_async_database)],
    settings: Annotated[Settings, Depends(get_settings)],
) -> ProvenanceService:
    """라우터용 ProvenanceService."""
    return build_provenance_service(database, settings)


def build_draft_decision_service(database: AsyncDatabase, settings: Settings) -> DraftDecisionService:
    """라우터와 CLI 결정 명령이 같은 조립을 쓴다."""
    return DraftDecisionService(
        draft_repository=PostDraftRepository(database),
        agent_call_repository=AgentCallRepository(database),
        topic_registry_repository=TopicRegistryRepository(database),
        round_service=build_round_service(database, settings),
        settings=settings,
        version_service=build_pipeline_version_service(database),
        derived_writer=build_derived_records_writer(database, settings),
    )


def build_round_service(database: AsyncDatabase, settings: Settings) -> RoundService:
    """라우터·CLI·러너가 같은 조립을 쓴다. 라운드 생성은 파이프라인 버전 서비스에서 최신 버전을 읽어 고정한다(09 문서 D-73).

    publisher 가 None(POST_BE_URL 없음)이면 start_round 가 ValueError 로 거절한다.
    """
    return RoundService(
        PublishingRoundRepository(database),
        PostDraftRepository(database),
        TopicRegistryRepository(database),
        publisher=build_publisher(settings),
        version_service=build_pipeline_version_service(database),
        route_repository=ModelRouteRepository(database),
        providers=available_providers(settings),
        main_categories=settings.agent_main_category_values,
        derived_writer=build_derived_records_writer(database, settings),
    )


def build_pipeline_view_service(database: AsyncDatabase, settings: Settings) -> PipelineViewService:
    """읽기 전용 조회. 라우터와 CLI(status·show·metrics·--wait)가 같이 쓴다."""
    return PipelineViewService(
        round_service=build_round_service(database, settings),
        draft_repository=PostDraftRepository(database),
        agent_call_repository=AgentCallRepository(database),
        version_service=build_pipeline_version_service(database),
    )


def build_agent_call_service(database: AsyncDatabase, settings: Settings) -> AgentCallService:
    """러너 조립과 CLI `agents replay` 가 같이 쓴다."""
    return AgentCallService(AgentCallRepository(database), build_llm_registry(settings), settings)


def build_pipeline_runner(database: AsyncDatabase, settings: Settings) -> PipelineRunner:
    """lifespan 과 CLI `agents run` 이 같은 조립으로 러너를 만든다. 러너는 프로세스에 하나만 둔다(설계 07 문서 Q-04)."""
    round_repository = PublishingRoundRepository(database)
    draft_repository = PostDraftRepository(database)
    topic_registry_repository = TopicRegistryRepository(database)
    publisher = build_publisher(settings)
    version_service = build_pipeline_version_service(database)  # 러너 안의 서비스가 같은 캐시를 쓴다
    round_service = build_round_service(database, settings)
    agent_call_service = build_agent_call_service(database, settings)
    draft_publisher: DraftPublisher = (
        PublishingService(
            draft_repository=draft_repository,
            topic_registry_repository=topic_registry_repository,
            publisher=publisher,
            settings=settings,
            version_service=version_service,
            derived_writer=build_derived_records_writer(database, settings),
        )
        if publisher is not None
        else UnconfiguredDraftPublisher()
    )
    glossary_service = GlossaryService(
        repository=GlossaryRepository(database),
        settings=settings,
        agent_call_service=agent_call_service,
        draft_repository=draft_repository,
        round_repository=round_repository,
        version_service=version_service,
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
            version_service=version_service,
        ),
        draft_pipeline_service=DraftPipelineService(
            draft_repository=draft_repository,
            round_service=round_service,
            agent_call_service=agent_call_service,
            publisher=draft_publisher,
            version_service=version_service,
            link_checker=LinkChecker(),
            glossary_service=glossary_service,
        ),
        version_service=version_service,
        settings=settings,
    )


def get_kis_service(
    kis_stream: Annotated[KISStreamManager, Depends(get_kis_stream)],
    settings: Annotated[Settings, Depends(get_settings)],
) -> KISService:
    """KIS 실시간 시세 서비스. 키가 없으면 degraded 응답이다."""
    return KISService(kis_stream=kis_stream, is_configured=settings.kis_configured)
