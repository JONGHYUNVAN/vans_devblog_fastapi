"""VansDevBlog 검색·실시간 시세 서비스 (FastAPI)."""

import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import RedirectResponse

from app.clients.es import create_es
from app.clients.mongo import create_mongo_client
from app.core.config import get_settings
from app.core.exceptions import register_exception_handlers
from app.routers import health

logging.basicConfig(
    level=logging.INFO,
    format="%(levelname)s %(asctime)s %(name)s %(message)s",
)
logging.getLogger("elasticsearch").setLevel(logging.WARNING)
logging.getLogger("elastic_transport").setLevel(logging.WARNING)

settings = get_settings()


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    """ES·MongoDB 클라이언트를 한 번 만들어 app.state 에 두고, 종료 때 닫는다."""
    app.state.es = create_es(settings)
    app.state.mongo_client = create_mongo_client(settings)
    yield
    app.state.es.close()
    app.state.mongo_client.close()


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


@app.get("/", include_in_schema=False)
def root() -> RedirectResponse:
    return RedirectResponse("/docs")
