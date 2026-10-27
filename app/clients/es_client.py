"""Elasticsearch 클라이언트 생성. 검색 쿼리 로직은 Phase 2 에서 옮긴다."""

import logging
from typing import Optional

from elasticsearch import Elasticsearch

from app.config import Settings, get_settings

logger = logging.getLogger("search")

# posts 는 posts_v2 를 가리키는 alias. 인덱스 버전이 바뀌어도 이 이름으로 접근한다.
POSTS_INDEX = "posts"


def create_es(timeout: Optional[int] = None, settings: Optional[Settings] = None) -> Elasticsearch:
    s = settings or get_settings()
    return Elasticsearch(
        hosts=[s.elasticsearch_url],
        basic_auth=(s.elasticsearch_username, s.elasticsearch_password),
        verify_certs=s.elasticsearch_verify_certs,
        request_timeout=timeout or s.elasticsearch_timeout,
        max_retries=0 if timeout else 3,
        retry_on_timeout=not timeout,
    )


def check_connection(es: Elasticsearch) -> bool:
    """클러스터 정보 조회 성공 여부.

    ping() 은 401 같은 실패 원인을 삼키므로 info() 로 확인해 원인을 로그에 남긴다.
    Django 버전은 ping 결과와 무관하게 True 를 돌려주는 버그가 있었다.
    """
    try:
        es.info()
        return True
    except Exception as e:
        logger.warning("Elasticsearch connection check failed: %s", e)
        return False
