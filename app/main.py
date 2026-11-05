"""VansDevBlog 검색·실시간 시세 서비스 (FastAPI)."""

import asyncio
import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from elasticsearch import Elasticsearch
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import RedirectResponse
from pymongo import AsyncMongoClient, MongoClient

from app.clients.kis.stream import create_kis_stream
from app.clients.mongo import create_async_mongo_client
from app.core.config import get_settings
from app.core.exceptions import register_exception_handlers
from app.dependencies import (
    build_pipeline_runner,
    ensure_agent_indexes,
    ensure_code_graph_indexes,
    get_index_setup_service,
    open_clients,
)
from app.routers import agent_rounds, code_graph, health, internal, kis, search, sync
from app.services.code_graph import AnalysisTracker
from app.services.runner import PipelineRunner

logging.basicConfig(
    level=logging.INFO,
    format="%(levelname)s %(asctime)s %(name)s %(message)s",
)
logging.getLogger("elasticsearch").setLevel(logging.WARNING)
logging.getLogger("elastic_transport").setLevel(logging.WARNING)

logger = logging.getLogger(__name__)

settings = get_settings()

_STARTUP_ES_TIMEOUT = 5


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    """ES·MongoDB 클라이언트와 KIS 스트림 매니저를 한 번 만들어 app.state 에 두고, 종료 때 닫는다.

    인기 검색어 인덱스도 준비한다. KIS 연결은 첫 구독 때 맺는다. 에이전트 파이프라인 러너도 여기서 시작·종료한다.
    """
    with open_clients(settings) as (es, mongo_client):
        app.state.es = es
        app.state.mongo_client = mongo_client
        # 에이전트 파이프라인은 같은 DB 를 비동기 클라이언트로 쓴다 (설계 07 문서 D-43)
        app.state.async_mongo_client = create_async_mongo_client(settings)
        app.state.kis_stream = create_kis_stream(settings)
        await _ensure_popular_index(es)
        app.state.code_graph_tracker = AnalysisTracker()
        await _ensure_code_graph_indexes(mongo_client, settings.mongodb_db_name)
        await _ensure_agent_indexes(app.state.async_mongo_client, settings.mongodb_db_name)
        app.state.pipeline_runner = _start_pipeline_runner(app.state.async_mongo_client, settings.mongodb_db_name)
        try:
            yield
        finally:
            if app.state.pipeline_runner is not None:
                await app.state.pipeline_runner.stop()
            await app.state.kis_stream.close()
            await app.state.async_mongo_client.close()


async def _ensure_popular_index(es: Elasticsearch) -> None:
    # Django apps.ready() 와 같다. 인덱스가 없으면 인기 검색어 갱신이 매번 실패한다.
    # ES 가 내려가 있어도 앱은 떠야 하므로 짧게 한 번만 시도하고 경고만 남긴다.
    quick_es = es.options(request_timeout=_STARTUP_ES_TIMEOUT, max_retries=0)
    try:
        created = await asyncio.to_thread(get_index_setup_service(quick_es).ensure_popular_index)
    except Exception as exc:  # 기동 경계
        logger.warning("인기 검색어 인덱스 확인 실패: %s", exc)
    else:
        if created:
            logger.info("인기 검색어 인덱스를 만들었다")


async def _ensure_code_graph_indexes(mongo_client: MongoClient, db_name: str) -> None:
    # 코드 그래프가 꺼져 있으면(CODE_GRAPH_REPOS 가 비어 있음) 건너뛴다. Mongo 가 내려가 있어도 앱은 떠야 한다.
    # 인덱스가 없으면 같은 commit 의 동시 분석이 문서를 둘 만들 수 있어 경고를 남긴다.
    if not settings.code_graph_repos:
        return
    try:
        await asyncio.to_thread(ensure_code_graph_indexes, mongo_client[db_name])
    except Exception as exc:  # 기동 경계
        logger.warning("코드 그래프 인덱스 확인 실패: %s", exc)


def _start_pipeline_runner(async_mongo_client: AsyncMongoClient, db_name: str) -> PipelineRunner | None:
    # uvicorn 워커 1개 전제. 러너는 lifespan 이 소유한 asyncio 태스크다 (설계 04 문서 1절). DB 이름이 없으면 돌리지 않는다.
    if not db_name:
        logger.warning("MongoDB DB 이름이 없어 에이전트 파이프라인 러너를 시작하지 않는다")
        return None
    runner = build_pipeline_runner(async_mongo_client[db_name], settings)
    runner.start()
    return runner


async def _ensure_agent_indexes(async_mongo_client: AsyncMongoClient, db_name: str) -> None:
    # Mongo 가 내려가 있거나 DB 이름이 비어 있어도 앱은 떠야 한다.
    # 인덱스가 없으면 주제 선정 라운드 단일 보장(부분 unique)이 빠지므로 경고를 남긴다.
    try:
        await ensure_agent_indexes(async_mongo_client[db_name])
    except Exception as exc:  # 기동 경계
        logger.warning("에이전트 파이프라인 인덱스 확인 실패: %s", exc)


app = FastAPI(
    title="VansDevBlog Search Service API",
    version="2.0.0",
    description="Elasticsearch(Nori) 게시물 검색, MongoDB→ES 동기화, KIS 실시간 시세 SSE.",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins,
    allow_origin_regex=r"^https://[a-z0-9.-]+\.cloudtype\.app$",
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

register_exception_handlers(app)

app.include_router(health.router)
app.include_router(search.router)
app.include_router(sync.router)
app.include_router(internal.router)
app.include_router(kis.router)
app.include_router(agent_rounds.router)
app.include_router(code_graph.router)


@app.get("/", include_in_schema=False)
def root() -> RedirectResponse:
    return RedirectResponse("/docs")
