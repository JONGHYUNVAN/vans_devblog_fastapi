"""VansDevBlog 검색·실시간 시세 서비스 (FastAPI)."""

import logging

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import RedirectResponse

from app.config import get_settings
from app.errors import register_error_handlers
from app.routers import health

logging.basicConfig(
    level=logging.INFO,
    format="%(levelname)s %(asctime)s %(name)s %(message)s",
)
logging.getLogger("elasticsearch").setLevel(logging.WARNING)
logging.getLogger("elastic_transport").setLevel(logging.WARNING)

settings = get_settings()

app = FastAPI(
    title="VansDevBlog Search Service API",
    version="2.0.0",
    description="Elasticsearch(Nori) 게시물 검색, MongoDB→ES 동기화, KIS 실시간 시세 SSE.",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins,
    allow_origin_regex=r"^https://[a-z0-9.-]+\.cloudtype\.app$",
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

register_error_handlers(app)

app.include_router(health.router)


@app.get("/", include_in_schema=False)
def root():
    return RedirectResponse("/docs")
