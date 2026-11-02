"""여러 테스트 파일이 쓰는 가짜 구현과 KIS 샘플 데이터."""

import asyncio
import copy
import json
from collections.abc import Callable, Iterable, Iterator
from datetime import datetime
from typing import Any

from pymongo.errors import DuplicateKeyError

from app.clients.llm.base import LLMMessage, LLMParams, LLMResponse, LLMUsage
from app.clients.llm.registry import LLMClientRegistry
from app.clients.publisher.base import PostCategory, PostCreatePayload, Publisher, PublishResult
from app.core.config import Settings
from app.models.agent_call import AgentCall, TokenUsage
from app.models.enums import AgentSlot, PostStatus
from app.models.popular_search import PopularSearch
from app.models.post import Post, PostSearchCriteria, PostSearchPage
from app.models.post_draft import PostDraft
from app.models.publishing_round import LockedTopic, PublishingRound, TopicAttempt
from app.models.topic import TopicRegistryEntry
from app.repositories.agent_call import AgentCallRepository
from app.repositories.agent_round import PublishingRoundRepository
from app.repositories.post_draft import PostDraftRepository
from app.repositories.topic_registry import TopicRegistryRepository
from app.services.agent_calls import AgentCallService
from app.services.draft_decision import DraftDecisionService
from app.services.draft_pipeline import DraftPipelineService
from app.services.publishing import PublishingService
from app.services.round import RoundService
from app.services.runner import PipelineRunner
from app.services.topic_selection import TopicSelectionService
from app.services.validation import ValidationGate
from tests.factories import make_call, make_round, make_topic


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
    """리포지토리가 쓰는 조건만 지원한다: 같음, $in, $gte, $ne."""
    for key, condition in query.items():
        value = doc.get(key)
        if isinstance(condition, dict):
            if "$in" in condition and value not in condition["$in"]:
                return False
            if "$gte" in condition and not (value is not None and value >= condition["$gte"]):
                return False
            if "$ne" in condition and value == condition["$ne"]:
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
        for keys, options in self.indexes:
            # 부분 unique 인덱스(단일 키)만 흉내 낸다 (agent_rounds 의 one_topic_selection_round)
            partial = options.get("partialFilterExpression")
            if not options.get("unique") or not isinstance(keys, str) or not _matches(doc, partial or {}):
                continue
            if any(_matches(existing, partial or {}) and existing.get(keys) == doc.get(keys) for existing in self.docs):
                raise DuplicateKeyError(f"E11000 duplicate key: {options.get('name', keys)}")
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
            outcome = self._next_outcome(messages)
            if isinstance(outcome, Exception):
                raise outcome
            return self.response(outcome) if isinstance(outcome, str) else outcome
        finally:
            self.active -= 1

    def _next_outcome(self, messages: list[LLMMessage]) -> str | LLMResponse | Exception:
        if not self.outcomes:
            raise AssertionError("FakeLLMClient has no scripted outcome left")
        return self.outcomes.pop(0)


# 시스템 프롬프트(v1 템플릿)의 문구로 호출 종류를 가린다. LLMClient 계약에는 종류가 없다 (설계 06 문서 6절).
_KIND_MARKERS = {
    "주제를 세 개 제안": "PROPOSE_TOPIC",
    "글 한 편을 한국어로 쓴다": "WRITE_DRAFT",
    "수정본을 낸다": "REVISE_DRAFT",
    "`content_markdown` 을 `lens`": "REVIEW_FULL",
    "해소됐는지 확인": "SPOT_CHECK",
    "리뷰 분쟁을 판정": "ARBITRATE",
}

ScriptOutcome = str | LLMResponse | Exception | Callable[[dict[str, Any]], "str | LLMResponse | Exception"]


def call_kind_of(messages: list[LLMMessage]) -> str:
    system = messages[0].content
    for marker, kind in _KIND_MARKERS.items():
        if marker in system:
            return kind
    raise AssertionError("cannot tell the call kind from the system prompt")


def call_input_of(messages: list[LLMMessage]) -> dict[str, Any]:
    """첫 user 메시지의 입력 JSON (builders.render_messages 형식)."""
    return json.loads(messages[1].content.removeprefix("입력(JSON):\n"))


class ScriptedLLMClient(FakeLLMClient):
    """종류별 스크립트. 값은 FakeLLMClient 와 같고, callable 이면 입력 JSON 을 받아 값을 만든다.

    종류의 스크립트가 list 면 순서대로 소비하고, callable 하나면 그 종류의 모든 호출에 쓴다. 스크립트가 없거나 모자라면 AssertionError.
    """

    def __init__(self, scripts: dict[str, list[ScriptOutcome] | Callable[..., Any]], delay_s: float = 0.0) -> None:
        super().__init__([], delay_s)
        self.scripts = {
            kind: (list(script) if isinstance(script, list) else script) for kind, script in scripts.items()
        }
        self.kinds: list[str] = []

    def _next_outcome(self, messages: list[LLMMessage]) -> str | LLMResponse | Exception:
        kind = call_kind_of(messages)
        self.kinds.append(kind)
        script = self.scripts.get(kind)
        if script is None or (isinstance(script, list) and not script):
            raise AssertionError(f"ScriptedLLMClient has no outcome left for {kind}")
        outcome = script.pop(0) if isinstance(script, list) else script
        if callable(outcome) and not isinstance(outcome, Exception):
            return outcome(call_input_of(messages))
        return outcome


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


# ---------- 파이프라인 하네스 (로드맵 4단계) ----------


class FakePostBE:
    """Publisher 대역. list_categories 는 고정 목록, publish 는 outcomes 를 순서대로 쓴다(비면 성공).

    outcome: str 은 게시글 id, Exception 은 던진다, callable 은 payload 를 받아 둘 중 하나를 돌려준다(호출 중 사고를 흉내 낸다).
    delay_s 는 HTTP 호출이 걸리는 시간을 흉내 낸다(종료와 겹치는 발행 테스트).
    """

    def __init__(
        self, outcomes: Iterable[Any] = (), categories: list[PostCategory] | None = None, delay_s: float = 0.0
    ) -> None:
        self.outcomes = list(outcomes)
        self.categories = categories or []
        self.delay_s = delay_s
        self.payloads: list[PostCreatePayload] = []
        self.published: list[str] = []  # 돌려준 게시글 id

    async def list_categories(self) -> list[PostCategory]:
        return self.categories

    async def publish(self, payload: PostCreatePayload) -> PublishResult:
        self.payloads.append(payload)
        if self.delay_s:
            await asyncio.sleep(self.delay_s)
        outcome = self.outcomes.pop(0) if self.outcomes else None
        if callable(outcome):
            outcome = outcome(payload)
        if isinstance(outcome, Exception):
            raise outcome
        post_id = outcome or f"post-{len(self.payloads)}"
        self.published.append(post_id)
        return PublishResult(external_post_id=post_id)


async def _no_sleep(seconds: float) -> None:
    return None


class PipelineHarness:
    """FakeAsyncDatabase 위에 실제 리포지토리·서비스를 조립한다. LLM 은 ScriptedLLMClient, Post BE 는 FakePostBE 다.

    settings 기본: 본문 길이 하한 1, 호출 재시도 2, 백오프 없음, 승인 토글 켬.
    """

    def __init__(
        self,
        scripts: dict[str, Any] | None = None,
        *,
        delay_s: float = 0.0,
        database: FakeAsyncDatabase | None = None,
        round_repository: PublishingRoundRepository | None = None,
        publisher: Publisher | None = None,
        **settings_overrides: Any,
    ) -> None:
        self.database = database or FakeAsyncDatabase()
        self.llm = ScriptedLLMClient(scripts or {}, delay_s)
        self.settings = Settings(
            _env_file=None,
            **({"draft_min_chars": 1, "draft_max_chars": 100_000, "max_llm_retries": 2} | settings_overrides),
        )
        self.rounds = round_repository or PublishingRoundRepository(self.database)
        self.drafts = PostDraftRepository(self.database)
        self.topics = TopicRegistryRepository(self.database)
        self.calls = AgentCallRepository(self.database)
        self.publisher = publisher or FakePostBE()
        self.round_service = RoundService(self.rounds, self.drafts, self.topics, publisher=self.publisher)
        self.call_service = AgentCallService(
            self.calls,
            LLMClientRegistry({"fake": self.llm}, concurrency_per_provider=3),
            self.settings,
            sleep=_no_sleep,
        )
        self.publishing = PublishingService(
            draft_repository=self.drafts,
            topic_registry_repository=self.topics,
            publisher=self.publisher,
            settings=self.settings,
            sleep=_no_sleep,
        )
        self.decisions = DraftDecisionService(
            draft_repository=self.drafts,
            agent_call_repository=self.calls,
            topic_registry_repository=self.topics,
            round_service=self.round_service,
            settings=self.settings,
        )
        self.draft_pipeline = DraftPipelineService(
            draft_repository=self.drafts,
            round_service=self.round_service,
            agent_call_service=self.call_service,
            gate=ValidationGate(min_chars=self.settings.draft_min_chars, max_chars=self.settings.draft_max_chars),
            publisher=self.publishing,
            settings=self.settings,
        )
        self.topic_selection = TopicSelectionService(
            round_repository=self.rounds,
            draft_repository=self.drafts,
            topic_registry_repository=self.topics,
            round_service=self.round_service,
            agent_call_service=self.call_service,
            settings=self.settings,
        )
        self.runner = PipelineRunner(
            round_repository=self.rounds,
            draft_repository=self.drafts,
            round_service=self.round_service,
            topic_selection_service=self.topic_selection,
            draft_pipeline_service=self.draft_pipeline,
            settings=self.settings,
        )

    async def seed_round(self, round_id: str = "round-1") -> PublishingRound:
        """주제가 확정된 IN_PROGRESS 라운드와 TOPIC_LOCKED 글 3편, RESERVED 레지스트리 3건."""
        publishing_round = make_round(round_id)
        attempt = TopicAttempt(attempt_number=1)
        for index, slot in enumerate(AgentSlot):
            topic = make_topic(title=f"주제 {slot}", keywords=[f"k{index}a", f"k{index}b", f"k{index}c"])
            attempt.proposals[slot] = [topic]
            attempt.locked[slot] = LockedTopic(slot=slot, source_attempt=1, proposal_index=0, proposal=topic)
        publishing_round.topic_attempts = [attempt]
        publishing_round.lock_topics()
        await self.rounds.add(publishing_round)
        for slot in AgentSlot:
            draft = self.round_service.build_draft(publishing_round, slot, self.settings.draft_token_limit)
            await self.drafts.add_if_missing(draft)
            await self.topics.add_if_missing(
                TopicRegistryEntry(
                    entry_id=draft.draft_id,
                    title=draft.topic.title,
                    keywords=draft.topic.keywords,
                    draft_id=draft.draft_id,
                    round_id=round_id,
                )
            )
        return publishing_round

    async def draft(self, slot: AgentSlot = AgentSlot.A, round_id: str = "round-1") -> PostDraft:
        draft = await self.drafts.get(f"{round_id}:{slot}")
        assert draft is not None
        return draft

    async def drive(self, slot: AgentSlot = AgentSlot.A, round_id: str = "round-1") -> PostDraft:
        await self.draft_pipeline.drive(f"{round_id}:{slot}")
        return await self.draft(slot, round_id)

    async def set_status(self, draft: PostDraft, status: PostStatus) -> None:
        """시나리오 준비용. 전이 규칙을 거치지 않고 상태만 바꿔 저장한다."""
        draft.status = status
        await self.drafts.save(draft)
