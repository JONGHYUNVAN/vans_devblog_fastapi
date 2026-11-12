"""여러 테스트 파일이 쓰는 가짜 구현과 KIS 샘플 데이터."""

import asyncio
import copy
import json
from collections.abc import Callable, Iterable, Iterator
from datetime import datetime
from typing import Any

from pymongo.errors import DuplicateKeyError

from app.clients.github import read_commit_sha
from app.clients.llm.base import LLMCitation, LLMMessage, LLMParams, LLMResponse, LLMUsage
from app.clients.llm.registry import LLMClientRegistry
from app.clients.publisher.base import PostCategory, PostCreatePayload, Publisher, PublishResult
from app.core.config import Settings
from app.core.exceptions import ServiceUnavailableError
from app.models.agent_call import AgentCall, TokenUsage
from app.models.code_graph import CodeGraph
from app.models.enums import AgentSlot, PostStatus
from app.models.pipeline import Parameters
from app.models.popular_search import PopularSearch
from app.models.post import Post, PostSearchCriteria, PostSearchPage
from app.models.post_draft import PostDraft
from app.models.publishing_round import LockedTopic, PublishingRound, TopicAttempt
from app.models.topic import TopicRegistryEntry
from app.repositories.agent_call import AgentCallRepository
from app.repositories.agent_round import PublishingRoundRepository
from app.repositories.glossary import GlossaryRepository
from app.repositories.model_route import ModelRouteRepository
from app.repositories.pipeline_version import PipelineVersionRepository
from app.repositories.post_draft import PostDraftRepository
from app.repositories.post_provenance import PostProvenanceRepository
from app.repositories.topic_registry import TopicRegistryRepository
from app.services.agent_calls import AgentCallService
from app.services.derived_records import DerivedRecordsWriter
from app.services.draft_decision import DraftDecisionService
from app.services.draft_pipeline import DraftPipelineService
from app.services.glossary import GlossaryService
from app.services.pipeline_versions import PipelineVersionService
from app.services.publishing import PublishingService
from app.services.round import RoundService
from app.services.runner import PipelineRunner
from app.services.topic_selection import TopicSelectionService
from tests.factories import (
    FACT_CITATION,
    FAKE_VENDORS,
    assess_level_output,
    fact_claim,
    fact_search_text,
    fact_transform_output,
    final_read_output,
    make_call,
    make_pipeline_version,
    make_round,
    make_routing,
    make_topic,
    polish_output,
    route_documents,
)


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


class FakeGitHubClient:
    """어느 저장소든 같은 tarball 을 준다. error 가 있으면 던진다."""

    def __init__(self, tarball: bytes = b"", error: Exception | None = None) -> None:
        self.tarball = tarball
        self.error = error
        self.calls: list[tuple[str, str, str]] = []  # 전체 다운로드만 센다. commit 확인은 sha_calls 다
        self.sha_calls: list[tuple[str, str, str]] = []

    def fetch_commit_sha(self, owner: str, name: str, ref: str) -> str:
        self.sha_calls.append((owner, name, ref))
        if self.error:
            raise self.error
        return read_commit_sha(self.tarball)

    def download_tarball(self, owner: str, name: str, ref: str) -> bytes:
        self.calls.append((owner, name, ref))
        if self.error:
            raise self.error
        return self.tarball


class FakeCodeGraphRepository:
    """(repo, commit_sha) → CodeGraph. upsert_error 는 저장에서 던진다."""

    def __init__(self, code_graphs: Iterable[CodeGraph] = (), upsert_error: Exception | None = None) -> None:
        self.code_graphs = {(each.repo, each.commit_sha): each for each in code_graphs}
        self.upsert_error = upsert_error
        self.stale_calls: list[tuple[str, int]] = []

    def get_by_commit(self, repo: str, commit_sha: str) -> CodeGraph | None:
        return self.code_graphs.get((repo, commit_sha))

    def get_latest(self, repo: str) -> CodeGraph | None:
        candidates = [each for (key, _), each in self.code_graphs.items() if key == repo]
        return max(candidates, key=lambda each: each.analyzed_at, default=None)

    def upsert(self, code_graph: CodeGraph) -> None:
        if self.upsert_error:
            raise self.upsert_error
        self.code_graphs[(code_graph.repo, code_graph.commit_sha)] = code_graph

    def delete_stale(self, repo: str, keep: int) -> int:
        self.stale_calls.append((repo, keep))
        return 0


class FakeOpenApiClient:
    """URL → 문서. 없는 URL 은 ServiceUnavailableError 다. 받은 요청(url, login_url, password)을 남긴다."""

    def __init__(self, documents: dict[str, dict] | None = None) -> None:
        self.documents = documents or {}
        self.calls: list[tuple[str, str | None, str]] = []

    def fetch(self, url: str, *, login_url: str | None = None, password: str = "") -> dict:
        self.calls.append((url, login_url, password))
        if url not in self.documents:
            raise ServiceUnavailableError(f"openapi fetch failed: {url}")
        return self.documents[url]


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


def _values_at(doc: dict[str, Any], path: list[str]) -> list[Any]:
    """점 경로의 값들. 배열을 만나면 원소마다 내려가고, 끝이 배열이면 원소도 후보다 (Mongo 의 다중키 조회: lookup_keys, items.sense_key)."""
    current: list[Any] = [doc]
    for part in path:
        next_values: list[Any] = []
        for value in current:
            if isinstance(value, dict):
                if part in value:
                    next_values.append(value[part])
            elif isinstance(value, list):
                next_values += [element[part] for element in value if isinstance(element, dict) and part in element]
        current = next_values
    flattened: list[Any] = []
    for value in current:
        flattened.append(value)
        if isinstance(value, list):
            flattened += value
    return flattened


def _matches(doc: dict[str, Any], query: dict[str, Any]) -> bool:
    """리포지토리가 쓰는 조건만 지원한다: 같음, $in, $gte, $ne. 점 경로와 배열 원소 일치를 지원한다. 없는 필드는 None 이다."""
    for key, condition in query.items():
        values = _values_at(doc, key.split(".")) or [None]
        if isinstance(condition, dict):
            if "$in" in condition and not any(value in condition["$in"] for value in values):
                return False
            if "$gte" in condition and not any(value is not None and value >= condition["$gte"] for value in values):
                return False
            if "$ne" in condition and any(value == condition["$ne"] for value in values):
                return False
        elif not any(value == condition for value in values):
            return False
    return True


def _index_values(value: Any) -> set[Any]:
    """unique 인덱스가 보는 값. 배열 필드는 원소마다(다중키), 없는 필드는 비교하지 않는다."""
    if isinstance(value, list):
        return set(value)
    return set() if value is None else {value}


def _set_path(doc: dict[str, Any], path: str, value: Any) -> None:
    target = doc
    *parents, last = path.split(".")
    for part in parents:
        target = target.setdefault(part, {})
    target[last] = value


class FakeAsyncCursor:
    def __init__(self, docs: list[dict[str, Any]]) -> None:
        self._docs = docs

    def sort(self, key: str, direction: int = 1) -> "FakeAsyncCursor":
        self._docs.sort(key=lambda doc: doc.get(key), reverse=direction < 0)
        return self

    def limit(self, count: int) -> "FakeAsyncCursor":
        self._docs = self._docs[:count]
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

    def _assert_unique(self, doc: dict[str, Any], exclude: dict[str, Any] | None = None) -> None:
        """단일 키 unique 인덱스(부분 조건·다중키 포함)만 흉내 낸다: agent_rounds 의 주제 선정 하나, glossary_headwords 의 lookup_keys."""
        for keys, options in self.indexes:
            partial = options.get("partialFilterExpression")
            if not options.get("unique") or not isinstance(keys, str) or not _matches(doc, partial or {}):
                continue
            values = _index_values(doc.get(keys))
            for existing in self.docs:
                if existing is exclude or not _matches(existing, partial or {}):
                    continue
                if _index_values(existing.get(keys)) & values:
                    raise DuplicateKeyError(f"E11000 duplicate key: {options.get('name', keys)}")

    async def insert_one(self, doc: dict[str, Any]) -> None:
        if any(existing["_id"] == doc["_id"] for existing in self.docs):
            raise DuplicateKeyError(f"E11000 duplicate key: {doc['_id']}")
        self._assert_unique(doc)
        self.docs.append(copy.deepcopy(doc))

    @staticmethod
    def _apply_update(doc: dict[str, Any], update: dict[str, Any], inserting: bool = False) -> None:
        """$set(점 경로), $setOnInsert, $addToSet, $inc 만 지원한다."""
        for path, value in update.get("$set", {}).items():
            _set_path(doc, path, copy.deepcopy(value))
        if inserting:
            for path, value in update.get("$setOnInsert", {}).items():
                _set_path(doc, path, copy.deepcopy(value))
        for path, value in update.get("$addToSet", {}).items():
            items = doc.setdefault(path, [])
            for each in value["$each"] if isinstance(value, dict) and "$each" in value else [value]:
                if each not in items:
                    items.append(copy.deepcopy(each))
        for path, amount in update.get("$inc", {}).items():
            doc[path] = doc.get(path, 0) + amount

    def _upserted(self, query: dict[str, Any], update: dict[str, Any]) -> dict[str, Any]:
        doc = {key: copy.deepcopy(value) for key, value in query.items() if not isinstance(value, dict)}
        self._apply_update(doc, update, inserting=True)
        return doc

    async def find_one(self, query: dict[str, Any]) -> dict[str, Any] | None:
        return next((copy.deepcopy(doc) for doc in self.docs if _matches(doc, query)), None)

    def find(self, query: dict[str, Any], projection: dict[str, int] | None = None) -> FakeAsyncCursor:
        """projection 은 빼는 꼴({필드: 0})만 흉내 낸다. 최상위 필드를 지운 문서를 돌려준다."""
        excluded = {key for key, keep in (projection or {}).items() if not keep}
        return FakeAsyncCursor(
            [
                {key: value for key, value in copy.deepcopy(doc).items() if key not in excluded}
                for doc in self.docs
                if _matches(doc, query)
            ]
        )

    async def replace_one(
        self, query: dict[str, Any], replacement: dict[str, Any], upsert: bool = False
    ) -> _WriteResult:
        for index, doc in enumerate(self.docs):
            if _matches(doc, query):
                self._assert_unique(replacement, exclude=doc)
                self.docs[index] = copy.deepcopy(replacement)
                return _WriteResult(1)
        if upsert:
            await self.insert_one(replacement)
        return _WriteResult(0)

    async def update_one(self, query: dict[str, Any], update: dict[str, Any], upsert: bool = False) -> _WriteResult:
        for doc in self.docs:
            if _matches(doc, query):
                self._apply_update(doc, update)
                self._assert_unique(doc, exclude=doc)
                return _WriteResult(1)
        if upsert:
            await self.insert_one(self._upserted(query, update))
        return _WriteResult(0)

    async def update_many(self, query: dict[str, Any], update: dict[str, Any]) -> _WriteResult:
        matched = 0
        for doc in self.docs:
            if _matches(doc, query):
                self._apply_update(doc, update)
                matched += 1
        return _WriteResult(matched)

    async def find_one_and_update(
        self, query: dict[str, Any], update: dict[str, Any], upsert: bool = False, return_document: Any = None
    ) -> dict[str, Any] | None:
        """갱신 뒤 문서를 돌려준다 (ReturnDocument.AFTER 만 흉내 낸다. glossary_counters)."""
        for doc in self.docs:
            if _matches(doc, query):
                self._apply_update(doc, update)
                return copy.deepcopy(doc)
        if upsert:
            doc = self._upserted(query, update)
            await self.insert_one(doc)
            return copy.deepcopy(doc)
        return None

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
    def __init__(
        self, messages: list[LLMMessage], schema: dict[str, Any] | None, params: LLMParams, timeout: float
    ) -> None:
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
        self, messages: list[LLMMessage], schema: dict[str, Any] | None, params: LLMParams, timeout: float
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
    "분쟁의 근거를 찾는": "ARBITRATE_SEARCH",
    "사실 확인 담당": "FACT_CHECK",
    "JSON 으로 옮기는 변환기": "FACT_TRANSFORM",
    "마지막 독자": "FINAL_READ",
    "전문성 수준을 판정": "ASSESS_LEVEL",
    "교정자": "POLISH_TONE",
    "다듬기 제안을 검토": "POLISH_REVISE",
    "glossary extractor": "GLOSSARY",
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


def default_scripts() -> dict[str, Any]:
    """시나리오의 주제가 아닌 호출의 기본 응답: 사실 확인은 지적 없음, 다듬기는 패치 없음, 전체 읽기는 지적 없음, 수준은 200."""
    return {
        "FACT_CHECK": LLMResponse(
            text=fact_search_text(),
            usage=LLMUsage(prompt_tokens=100, completion_tokens=50),
            citations=[LLMCitation(url=FACT_CITATION, title="docs.example.com")],
        ),
        "FACT_TRANSFORM": fact_transform_output(fact_claim()),
        "POLISH_TONE": polish_output(),
        # 작성 모델이 다듬기 제안을 그대로 받는다. 고쳐 쓰거나 버리는 경우는 다듬기 테스트가 따로 준다 (D-91)
        "POLISH_REVISE": lambda call_input: polish_output(
            *[(each["index"], each["text"]) for each in call_input["proposals"]]
        ),
        "FINAL_READ": final_read_output(),
        "ASSESS_LEVEL": assess_level_output(),
    }


# 시나리오가 보는 호출. kinds 는 이 목록만 남겨 시나리오 단언이 기본 호출에 흔들리지 않게 한다
_SCENARIO_KINDS = {"PROPOSE_TOPIC", "WRITE_DRAFT", "REVISE_DRAFT", "REVIEW_FULL", "SPOT_CHECK", "ARBITRATE"}


class ScriptedLLMClient(FakeLLMClient):
    """종류별 스크립트. 값은 FakeLLMClient 와 같고, callable 이면 입력 JSON 을 받아 값을 만든다.

    종류의 스크립트가 list 면 순서대로 소비하고, callable 하나면 그 종류의 모든 호출에 쓴다. 스크립트가 없거나 모자라면 AssertionError.
    all_kinds 는 불린 호출 전부, kinds 는 그중 시나리오가 보는 호출이다.
    """

    def __init__(self, scripts: dict[str, list[ScriptOutcome] | Callable[..., Any]], delay_s: float = 0.0) -> None:
        super().__init__([], delay_s)
        self.scripts = {
            kind: (list(script) if isinstance(script, list) else script) for kind, script in scripts.items()
        }
        self.all_kinds: list[str] = []

    @property
    def kinds(self) -> list[str]:
        return [kind for kind in self.all_kinds if kind in _SCENARIO_KINDS]

    def _next_outcome(self, messages: list[LLMMessage]) -> str | LLMResponse | Exception:
        kind = call_kind_of(messages)
        self.all_kinds.append(kind)
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
        self,
        outcomes: Iterable[Any] = (),
        categories: list[PostCategory] | None = None,
        delay_s: float = 0.0,
        accounts: set[str] | None = None,
    ) -> None:
        self.outcomes = list(outcomes)
        self.accounts = accounts  # None 이면 모든 벤더에 계정이 있다
        self.providers: list[str] = []  # publish 에 넘어온 벤더
        self.categories = categories or []
        self.delay_s = delay_s
        self.payloads: list[PostCreatePayload] = []
        self.published: list[str] = []  # 돌려준 게시글 id

    async def list_categories(self) -> list[PostCategory]:
        return self.categories

    def has_account(self, provider: str) -> bool:
        return self.accounts is None or provider in self.accounts

    async def publish(self, payload: PostCreatePayload, provider: str) -> PublishResult:
        self.payloads.append(payload)
        self.providers.append(provider)
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


# 하네스 overrides 가운데 파이프라인 버전의 조절 값(Parameters)으로 가는 이름. 옛 Settings 의 평면 이름은 중첩 키로 옮긴다
_FLAT_PARAMETER_PATHS = {
    "publish_requires_approval": ("publish", "requires_approval"),
    "glossary_full_list_max": ("glossary", "full_list_max"),
    "glossary_min_terms": ("glossary", "min_terms"),
    "glossary_max_terms": ("glossary", "max_terms"),
    "glossary_surface_max_chars": ("glossary", "surface_max_chars"),
    "glossary_terminology_max": ("glossary", "terminology_max"),
}


def split_overrides(overrides: dict[str, Any]) -> tuple[dict[str, Any], dict[str, Any]]:
    """(조절 값, Settings) 로 나눈다. 조절 값의 평면 이름은 중첩 dict 로 바꾼다."""
    parameters: dict[str, Any] = {}
    settings: dict[str, Any] = {}
    for key, value in overrides.items():
        if key in _FLAT_PARAMETER_PATHS:
            group, field = _FLAT_PARAMETER_PATHS[key]
            parameters.setdefault(group, {})[field] = value
        elif key in Parameters.model_fields:
            parameters[key] = value
        else:
            settings[key] = value
    return parameters, settings


class PipelineHarness:
    """FakeAsyncDatabase 위에 실제 리포지토리·서비스를 조립한다. LLM 은 ScriptedLLMClient, Post BE 는 FakePostBE 다.

    파이프라인 버전 v1(가짜 벤더 라우트, 기본 페르소나, 스텁 프롬프트)을 넣어 둔다(with_version=False 면 넣지 않는다).
    조절 값 기본: 본문 길이 하한 1, 호출 재시도 2, 승인 토글 켬. overrides 는 Parameters 필드(평면 이름 포함)나 Settings 필드다.
    """

    def __init__(
        self,
        scripts: dict[str, Any] | None = None,
        *,
        delay_s: float = 0.0,
        database: FakeAsyncDatabase | None = None,
        round_repository: PublishingRoundRepository | None = None,
        publisher: Publisher | None = None,
        with_version: bool = True,
        **overrides: Any,
    ) -> None:
        self.database = database or FakeAsyncDatabase()
        self.llm = ScriptedLLMClient(default_scripts() | (scripts or {}), delay_s)
        parameters, settings = split_overrides(overrides)
        self.settings = Settings(_env_file=None, **settings)
        self.version = make_pipeline_version(
            **(
                {"draft_min_chars": 1, "draft_max_chars": 100_000, "draft_reject_chars": 100_000, "max_llm_retries": 2}
                | parameters
            )
        )
        self.version_repository = PipelineVersionRepository(self.database)
        if with_version:
            # __init__ 은 동기라 리포지토리 대신 가짜 컬렉션에 바로 넣는다. 테스트가 시작할 때 v1 이 최신이다
            self.database.agent_pipeline_versions.docs.append(self.version.model_dump(by_alias=True))
            self.database.agent_pipeline_counters.docs.append({"_id": "version", "seq": self.version.seq})
        self.versions = PipelineVersionService(self.version_repository, created_by="test")
        self.rounds = round_repository or PublishingRoundRepository(self.database)
        self.drafts = PostDraftRepository(self.database)
        self.topics = TopicRegistryRepository(self.database)
        self.calls = AgentCallRepository(self.database)
        self.publisher = publisher or FakePostBE()
        self.routes = ModelRouteRepository(self.database)
        self.glossary_repository = GlossaryRepository(self.database)
        self.provenance_repository = PostProvenanceRepository(self.database)
        self.derived = DerivedRecordsWriter(
            draft_repository=self.drafts,
            round_repository=self.rounds,
            agent_call_repository=self.calls,
            glossary_repository=self.glossary_repository,
            provenance_repository=self.provenance_repository,
            glossary_service=GlossaryService(repository=self.glossary_repository, settings=self.settings),
        )
        self.round_service = RoundService(
            self.rounds,
            self.drafts,
            self.topics,
            publisher=self.publisher,
            version_service=self.versions,
            route_repository=self.routes,
            providers=["fake", *FAKE_VENDORS.values()],
            derived_writer=self.derived,
        )
        self.call_service = AgentCallService(
            self.calls,
            LLMClientRegistry(
                {vendor: self.llm for vendor in ("fake", *FAKE_VENDORS.values())}, concurrency_per_provider=3
            ),
            self.settings,
            sleep=_no_sleep,
        )
        self.publishing = PublishingService(
            draft_repository=self.drafts,
            topic_registry_repository=self.topics,
            publisher=self.publisher,
            settings=self.settings,
            version_service=self.versions,
            sleep=_no_sleep,
            derived_writer=self.derived,
        )
        self.decisions = DraftDecisionService(
            draft_repository=self.drafts,
            agent_call_repository=self.calls,
            topic_registry_repository=self.topics,
            round_service=self.round_service,
            settings=self.settings,
            version_service=self.versions,
            derived_writer=self.derived,
        )
        self.glossary = GlossaryService(
            repository=self.glossary_repository,
            settings=self.settings,
            agent_call_service=self.call_service,
            draft_repository=self.drafts,
            round_repository=self.rounds,
            version_service=self.versions,
        )
        self.draft_pipeline = DraftPipelineService(
            draft_repository=self.drafts,
            round_service=self.round_service,
            agent_call_service=self.call_service,
            publisher=self.publishing,
            version_service=self.versions,
            glossary_service=self.glossary,
        )
        self.topic_selection = TopicSelectionService(
            round_repository=self.rounds,
            draft_repository=self.drafts,
            topic_registry_repository=self.topics,
            round_service=self.round_service,
            agent_call_service=self.call_service,
            version_service=self.versions,
        )
        self.runner = PipelineRunner(
            round_repository=self.rounds,
            draft_repository=self.drafts,
            round_service=self.round_service,
            topic_selection_service=self.topic_selection,
            draft_pipeline_service=self.draft_pipeline,
            version_service=self.versions,
            settings=self.settings,
        )

    async def seed_round(self, round_id: str = "round-1", *, with_glossary: bool = False) -> PublishingRound:
        """주제가 확정된 IN_PROGRESS 라운드와 TOPIC_LOCKED 글 3편, RESERVED 레지스트리 3건. with_glossary 면 GLOSSARY 라우트도.

        라운드는 하네스의 버전 v1 을 고정한다(09 문서 D-73).
        """
        await self.seed_routes(with_glossary)
        publishing_round = make_round(
            round_id, routing=make_routing(with_glossary), pipeline_version_id=self.version.version_id
        )
        attempt = TopicAttempt(attempt_number=1)
        for index, slot in enumerate(AgentSlot):
            topic = make_topic(title=f"주제 {slot}", keywords=[f"k{index}a", f"k{index}b", f"k{index}c"])
            attempt.proposals[slot] = [topic]
            attempt.locked[slot] = LockedTopic(slot=slot, source_attempt=1, proposal_index=0, proposal=topic)
        publishing_round.topic_attempts = [attempt]
        publishing_round.lock_topics()
        await self.rounds.add(publishing_round)
        for slot in AgentSlot:
            draft = self.round_service.build_draft(publishing_round, slot, self.version.parameters.draft_token_limit)
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

    async def seed_routes(self, with_glossary: bool = False) -> None:
        await self.routes.replace_all(route_documents(with_glossary))

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


class FakeCodeGraphRepoConfigRepository:
    """저장소 키 → 설정. replace_calls 는 넣은 횟수다."""

    def __init__(self, repos: dict[str, dict[str, Any]] | None = None) -> None:
        self.repos = dict(repos or {})
        self.replace_calls = 0

    def list_all(self) -> dict[str, dict[str, Any]]:
        return dict(sorted(self.repos.items()))

    def replace_all(self, repos: dict[str, dict[str, Any]]) -> None:
        self.replace_calls += 1
        self.repos = dict(repos)
