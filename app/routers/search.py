"""검색 엔드포인트. vans_story(Next.js BFF)가 호출한다."""

from typing import Annotated

from fastapi import APIRouter, Depends, Query

from app.dependencies import get_search_service
from app.schemas.search import (
    AutocompleteParams,
    AutocompleteResult,
    CategoryList,
    PopularKeywords,
    SearchParams,
    SearchResult,
)
from app.services.search import SearchService

router = APIRouter(prefix="/api/v1/search", tags=["Search"])


# Django 버전은 ES 오류에 {"error": "Search request failed", "message": 검색 전용 문구}로 500 응답했다.
# 지금은 전역 핸들러의 500 형식이다. 프론트(BFF)는 상태 코드와 message 만 읽는다.
@router.get("/posts/", summary="게시물 검색")
def search_posts(
    params: Annotated[SearchParams, Query()],
    search_service: Annotated[SearchService, Depends(get_search_service)],
) -> SearchResult:
    """키워드·카테고리·태그·언어로 게시물을 검색한다. 같은 조건의 결과는 5분 캐시한다.

    `query` 를 비우면 전체 게시물을 돌려준다. 한 페이지에는 최대 50개까지만 담긴다.
    """
    return search_service.search_posts(params)


@router.get("/autocomplete/", summary="자동완성")
def suggest_keywords(
    params: Annotated[AutocompleteParams, Query()],
    search_service: Annotated[SearchService, Depends(get_search_service)],
) -> AutocompleteResult:
    """제목·태그 중 검색어로 시작하는 것(대소문자 무시). 10분 캐시한다. 검색 엔진 오류 때는 빈 목록이다."""
    return search_service.suggest_keywords(params)


@router.get("/popular/", summary="인기 검색어")
def list_popular_keywords(
    search_service: Annotated[SearchService, Depends(get_search_service)],
) -> PopularKeywords:
    """검색 횟수 상위 10개. 1분 캐시한다. 검색 엔진 오류 때는 빈 목록이다."""
    return search_service.list_popular_keywords()


@router.get("/categories/", summary="카테고리 목록")
def list_categories(
    search_service: Annotated[SearchService, Depends(get_search_service)],
) -> CategoryList:
    """고정 카테고리 목록."""
    return search_service.list_categories()
