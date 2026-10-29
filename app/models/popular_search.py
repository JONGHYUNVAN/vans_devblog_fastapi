"""ES 인기 검색어 문서."""

from pydantic import BaseModel


class PopularSearch(BaseModel):
    """vans_popular_searches 인덱스의 검색어 하나. 목록 조회에 쓰는 필드만 둔다."""

    query: str
    search_count: int
