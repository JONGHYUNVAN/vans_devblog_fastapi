"""ES 인기 검색어 인덱스 조회·갱신과 인덱스 생성."""

from datetime import datetime

from elasticsearch import Elasticsearch

from app.models.popular_search import POPULAR_SEARCH_INDEX_MAPPING, PopularSearch

POPULAR_SEARCHES_INDEX = "vans_popular_searches"


class PopularSearchRepository:
    """vans_popular_searches 인덱스. 검색어마다 문서 하나에 검색 횟수를 센다."""

    def __init__(self, es: Elasticsearch) -> None:
        self._es = es

    def list_top(self, limit: int) -> list[PopularSearch]:
        """검색 횟수가 많은 순, 같으면 최근에 검색된 순. ES 오류는 그대로 던진다."""
        response = self._es.search(
            index=POPULAR_SEARCHES_INDEX,
            size=limit,
            sort=[{"search_count": {"order": "desc"}}, {"last_searched": {"order": "desc"}}],
            source=["query", "search_count"],
        )
        return [PopularSearch.model_validate(hit["_source"]) for hit in response["hits"]["hits"]]

    def upsert(self, query: str, searched_at: datetime) -> None:
        """검색어의 횟수를 1 올린다. 없으면 횟수 1로 만든다. ES 오류는 그대로 던진다.

        조회한 뒤 갱신하므로 같은 검색어가 동시에 처음 들어오면 문서가 둘 생길 수 있다 (Django 동작 그대로).
        """
        response = self._es.search(index=POPULAR_SEARCHES_INDEX, query={"term": {"query.keyword": query}})
        hits = response["hits"]["hits"]
        if hits:
            self._es.update(
                index=POPULAR_SEARCHES_INDEX,
                id=hits[0]["_id"],
                script={
                    "source": "ctx._source.search_count += 1; ctx._source.last_searched = params.now",
                    "lang": "painless",
                    "params": {"now": searched_at.isoformat()},
                },
            )
        else:
            self._es.index(
                index=POPULAR_SEARCHES_INDEX,
                document={"query": query, "search_count": 1, "last_searched": searched_at, "created_at": searched_at},
                refresh=True,
            )

    def create_index_if_missing(self) -> bool:
        """인덱스가 없으면 만든다. 만들었으면 True. ES 오류는 그대로 던진다."""
        if self._es.indices.exists(index=POPULAR_SEARCHES_INDEX):
            return False
        self._es.indices.create(index=POPULAR_SEARCHES_INDEX, **POPULAR_SEARCH_INDEX_MAPPING)
        return True

    def delete_index(self) -> bool:
        """인덱스를 지운다. 없었으면 False. ES 오류는 그대로 던진다."""
        if not self._es.indices.exists(index=POPULAR_SEARCHES_INDEX):
            return False
        self._es.indices.delete(index=POPULAR_SEARCHES_INDEX)
        return True
