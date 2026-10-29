"""Elasticsearch 클라이언트 생성과 연결 확인."""

import logging

from elasticsearch import Elasticsearch

from app.core.config import Settings

logger = logging.getLogger(__name__)

_MAX_RETRIES = 3


def create_es(settings: Settings) -> Elasticsearch:
    """앱 전체가 공유하는 클라이언트. 생성 시 연결하지 않고 첫 요청 때 맺는다."""
    return Elasticsearch(
        hosts=[settings.elasticsearch_url],
        basic_auth=(settings.elasticsearch_username, settings.elasticsearch_password),
        verify_certs=settings.elasticsearch_verify_certs,
        request_timeout=settings.elasticsearch_timeout,
        max_retries=_MAX_RETRIES,
        retry_on_timeout=True,
    )


def is_es_reachable(es: Elasticsearch, timeout: float) -> bool:
    """클러스터 정보 조회 성공 여부. 실패하면 원인을 warning 로그로 남기고 False.

    ping() 은 401 같은 실패 원인을 삼키므로 info() 로 확인한다.
    Django 버전은 ping 결과와 무관하게 True 를 돌려주는 버그가 있었다.
    """
    try:
        es.options(request_timeout=timeout, max_retries=0, retry_on_timeout=False).info()
        return True
    except Exception as exc:  # 헬스체크 경계: 어떤 실패든 down 으로 보고한다
        logger.warning("Elasticsearch connection check failed: %s", exc)
        return False
