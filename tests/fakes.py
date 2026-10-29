"""여러 테스트 파일이 쓰는 가짜 구현과 KIS 샘플 데이터."""

import asyncio
from collections.abc import Iterator
from datetime import datetime
from typing import Any

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


def kis_trade_body(**overrides: str) -> str:
    """체결가(H0STCNT0) 본문 15필드. overrides 는 {"f2": "58300"} 처럼 필드 번호로 바꾼다."""
    fields = ["005930", "153000", "58300", "2", "200", "0.34", "58200", "58000", "58500", "57800"]
    fields += ["58310", "58290", "100", "5000000", "290000000000"]
    for name, value in overrides.items():
        fields[int(name.removeprefix("f"))] = value
    return "^".join(fields)


def kis_orderbook_body() -> str:
    """호가(H0STASP0) 본문 45필드."""
    fields = ["005930", "153000", "0"]
    fields += [str(58300 + i * 10) for i in range(10)]  # 매도호가
    fields += [str(58290 - i * 10) for i in range(10)]  # 매수호가
    fields += [str(1000 + i * 100) for i in range(10)]  # 매도잔량
    fields += [str(900 + i * 100) for i in range(10)]  # 매수잔량
    fields += ["55000", "49500"]
    return "^".join(fields)


class FakeKISStream:
    """KISStreamManager 대역. 스냅샷은 고정값, 구독 큐는 테스트가 직접 채운다."""

    def __init__(self, snapshot: dict[str, Any] | None = None) -> None:
        self.snapshot = snapshot
        self.queues: dict[str, asyncio.Queue[tuple[str, dict[str, Any]]]] = {}
        self.unsubscribed: list[str] = []
        self.ensure_calls = 0

    def get_snapshot(self, symbol: str) -> dict[str, Any] | None:
        return self.snapshot

    def ensure_connected(self) -> None:
        self.ensure_calls += 1

    def subscribe(self, symbol: str) -> asyncio.Queue[tuple[str, dict[str, Any]]]:
        self.queues[symbol] = asyncio.Queue()
        return self.queues[symbol]

    def unsubscribe(self, symbol: str, queue: asyncio.Queue[tuple[str, dict[str, Any]]]) -> None:
        self.unsubscribed.append(symbol)
