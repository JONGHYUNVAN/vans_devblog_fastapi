"""게시물 검색, 자동완성, 인기 검색어, 카테고리. 결과는 인프로세스 캐시에 둔다."""

import logging
import math
from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any

from app.core.cache import AUTOCOMPLETE_TTL, POPULAR_TTL, SEARCH_TTL, cache
from app.models.post import Post, PostHit, PostSearchCriteria
from app.repositories.popular_search import PopularSearchRepository
from app.repositories.post_index import PostIndexRepository
from app.schemas.search import (
    AutocompleteParams,
    AutocompleteResult,
    CategoryList,
    PopularKeyword,
    PopularKeywords,
    SearchHit,
    SearchParams,
    SearchResult,
)

logger = logging.getLogger(__name__)

POPULAR_LIMIT = 10
_SNIPPET_LENGTH = 150
_POPULAR_CACHE_KEY = "popular_searches"

# 계약: Django 버전은 MongoDB 클라이언트를 만들지 않아 항상 이 고정 목록을 돌려줬다.
_CATEGORIES = ["Frontend", "Backend", "Database", "DevOps", "AI/ML", "Mobile", "Security", "Testing"]

RunInBackground = Callable[..., Any]


class SearchService:
    """posts·인기 검색어 인덱스 조회와 캐시.

    인기 검색어 갱신은 응답을 늦추지 않도록 run_in_background 로 넘긴다 (dependencies.py 가 BackgroundTasks.add_task 를 준다).
    """

    def __init__(
        self,
        post_index_repository: PostIndexRepository,
        popular_search_repository: PopularSearchRepository,
        run_in_background: RunInBackground,
    ) -> None:
        self._post_index_repository = post_index_repository
        self._popular_search_repository = popular_search_repository
        self._run_in_background = run_in_background

    def search_posts(self, params: SearchParams) -> SearchResult:
        """조건에 맞는 게시물 한 페이지. 같은 조건은 5분 캐시한다. ES 오류는 그대로 던진다.

        검색어가 있고 결과가 있으면 응답 뒤에 인기 검색어 횟수를 올린다. 캐시 적중 때는 올리지 않는다 (Django 와 같다).
        """
        criteria = PostSearchCriteria(
            query=params.query,
            main_category=params.main_category,
            sub_category=params.sub_category,
            tags=tuple(params.tags),
            language=None if params.language == "all" else params.language,
            page=params.page,
            page_size=params.page_size,
            sort=params.sort,
        )
        # 키는 조건 전체다. Django 버전은 키에 정렬이 빠져 있어 5분 안에 정렬만 바꾼 요청에 이전 결과를 돌려줬다.
        cache_key = f"search:{criteria!r}"
        cached: SearchResult | None = cache.get(cache_key)
        if cached is not None:
            return cached

        page = self._post_index_repository.search(criteria)
        result = SearchResult(
            total=page.total,
            page=params.page,
            page_size=params.page_size,
            total_pages=math.ceil(page.total / params.page_size),
            post_ids=[hit.post.post_id for hit in page.hits],
            results=[_to_search_hit(hit) for hit in page.hits],
            aggregations={},
        )
        cache.set(cache_key, result, SEARCH_TTL)
        if params.query and page.total > 0:
            self._run_in_background(self._record_search, params.query)
        return result

    def suggest_keywords(self, params: AutocompleteParams) -> AutocompleteResult:
        """제목·태그 중 검색어로 시작하는 것. 10분 캐시한다. ES 오류는 캐시하지 않고 빈 목록으로 응답한다 (Django 와 같다)."""
        # language 는 Django 버전도 쿼리에 쓰지 않아 결과가 같으므로 키에서 뺀다.
        # limit 은 넣는다. Django 버전은 키에 limit 이 없어, 작은 limit 으로 먼저 캐시된 결과를 큰 limit 요청에도 돌려줬다.
        cache_key = f"autocomplete:{params.query}:{params.limit}"
        suggestions: list[str] | None = cache.get(cache_key)
        if suggestions is None:
            try:
                posts = self._post_index_repository.list_by_prefix(params.query, size=params.limit)
            except Exception as exc:  # 계약: 자동완성 실패는 빈 목록 200 으로 응답한다
                logger.warning("자동완성 조회 실패: %s", exc)
                return AutocompleteResult(suggestions=[], query=params.query)
            suggestions = _prefix_matches(posts, params.query, params.limit)
            cache.set(cache_key, suggestions, AUTOCOMPLETE_TTL)
        return AutocompleteResult(suggestions=suggestions, query=params.query)

    def list_popular_keywords(self) -> PopularKeywords:
        """검색 횟수 상위 10개. 1분 캐시한다. ES 오류는 캐시하지 않고 빈 목록으로 응답한다 (Django 와 같다)."""
        cached: PopularKeywords | None = cache.get(_POPULAR_CACHE_KEY)
        if cached is not None:
            return cached

        try:
            popular_searches = self._popular_search_repository.list_top(POPULAR_LIMIT)
        except Exception as exc:  # 계약: 인기 검색어 조회 실패는 빈 목록 200 으로 응답한다
            logger.warning("인기 검색어 조회 실패: %s", exc)
            return PopularKeywords(popular_searches=[])
        result = PopularKeywords(
            popular_searches=[PopularKeyword(query=item.query, count=item.search_count) for item in popular_searches]
        )
        cache.set(_POPULAR_CACHE_KEY, result, POPULAR_TTL)
        return result

    def list_categories(self) -> CategoryList:
        """고정 카테고리 목록."""
        return CategoryList(categories=_CATEGORIES)

    def _record_search(self, query: str) -> None:
        # 응답을 보낸 뒤 실행되므로 실패해도 알릴 곳이 없다. 검색 결과와 무관한 집계라 로그만 남긴다.
        try:
            self._popular_search_repository.upsert(query, datetime.now(UTC))
        except Exception as exc:  # 계약: 인기 검색어 갱신 실패는 검색에 영향을 주지 않는다
            logger.warning("인기 검색어 갱신 실패 (%s): %s", query, exc)


def _to_search_hit(hit: PostHit) -> SearchHit:
    post = hit.post
    return SearchHit(
        post_id=post.post_id,
        title=post.title,
        description=_description_snippet(post, hit.highlight),
        topic=post.topic,
        main_category=post.main_category,
        sub_category=post.sub_category,
        tags=post.tags,
        author=post.author,
        language=post.language,
        created_at=post.created_at,
        updated_at=post.updated_at,
        score=hit.score,
        highlight=hit.highlight,
    )


def _description_snippet(post: Post, highlight: dict[str, list[str]]) -> str:
    """하이라이트된 설명이 있으면 그 조각, 없으면 설명 앞 150자(넘치면 ...)."""
    if "description" in highlight:
        return highlight["description"][0]
    description = post.description or ""
    if len(description) > _SNIPPET_LENGTH:
        return description[:_SNIPPET_LENGTH] + "..."
    return description


def _prefix_matches(posts: list[Post], prefix: str, limit: int) -> list[str]:
    """제목·태그 중 prefix 로 시작하는 것(대소문자 무시)을 중복 없이 limit 개까지.

    Django 버전은 set 으로 모아 순서가 프로세스마다 달랐다. 검색 결과 순서(제목 다음 태그)를 따른다.
    """
    lowered = prefix.lower()
    candidates = [text for post in posts for text in [post.title or "", *(post.tags or [])]]
    matches = dict.fromkeys(text for text in candidates if text.lower().startswith(lowered))
    return list(matches)[:limit]
