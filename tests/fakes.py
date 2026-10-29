"""여러 테스트 파일이 쓰는 가짜 리포지토리."""

from collections.abc import Iterator
from datetime import datetime

from app.models.popular_search import PopularSearch
from app.models.post import Post, PostSearchCriteria, PostSearchPage


class FakePostIndexRepository:
    """error 는 읽기(search·list_by_prefix·count·list_ids)에서 던진다. fail_ids 의 게시글은 upsert 가 실패한다."""

    def __init__(
        self,
        page: PostSearchPage | None = None,
        prefix_posts: list[Post] | None = None,
        error: Exception | None = None,
        docs: dict[str, Post] | None = None,
        fail_ids: set[str] | None = None,
    ) -> None:
        self.page = page or PostSearchPage(total=0, hits=[])
        self.prefix_posts = prefix_posts or []
        self.error = error
        self.docs = dict(docs or {})
        self.fail_ids = fail_ids or set()
        self.search_calls: list[PostSearchCriteria] = []
        self.prefix_calls: list[tuple[str, int]] = []

    def search(self, criteria: PostSearchCriteria) -> PostSearchPage:
        self.search_calls.append(criteria)
        if self.error:
            raise self.error
        return self.page

    def list_by_prefix(self, prefix: str, *, size: int) -> list[Post]:
        self.prefix_calls.append((prefix, size))
        if self.error:
            raise self.error
        return self.prefix_posts

    def upsert(self, post: Post) -> None:
        if post.post_id in self.fail_ids:
            raise RuntimeError("index failed")
        self.docs[post.post_id] = post

    def delete(self, post_id: str) -> bool:
        return self.docs.pop(post_id, None) is not None

    def delete_all(self) -> int:
        deleted = len(self.docs)
        self.docs.clear()
        return deleted

    def count(self) -> int:
        if self.error:
            raise self.error
        return len(self.docs)

    def list_ids(self) -> list[str]:
        if self.error:
            raise self.error
        return list(self.docs)


class FakePopularSearchRepository:
    def __init__(self, popular: list[PopularSearch] | None = None, error: Exception | None = None) -> None:
        self.popular = popular or []
        self.error = error
        self.list_calls = 0
        self.upserted: list[str] = []

    def list_top(self, limit: int) -> list[PopularSearch]:
        self.list_calls += 1
        if self.error:
            raise self.error
        return self.popular[:limit]

    def upsert(self, query: str, searched_at: datetime) -> None:
        if self.error:
            raise self.error
        self.upserted.append(query)


class FakePostRepository:
    """error 는 모든 조회에서 던진다 (순회는 첫 항목을 읽을 때)."""

    def __init__(self, posts: list[Post] | None = None, error: Exception | None = None) -> None:
        self.posts = posts or []
        self.error = error
        self.since_calls: list[datetime] = []

    def count(self) -> int:
        if self.error:
            raise self.error
        return len(self.posts)

    def iter_all(self, batch_size: int = 100) -> Iterator[Post]:
        if self.error:
            raise self.error
        yield from self.posts

    def iter_updated_since(self, since: datetime, batch_size: int = 100) -> Iterator[Post]:
        self.since_calls.append(since)
        if self.error:
            raise self.error
        yield from self.posts

    def list_ids(self) -> list[str]:
        if self.error:
            raise self.error
        return [post.post_id for post in self.posts if post.post_id]
