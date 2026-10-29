"""VansDevBlog 검색·실시간 시세 서비스 (FastAPI)."""

import asyncio
import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from elasticsearch import Elasticsearch
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import RedirectResponse

from app.core.config import get_settings
from app.core.exceptions import register_exception_handlers
from app.dependencies import get_index_setup_service, open_clients
from app.routers import health, internal, search, sync

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
    """ES·MongoDB 클라이언트를 한 번 만들어 app.state 에 두고, 종료 때 닫는다. 인기 검색어 인덱스도 준비한다."""
    with open_clients(settings) as (es, mongo_client):
        app.state.es = es
        app.state.mongo_client = mongo_client
        await _ensure_popular_index(es)
        yield


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


@app.get("/", include_in_schema=False)
def root() -> RedirectResponse:
    return RedirectResponse("/docs")
