"""PostIndexRepository 테스트. ES 에 보내는 쿼리를 확인한다."""

from typing import Any

from app.models.post import PostSearchCriteria
from app.repositories.post_index import POSTS_INDEX, PostIndexRepository


class FakeElasticsearch:
    def __init__(self, hits: list[dict[str, Any]] | None = None) -> None:
        self.hits = hits or []
        self.options_calls: list[dict[str, Any]] = []
        self.search_calls: list[dict[str, Any]] = []

    def options(self, **kwargs: Any) -> "FakeElasticsearch":
        self.options_calls.append(kwargs)
        return self

    def search(self, **kwargs: Any) -> dict[str, Any]:
        self.search_calls.append(kwargs)
        return {"hits": {"total": {"value": len(self.hits)}, "hits": self.hits}}


def _criteria(**overrides: Any) -> PostSearchCriteria:
    values: dict[str, Any] = {
        "query": "",
        "main_category": "",
        "sub_category": "",
        "tags": (),
        "language": None,
        "page": 1,
        "page_size": 20,
        "sort": "relevance",
    }
    return PostSearchCriteria(**(values | overrides))


def test_search_applies_filters_sort_and_size_cap():
    es = FakeElasticsearch()

    PostIndexRepository(es).search(
        _criteria(
            query="React",
            main_category="fe",
            tags=("React", "JS"),
            language="ko",
            page=3,
            page_size=100,
            sort="date_desc",
        )
    )

    call = es.search_calls[0]
    assert call["index"] == POSTS_INDEX
    assert call["query"]["bool"]["filter"] == [
        {"term": {"mainCategory": "fe"}},
        {"term": {"language": "ko"}},
        {"terms": {"tags": ["React", "JS"]}},
    ]
    assert call["query"]["bool"]["must"][0]["bool"]["should"][0] == {
        "match": {"title": {"query": "React", "boost": 4, "fuzziness": "1"}}
    }
    assert call["sort"] == [{"updatedAt": {"order": "desc"}}]
    assert call["from_"] == 200
    assert call["size"] == 50
    assert call["source"] == {"excludes": ["content_text"]}
    assert es.options_calls == [{"request_timeout": 2}]


def test_search_matches_all_when_query_empty():
    es = FakeElasticsearch()

    PostIndexRepository(es).search(_criteria())

    assert es.search_calls[0]["query"] == {"bool": {"must": [{"match_all": {}}], "filter": []}}


def test_search_returns_hits_with_score_and_highlight():
    source = {"post_id": "p1", "title": "React", "mainCategory": "fe", "createdAt": "2025-03-04T11:12:09.369000"}
    es = FakeElasticsearch(hits=[{"_source": source, "_score": None, "highlight": {"title": ["<mark>React</mark>"]}}])

    page = PostIndexRepository(es).search(_criteria(sort="date_asc"))

    assert page.total == 1
    hit = page.hits[0]
    assert hit.post.main_category == "fe"
    assert hit.post.created_at == "2025-03-04T11:12:09.369000"
    assert hit.score is None
    assert hit.highlight == {"title": ["<mark>React</mark>"]}
