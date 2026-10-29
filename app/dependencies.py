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

from app.clients.es import create_es, is_es_reachable
from app.clients.mongo import create_mongo_client, is_mongo_reachable
from app.core.config import Settings, get_settings
from app.core.exceptions import AuthenticationError
from app.repositories.popular_search import PopularSearchRepository
from app.repositories.post import PostRepository
from app.repositories.post_index import PostIndexRepository
from app.services.health import HealthService
from app.services.index_setup import IndexSetupService
from app.services.indexing import IndexingService
from app.services.search import SearchService
from app.services.sync import SyncService


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


def verify_internal_key(
    settings: Annotated[Settings, Depends(get_settings)],
    x_internal_key: Annotated[str, Header()] = "",
) -> None:
    """X-Internal-Key 가 INTERNAL_API_KEY 와 같은지 확인한다. 서버에 키가 설정되지 않았으면 모두 거절한다."""
    expected = settings.internal_api_key
    if not expected or not secrets.compare_digest(x_internal_key.encode(), expected.encode()):
        raise AuthenticationError("Invalid or missing X-Internal-Key header")


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
