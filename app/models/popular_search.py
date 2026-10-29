"""ES 인기 검색어 문서와 인덱스 매핑."""

from typing import Any

from pydantic import BaseModel

from app.models.analyzers import BASE_INDEX_SETTINGS

POPULAR_SEARCH_INDEX_MAPPING: dict[str, Any] = {
    "settings": BASE_INDEX_SETTINGS,
    "mappings": {
        "properties": {
            "query": {"type": "text", "analyzer": "korean_analyzer", "fields": {"keyword": {"type": "keyword"}}},
            "search_count": {"type": "integer"},
            "last_searched": {"type": "date"},
            "created_at": {"type": "date"},
        }
    },
}


class PopularSearch(BaseModel):
    """vans_popular_searches 인덱스의 검색어 하나. 목록 조회에 쓰는 필드만 둔다."""

    query: str
    search_count: int
