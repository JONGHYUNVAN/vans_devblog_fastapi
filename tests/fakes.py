"""여러 테스트 파일이 쓰는 가짜 구현과 KIS 샘플 데이터."""

import asyncio
import copy
from collections.abc import Iterable, Iterator
from datetime import datetime
from typing import Any

from pymongo.errors import DuplicateKeyError

from app.clients.llm.base import LLMMessage, LLMParams, LLMResponse, LLMUsage
from app.models.agent_call import AgentCall, TokenUsage
from app.models.popular_search import PopularSearch
from app.models.post import Post, PostSearchCriteria, PostSearchPage
from tests.factories import make_call


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


# ---------- 에이전트 파이프라인 (로드맵 2단계) ----------


class _WriteResult:
    def __init__(self, matched_count: int) -> None:
        self.matched_count = matched_count


def _matches(doc: dict[str, Any], query: dict[str, Any]) -> bool:
    """리포지토리가 쓰는 조건만 지원한다: 같음, $in, $gte."""
    for key, condition in query.items():
        value = doc.get(key)
        if isinstance(condition, dict):
            if "$in" in condition and value not in condition["$in"]:
                return False
            if "$gte" in condition and not (value is not None and value >= condition["$gte"]):
                return False
        elif value != condition:
            return False
    return True


class FakeAsyncCursor:
    def __init__(self, docs: list[dict[str, Any]]) -> None:
        self._docs = docs

    def sort(self, key: str, direction: int = 1) -> "FakeAsyncCursor":
        self._docs.sort(key=lambda doc: doc.get(key), reverse=direction < 0)
        return self

    async def to_list(self) -> list[dict[str, Any]]:
        return self._docs


class FakeAsyncCollection:
    """pymongo AsyncCollection 대역. 문서를 깊은 복사로 넣고 꺼내 테스트가 참조를 공유하지 않게 한다."""

    def __init__(self, name: str) -> None:
        self.name = name
        self.docs: list[dict[str, Any]] = []
        self.indexes: list[tuple[Any, dict[str, Any]]] = []

    async def create_index(self, keys: Any, **options: Any) -> None:
        self.indexes.append((keys, options))

    async def insert_one(self, doc: dict[str, Any]) -> None:
        if any(existing["_id"] == doc["_id"] for existing in self.docs):
            raise DuplicateKeyError(f"E11000 duplicate key: {doc['_id']}")
        self.docs.append(copy.deepcopy(doc))

    async def find_one(self, query: dict[str, Any]) -> dict[str, Any] | None:
        return next((copy.deepcopy(doc) for doc in self.docs if _matches(doc, query)), None)

    def find(self, query: dict[str, Any]) -> FakeAsyncCursor:
        return FakeAsyncCursor([copy.deepcopy(doc) for doc in self.docs if _matches(doc, query)])

    async def replace_one(self, query: dict[str, Any], replacement: dict[str, Any]) -> _WriteResult:
        for index, doc in enumerate(self.docs):
            if _matches(doc, query):
                self.docs[index] = copy.deepcopy(replacement)
                return _WriteResult(1)
        return _WriteResult(0)

    async def update_one(self, query: dict[str, Any], update: dict[str, Any]) -> _WriteResult:
        for doc in self.docs:
            if _matches(doc, query):
                doc.update(copy.deepcopy(update["$set"]))
                return _WriteResult(1)
        return _WriteResult(0)

    async def aggregate(self, pipeline: list[dict[str, Any]]) -> FakeAsyncCursor:
        """$match 하나와 $group(_id: None, 필드: {$sum: "$a.b"}) 하나만 지원한다 (AgentCallRepository.sum_usage)."""
        docs = [doc for doc in self.docs if _matches(doc, pipeline[0]["$match"])]
        if not docs:
            return FakeAsyncCursor([])
        row: dict[str, Any] = {"_id": None}
        for field, spec in pipeline[1]["$group"].items():
            if field == "_id":
                continue
            path = spec["$sum"].lstrip("$").split(".")
            row[field] = sum(_dig(doc, path) for doc in docs)
        return FakeAsyncCursor([row])


def _dig(doc: dict[str, Any], path: list[str]) -> int:
    value: Any = doc
    for part in path:
        value = value[part]
    return value


class FakeAsyncDatabase:
    def __init__(self) -> None:
        self._collections: dict[str, FakeAsyncCollection] = {}

    def __getitem__(self, name: str) -> FakeAsyncCollection:
        return self._collections.setdefault(name, FakeAsyncCollection(name))

    def __getattr__(self, name: str) -> FakeAsyncCollection:
        return self[name]


class FakeLLMCall:
    def __init__(self, messages: list[LLMMessage], schema: dict[str, Any], params: LLMParams, timeout: float) -> None:
        self.messages = messages
        self.schema = schema
        self.params = params
        self.timeout = timeout


class FakeLLMClient:
    """outcomes 를 순서대로 돌려준다: str 은 본문(JSON), LLMResponse 는 그대로, Exception 은 던진다.

    호출마다 FakeLLMCall 을 남기고, delay_s 동안 기다려 동시 호출 수(max_active)를 잴 수 있게 한다.
    스크립트가 모자라면 AssertionError.
    """

    def __init__(self, outcomes: Iterable[str | LLMResponse | Exception], delay_s: float = 0.0) -> None:
        self.outcomes = list(outcomes)
        self.delay_s = delay_s
        self.calls: list[FakeLLMCall] = []
        self.active = 0
        self.max_active = 0

    @staticmethod
    def response(text: str, prompt_tokens: int = 100, completion_tokens: int = 50) -> LLMResponse:
        return LLMResponse(text=text, usage=LLMUsage(prompt_tokens=prompt_tokens, completion_tokens=completion_tokens))

    async def complete(
        self, messages: list[LLMMessage], schema: dict[str, Any], params: LLMParams, timeout: float
    ) -> LLMResponse:
        self.calls.append(FakeLLMCall(messages, schema, params, timeout))
        self.active += 1
        self.max_active = max(self.max_active, self.active)
        try:
            if self.delay_s:
                await asyncio.sleep(self.delay_s)
            if not self.outcomes:
                raise AssertionError("FakeLLMClient has no scripted outcome left")
            outcome = self.outcomes.pop(0)
            if isinstance(outcome, Exception):
                raise outcome
            return self.response(outcome) if isinstance(outcome, str) else outcome
        finally:
            self.active -= 1


class FakeAgentCallRepository:
    """AgentCallRepository 대역. 메모리 목록에 쌓고 usage 를 더한다."""

    def __init__(self) -> None:
        self.calls: list[AgentCall] = []

    def preload(self, draft_id: str, prompt_tokens: int = 0, completion_tokens: int = 0) -> None:
        """이미 쓴 토큰을 영수증 하나로 넣는다 (예산 테스트)."""
        self.calls.append(
            make_call(
                call_id=f"preload-{len(self.calls)}",
                draft_id=draft_id,
                prompt_tokens=prompt_tokens,
                completion_tokens=completion_tokens,
            )
        )

    async def add(self, call: AgentCall) -> None:
        self.calls.append(call)

    async def list_by_draft(self, draft_id: str) -> list[AgentCall]:
        return [call for call in self.calls if call.draft_id == draft_id]

    async def sum_usage(self, draft_id: str) -> TokenUsage:
        usage = TokenUsage()
        for call in await self.list_by_draft(draft_id):
            usage.prompt_tokens += call.usage.prompt_tokens
            usage.completion_tokens += call.usage.completion_tokens
        return usage
