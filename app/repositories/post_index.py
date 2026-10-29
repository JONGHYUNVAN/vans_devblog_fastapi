"""ES posts 인덱스 검색·색인·삭제."""

from typing import Any

from elasticsearch import Elasticsearch, NotFoundError, helpers

from app.models.post import Post, PostHit, PostSearchCriteria, PostSearchPage, PostSort

POSTS_INDEX = "posts"  # posts_v2 를 가리키는 alias. 인덱스 버전이 바뀌어도 이 이름으로 접근한다.

_SEARCH_REQUEST_TIMEOUT = 2  # 초. 클라이언트가 응답을 기다리는 한도
_SEARCH_SHARD_TIMEOUT = "1s"  # ES 가 샤드 검색을 끊고 부분 결과를 돌려주는 한도
# page_size 는 100까지 받지만 ES 에는 50개까지만 요청한다. 응답의 page_size·total_pages 는 요청 값 기준이다 (Django 동작 그대로).
_MAX_SEARCH_SIZE = 50

_SORTS: dict[PostSort, list[dict[str, Any]]] = {
    "relevance": [{"_score": {"order": "desc"}}],
    "date_desc": [{"updatedAt": {"order": "desc"}}],
    "date_asc": [{"updatedAt": {"order": "asc"}}],
}

_HIGHLIGHT = {
    "pre_tags": ["<mark>"],
    "post_tags": ["</mark>"],
    "fields": {
        "title": {"fragment_size": 100, "number_of_fragments": 1},
        "description": {"fragment_size": 120, "number_of_fragments": 1},
        "topic": {"fragment_size": 80, "number_of_fragments": 1},
    },
    "require_field_match": False,
}


class PostIndexRepository:
    """posts alias 에 대한 검색·색인·삭제. 문서 id 는 post_id 다."""

    def __init__(self, es: Elasticsearch) -> None:
        self._es = es

    def search(self, criteria: PostSearchCriteria) -> PostSearchPage:
        """조건에 맞는 게시글 한 페이지. 본문(content_text)은 읽지 않는다. ES 오류는 그대로 던진다."""
        response = self._es.options(request_timeout=_SEARCH_REQUEST_TIMEOUT).search(
            index=POSTS_INDEX,
            query={"bool": {"must": [_match_query(criteria.query)], "filter": _filters(criteria)}},
            source={"excludes": ["content_text"]},
            highlight=_HIGHLIGHT,
            from_=(criteria.page - 1) * criteria.page_size,
            size=min(criteria.page_size, _MAX_SEARCH_SIZE),
            sort=_SORTS[criteria.sort],
            timeout=_SEARCH_SHARD_TIMEOUT,
            ignore_unavailable=True,
            allow_partial_search_results=True,
        )
        hits = [
            PostHit(
                post=Post.model_validate(hit["_source"]),
                score=hit["_score"],
                highlight=hit.get("highlight", {}),
            )
            for hit in response["hits"]["hits"]
        ]
        return PostSearchPage(total=response["hits"]["total"]["value"], hits=hits)

    def list_by_prefix(self, prefix: str, *, size: int) -> list[Post]:
        """제목이나 태그가 prefix 로 시작하는 게시글. title·tags 만 읽는다. ES 오류는 그대로 던진다."""
        response = self._es.search(
            index=POSTS_INDEX,
            query={
                "bool": {
                    "should": [
                        {"prefix": {"title": prefix}},
                        {"prefix": {"title.raw": prefix}},
                        {"prefix": {"tags": prefix}},
                    ],
                    "minimum_should_match": 1,
                }
            },
            size=size,
            source=["title", "tags"],
        )
        return [Post.model_validate(hit["_source"]) for hit in response["hits"]["hits"]]

    def upsert(self, post: Post) -> None:
        """post_id 를 문서 id 로 저장한다. 있으면 통째로 바꾼다. ES 오류는 그대로 던진다."""
        self._es.index(index=POSTS_INDEX, id=post.post_id, document=_to_source(post))

    def delete(self, post_id: str) -> bool:
        """문서를 지운다. 없으면 False. 그 밖의 ES 오류는 그대로 던진다."""
        try:
            self._es.delete(index=POSTS_INDEX, id=post_id)
        except NotFoundError:
            return False
        return True

    def delete_all(self) -> int:
        """posts 인덱스의 문서를 모두 지우고 지운 수를 돌려준다. 인덱스와 매핑은 남는다."""
        return self._es.delete_by_query(index=POSTS_INDEX, query={"match_all": {}})["deleted"]

    def count(self) -> int:
        return self._es.count(index=POSTS_INDEX)["count"]

    def list_ids(self) -> list[str]:
        """색인된 모든 문서의 post_id (scroll 로 전부 읽는다)."""
        hits = helpers.scan(self._es, index=POSTS_INDEX, query={"_source": ["post_id"]})
        return [hit["_source"]["post_id"] for hit in hits if hit["_source"].get("post_id")]


def _to_source(post: Post) -> dict[str, Any]:
    # Django(elasticsearch_dsl save)처럼 None·빈 리스트는 저장하지 않고 빈 문자열은 저장한다.
    # 그래서 태그 없는 게시글은 검색 응답에서 tags 가 null 이다.
    return {key: value for key, value in post.model_dump(by_alias=True).items() if value not in (None, [], {})}


def _match_query(query: str) -> dict[str, Any]:
    if not query:
        return {"match_all": {}}
    fuzziness = "1" if len(query) > 3 else "0"
    return {
        "bool": {
            "should": [
                {"match": {"title": {"query": query, "boost": 4, "fuzziness": fuzziness}}},
                {"match": {"topic": {"query": query, "boost": 3, "fuzziness": fuzziness}}},
                {"match": {"description": {"query": query, "boost": 2, "fuzziness": fuzziness}}},
                {"terms": {"tags": [query], "boost": 2}},
            ],
            "minimum_should_match": 1,
        }
    }


def _filters(criteria: PostSearchCriteria) -> list[dict[str, Any]]:
    filters: list[dict[str, Any]] = []
    if criteria.main_category:
        filters.append({"term": {"mainCategory": criteria.main_category}})
    if criteria.sub_category:
        filters.append({"term": {"subCategory": criteria.sub_category}})
    if criteria.language:
        filters.append({"term": {"language": criteria.language}})
    if criteria.tags:
        filters.append({"terms": {"tags": list(criteria.tags)}})
    return filters
