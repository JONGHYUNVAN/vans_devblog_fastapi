"""헬스체크. ES·Mongo 를 2초 타임아웃으로 확인하고 결과를 30분 캐시한다."""

import logging
from datetime import datetime
from typing import Any
from zoneinfo import ZoneInfo

from app.cache import HEALTH_TTL, cache
from app.clients.es_client import check_connection, create_es
from app.clients.mongo_client import MongoDBClient

logger = logging.getLogger("search")

SERVICE_NAME = "VansDevBlog Search Service"
VERSION = "1.0.0"
KST = ZoneInfo("Asia/Seoul")

_CACHE_KEY = "health_check_result"
_TS_KEY = "health_check_timestamp"


def _now_str() -> str:
    return datetime.now(KST).strftime("%Y-%m-%d %H:%M:%S")


def _es_ok() -> bool:
    try:
        es = create_es(timeout=2)
        try:
            return check_connection(es)
        finally:
            es.close()
    except Exception as e:
        logger.warning("Elasticsearch health check failed: %s", e)
        return False


def _mongo_ok() -> bool:
    try:
        with MongoDBClient(timeout=2) as mongo:
            return mongo.check_connection()
    except Exception as e:
        logger.warning("MongoDB health check failed: %s", e)
        return False


def get_health_status() -> dict[str, Any]:
    es_ok = _es_ok()
    mongo_ok = _mongo_ok()
    return {
        "status": "healthy" if es_ok and mongo_ok else "degraded",
        "service": SERVICE_NAME,
        "version": VERSION,
        "components": {
            "elasticsearch": {"status": "up" if es_ok else "down", "connected": es_ok},
            "mongodb": {"status": "up" if mongo_ok else "down", "connected": mongo_ok},
        },
    }


def get_cached_health() -> dict[str, Any]:
    cached = cache.get(_CACHE_KEY)
    if cached:
        return {**cached, "cached": True, "last_check": cache.get(_TS_KEY, "unknown")}

    data = get_health_status()
    ts = _now_str()
    data["cached"] = False
    data["last_check"] = ts
    cache.set(_CACHE_KEY, data, HEALTH_TTL)
    cache.set(_TS_KEY, ts, HEALTH_TTL)
    return data


def unhealthy(error: Exception) -> dict[str, Any]:
    return {
        "status": "unhealthy",
        "service": SERVICE_NAME,
        "version": VERSION,
        "error": str(error),
        "cached": False,
        "last_check": _now_str(),
    }
