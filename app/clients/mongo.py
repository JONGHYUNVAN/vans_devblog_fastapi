"""MongoDB 클라이언트 생성과 연결 확인."""

import logging
from typing import Any

import certifi
import pymongo
from pymongo import MongoClient

from app.core.config import Settings

logger = logging.getLogger(__name__)

_SERVER_SELECTION_TIMEOUT_MS = 10_000
_CONNECT_TIMEOUT_MS = 10_000
_SOCKET_TIMEOUT_MS = 20_000


def _connection_url(settings: Settings) -> str:
    if settings.mongodb_uri:
        return settings.mongodb_uri
    if settings.mongodb_username and settings.mongodb_password:
        return (
            f"mongodb://{settings.mongodb_username}:{settings.mongodb_password}"
            f"@{settings.mongodb_host}:{settings.mongodb_port}/{settings.mongodb_db_name}"
            f"?authSource={settings.mongodb_auth_source}"
            f"&directConnection={str(settings.mongodb_direct_connection).lower()}"
        )
    return f"mongodb://{settings.mongodb_host}:{settings.mongodb_port}/{settings.mongodb_db_name}"


def _uses_tls(url: str) -> bool:
    lowered = url.lower()
    return lowered.startswith("mongodb+srv://") or "tls=true" in lowered or "ssl=true" in lowered


def create_mongo_client(settings: Settings) -> MongoClient:
    """앱 전체가 공유하는 클라이언트.

    connect=False 라서 생성 시 연결하지 않는다. mongodb+srv 의 DNS 조회도 첫 요청 때 한다 —
    생성자에서 조회하면 DNS 실패가 앱 기동 실패가 된다.
    """
    url = _connection_url(settings)
    options: dict[str, Any] = {
        "serverSelectionTimeoutMS": _SERVER_SELECTION_TIMEOUT_MS,
        "connectTimeoutMS": _CONNECT_TIMEOUT_MS,
        "socketTimeoutMS": _SOCKET_TIMEOUT_MS,
    }
    # Windows 로컬의 OpenSSL CA 문제 대응. tlsCAFile 은 TLS 를 강제하므로 TLS 연결일 때만 지정한다.
    if _uses_tls(url):
        options["tlsCAFile"] = certifi.where()
    return MongoClient(url, connect=False, **options)


def is_mongo_reachable(mongo_client: MongoClient, timeout: float) -> bool:
    """ping 성공 여부. 실패하면 원인을 warning 로그로 남기고 False."""
    try:
        # pymongo.timeout 은 서버 선택까지 포함해 이 블록 전체의 시간을 제한한다.
        with pymongo.timeout(timeout):
            mongo_client.admin.command("ping")
        return True
    except Exception as exc:  # 헬스체크 경계: 어떤 실패든 down 으로 보고한다
        logger.warning("MongoDB connection check failed: %s", exc)
        return False
