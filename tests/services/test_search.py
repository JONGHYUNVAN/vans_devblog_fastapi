"""SearchService 테스트."""

from collections.abc import Callable
from typing import Any

from app.models.popular_search import PopularSearch
from app.models.post import Post, PostHit, PostSearchPage
from app.schemas.search import AutocompleteParams, SearchParams
from app.services.search import SearchService
from tests.fakes import FakePopularSearchRepository, FakePostIndexRepository


def _service(
    post_index: FakePostIndexRepository | None = None,
    popular: FakePopularSearchRepository | None = None,
    background: list[tuple[Callable[..., Any], tuple[Any, ...]]] | None = None,
) -> SearchService:
    queued = background if background is not None else []
    return SearchService(
        post_index_repository=post_index or FakePostIndexRepository(),
        popular_search_repository=popular or FakePopularSearchRepository(),
        run_in_background=lambda func, *args: queued.append((func, args)),
    )


def _page(*posts: Post, total: int | None = None) -> PostSearchPage:
    hits = [PostHit(post=post, score=1.0, highlight={}) for post in posts]
    return PostSearchPage(total=len(hits) if total is None else total, hits=hits)


def test_search_posts_builds_page_and_queues_popular_update():
    post_index = FakePostIndexRepository(page=_page(Post(post_id="p1", title="React"), total=45))
    background: list[tuple[Callable[..., Any], tuple[Any, ...]]] = []

    result = _service(post_index, background=background).search_posts(SearchParams(query="React", page_size=20))

    assert (result.total, result.page, result.page_size, result.total_pages) == (45, 1, 20, 3)
    assert result.post_ids == ["p1"]
    assert result.aggregations == {}
    assert post_index.search_calls[0].language is None
    assert [args for _, args in background] == [("React",)]


def test_search_posts_skips_popular_update_when_no_query_or_no_results():
    background: list[tuple[Callable[..., Any], tuple[Any, ...]]] = []
    service = _service(FakePostIndexRepository(page=_page(Post(post_id="p1"))), background=background)
    service.search_posts(SearchParams(query=""))

    _service(FakePostIndexRepository(), background=background).search_posts(SearchParams(query="없는글"))

    assert background == []


def test_search_posts_cached_per_criteria_including_sort():
    post_index = FakePostIndexRepository(page=_page(Post(post_id="p1")))
    service = _service(post_index)

    service.search_posts(SearchParams(query="React"))
    service.search_posts(SearchParams(query="React"))
    service.search_posts(SearchParams(query="React", sort="date_desc"))

    assert [criteria.sort for criteria in post_index.search_calls] == ["relevance", "date_desc"]


def test_search_posts_description_snippet_prefers_highlight():
    long_description = "가" * 151
    hits = [
        PostHit(post=Post(post_id="p1", description=long_description), score=1.0, highlight={}),
        PostHit(
            post=Post(post_id="p2", description="짧은 설명"),
            score=1.0,
            highlight={"description": ["<mark>짧은</mark>"]},
        ),
    ]
    post_index = FakePostIndexRepository(page=PostSearchPage(total=2, hits=hits))

    result = _service(post_index).search_posts(SearchParams())

    assert result.results[0].description == "가" * 150 + "..."
    assert result.results[1].description == "<mark>짧은</mark>"


def test_record_search_logs_and_swallows_error():
    popular = FakePopularSearchRepository(error=RuntimeError("es down"))
    background: list[tuple[Callable[..., Any], tuple[Any, ...]]] = []
    _service(FakePostIndexRepository(page=_page(Post(post_id="p1"))), popular, background).search_posts(
        SearchParams(query="React")
    )

    func, args = background[0]
    func(*args)

    assert popular.upserted == []


def test_suggest_keywords_dedupes_prefix_matches_in_hit_order():
    posts = [
        Post(title="React 입문", tags=["react", "JS"]),
        Post(title="Redux", tags=["React 입문"]),
    ]

    result = _service(FakePostIndexRepository(prefix_posts=posts)).suggest_keywords(
        AutocompleteParams(query="re", limit=3)
    )

    assert result.suggestions == ["React 입문", "react", "Redux"]
    assert result.query == "re"


def test_suggest_keywords_empty_and_not_cached_when_es_fails():
    post_index = FakePostIndexRepository(error=RuntimeError("es down"))
    service = _service(post_index)

    first = service.suggest_keywords(AutocompleteParams(query="Re"))
    service.suggest_keywords(AutocompleteParams(query="Re"))

    assert first.suggestions == []
    assert len(post_index.prefix_calls) == 2


def test_list_popular_keywords_maps_count_and_caches():
    popular = FakePopularSearchRepository(popular=[PopularSearch(query="정렬", search_count=3)])
    service = _service(popular=popular)

    first = service.list_popular_keywords()
    service.list_popular_keywords()

    assert first.model_dump() == {"popular_searches": [{"query": "정렬", "count": 3}]}
    assert popular.list_calls == 1


def test_list_popular_keywords_empty_and_not_cached_when_es_fails():
    popular = FakePopularSearchRepository(error=RuntimeError("es down"))
    service = _service(popular=popular)

    first = service.list_popular_keywords()
    service.list_popular_keywords()

    assert first.popular_searches == []
    assert popular.list_calls == 2
