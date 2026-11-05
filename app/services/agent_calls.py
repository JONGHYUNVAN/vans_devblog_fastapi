"""LLM 호출 한 단계를 끝까지 책임진다: 소프트 예산 검사, 벤더 호출, 재시도, 영수증 (설계 04 문서 6절, 06 문서 3절)."""

import asyncio
import logging
from collections.abc import Awaitable, Callable, Iterable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from uuid import uuid4

from pydantic import BaseModel, ValidationError

from app.agents.builders import render_messages
from app.agents.contracts import (
    ArbitrateOutput,
    AssessLevelOutput,
    CitationRef,
    DraftOutput,
    FactCheckInput,
    FactCheckOutput,
    FactSpotCheckOutput,
    FactTransformInput,
    FinalReadOutput,
    PolishOutput,
    ProposeTopicOutput,
    ReviewOutput,
    RevisionOutput,
    SpotCheckOutput,
    TonePreservationRule,
    TopicForReviewer,
    WriteDraftInput,
)
from app.agents.parsers import normalize_claims
from app.clients.llm.base import LLMMessage, LLMParams, LLMResponse
from app.clients.llm.registry import LLMClientRegistry
from app.core.config import Settings
from app.core.exceptions import (
    BudgetExceededError,
    LLMOutputError,
    LLMOutputTruncatedError,
    LLMRetriesExhaustedError,
    LLMTransportError,
    NotFoundError,
)
from app.models.agent_call import AgentCall, ChatMessage, TokenUsage
from app.models.enums import AgentSlot, CallKind, ReviewLens
from app.models.model_binding import CallModel
from app.models.post_draft import FactClaim, PostDraft
from app.models.routing import raised_output_limit
from app.models.sections import find_anchors
from app.repositories.agent_call import AgentCallRepository
from app.services.metrics import VersionDensity, density_of

logger = logging.getLogger(__name__)

_BACKOFF_BASE_S = 1.0
_RATE_LIMIT_COOLDOWN_S = 30.0  # 429 뒤 그 벤더의 동시 호출을 1건으로 줄여 두는 시간 (설계 04 문서 9절)
_SEARCH_TOKEN_ESTIMATE = 30_000  # 검색 결과는 입력 토큰으로 잡히는데 메시지 문자 수에 없다. 예산 추정에 더한다
# 본문 전체를 새로 쓰지 않는 호출은 REVIEW_TIMEOUT_S, 나머지와 검색 호출은 DRAFT_TIMEOUT_S 다
_SHORT_KINDS = frozenset(
    {
        CallKind.REVIEW,
        CallKind.ARBITRATE,
        CallKind.FACT_TRANSFORM,
        CallKind.FINAL_READ,
        CallKind.ASSESS_LEVEL,
        CallKind.POLISH_FORMAT,
        CallKind.POLISH_TONE,
    }
)
_OUTPUT_SCHEMAS: dict[CallKind, type[BaseModel]] = {
    CallKind.PROPOSE_TOPIC: ProposeTopicOutput,
    CallKind.WRITE_DRAFT: DraftOutput,
    CallKind.REVISE_DRAFT: RevisionOutput,
    CallKind.ARBITRATE: ArbitrateOutput,
    CallKind.FINAL_READ: FinalReadOutput,
    CallKind.ASSESS_LEVEL: AssessLevelOutput,
    CallKind.POLISH_FORMAT: PolishOutput,
    CallKind.POLISH_TONE: PolishOutput,
}


@dataclass(frozen=True)
class ReplayResult:
    """replay 한 호출의 응답. 영수증은 남기지 않는다."""

    text: str
    usage: TokenUsage
    parsed_ok: bool
    error: str | None
    latency_ms: int


# 스키마 위반 재시도 때 첫 시도 메시지 뒤에 붙이는 안내. 직전 출력은 assistant 메시지로 그 앞에 둔다.
_RETRY_GUIDANCE = (
    "직전 응답이 출력 계약에 맞지 않았다: {error}\n같은 요청에 대해 계약에 맞는 JSON 객체 하나만 다시 출력하라."
)


@dataclass(frozen=True)
class CallResult[T: BaseModel]:
    """성공한 시도의 파싱 결과와 영수증 ID. 버전·리뷰·중재가 call_id 로 영수증을 가리킨다(설계 02 문서)."""

    output: T
    call_id: str


@dataclass(frozen=True)
class SearchResult:
    """검색 호출의 글과 실제로 쓰인 출처."""

    text: str
    call_id: str
    citations: list[CitationRef] = field(default_factory=list)


class AgentCallService:
    """한 단계의 호출을 성공한 계약 모델 하나로 끝내거나, 재시도를 소진하고 LLMRetriesExhaustedError 를 던진다.

    시도마다 AgentCall 을 한 건 insert 한다(실패 포함). 글 호출은 시도마다 소프트 예산을 검사하고, 넘으면
    호출하지 않고 BudgetExceededError 다. 시도 수는 call 의 지역 값이라 재개하면 새로 센다.
    sleep 은 백오프용이고 테스트가 바꾼다.
    """

    def __init__(
        self,
        agent_call_repository: AgentCallRepository,
        llm_registry: LLMClientRegistry,
        settings: Settings,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
    ) -> None:
        self._calls = agent_call_repository
        self._llm = llm_registry
        self._settings = settings
        self._sleep = sleep

    async def call[T: BaseModel](
        self,
        *,
        kind: CallKind,
        round_id: str,
        binding: CallModel,
        messages: list[ChatMessage],
        schema: type[T],
        check: Callable[[T], None] | None = None,
        slot: AgentSlot | None = None,
        lens: ReviewLens | None = None,
        draft: PostDraft | None = None,
        target_version: int | None = None,
        topic_attempt: int | None = None,
        item_id: str | None = None,
    ) -> CallResult[T]:
        """messages 를 binding 의 모델에 보내 schema 로 파싱한 결과와 그 시도의 call_id 를 돌려준다.

        draft 가 None 이면 주제 제안 호출이라 예산을 검사하지 않는다 (설계 02 문서 3절).
        check 는 입력 문맥이 필요한 검사(agents/parsers)다. 파싱된 모델을 받아 위반이면 LLMOutputError 를 던진다.
        네트워크·5xx·429·타임아웃(LLMTransportError)은 지수 백오프 뒤 같은 메시지로,
        계약 위반(ValidationError·LLMOutputError)은 직전 출력과 안내를 붙여 다시 부른다.
        429 는 그 벤더의 게이트도 _RATE_LIMIT_COOLDOWN_S 동안 1건씩으로 줄인다(다른 글·리뷰 코루틴도 함께 느려진다).
        출력 한도에서 잘린 응답은 계약 위반이 아니다. 같은 한도로 되풀이하지 않고 올려서 부르고, 더 못 올리면 LLMOutputTruncatedError 다.
        """

        def parse(response: LLMResponse) -> T:
            parsed = schema.model_validate_json(response.text)
            if check is not None:
                check(parsed)
            return parsed

        output, call_id = await self._attempts(
            kind=kind,
            round_id=round_id,
            slot=slot,
            lens=lens,
            binding=binding,
            messages=messages,
            json_schema=schema.model_json_schema(),
            web_search=False,
            parse=parse,
            draft=draft,
            target_version=target_version,
            topic_attempt=topic_attempt,
            item_id=item_id,
        )
        return CallResult(output=output, call_id=call_id)

    async def search(
        self,
        *,
        round_id: str,
        binding: CallModel,
        messages: list[ChatMessage],
        draft: PostDraft,
        lens: ReviewLens | None = None,
        target_version: int | None = None,
        item_id: str | None = None,
    ) -> SearchResult:
        """웹 검색을 켜고 글로 받는다. 구조화 출력과 검색을 한 호출에 묶는 것은 벤더마다 달라, 변환은 다음 호출이 맡는다."""

        def parse(response: LLMResponse) -> SearchResult:
            if not response.text.strip():
                raise LLMOutputError("search call returned an empty body")
            return SearchResult(
                text=response.text,
                call_id="",
                citations=[CitationRef(url=each.url, title=each.title) for each in response.citations],
            )

        output, call_id = await self._attempts(
            kind=CallKind.FACT_CHECK,
            round_id=round_id,
            slot=None,
            lens=lens,
            binding=binding,
            messages=messages,
            json_schema=None,
            web_search=True,
            parse=parse,
            draft=draft,
            target_version=target_version,
            topic_attempt=None,
            item_id=item_id,
        )
        return SearchResult(text=output.text, call_id=call_id, citations=output.citations)

    async def _attempts[R](
        self,
        *,
        kind: CallKind,
        round_id: str,
        slot: AgentSlot | None,
        lens: ReviewLens | None,
        binding: CallModel,
        messages: list[ChatMessage],
        json_schema: dict | None,
        web_search: bool,
        parse: Callable[[LLMResponse], R],
        draft: PostDraft | None,
        target_version: int | None,
        topic_attempt: int | None,
        item_id: str | None,
    ) -> tuple[R, str]:
        client = self._llm.get(binding.provider)
        base_timeout = float(
            self._settings.review_timeout_s if kind in _SHORT_KINDS else self._settings.draft_timeout_s
        )
        base_limit = binding.max_output_tokens
        attempt_messages = list(messages)
        max_attempts = self._settings.max_llm_retries
        last_error = ""
        label = f"{kind} for {round_id}/{slot or lens or item_id or ''}"

        for attempt in range(1, max_attempts + 1):
            if draft is not None:
                await self._check_budget(draft, attempt_messages, binding, web_search)

            # 잘려서 한도를 올렸으면 그만큼 오래 걸린다. 같은 시간을 두면 시간 초과로 끝난다
            timeout = base_timeout * binding.max_output_tokens / base_limit
            started_at = datetime.now(UTC)
            response: LLMResponse | None = None
            parsed: R | None = None
            error: str | None = None
            try:
                async with self._llm.gate(binding.provider).slot():
                    response = await client.complete(
                        _to_llm_messages(attempt_messages), json_schema, _params(binding, web_search), timeout
                    )
            except LLMTransportError as exc:
                error = f"transport: {exc.message}"
                if exc.rate_limited:
                    self._llm.gate(binding.provider).throttle(_RATE_LIMIT_COOLDOWN_S)
            else:
                if response.truncated:
                    error = f"truncated: {response.stop_reason} at max_output_tokens={binding.max_output_tokens}"
                else:
                    try:
                        parsed = parse(response)
                    except ValidationError as exc:
                        error = "output: " + "; ".join(
                            f"{'.'.join(str(part) for part in item['loc'])}: {item['msg']}" for item in exc.errors()
                        )
                    except LLMOutputError as exc:
                        error = f"output: {exc.message}"
            finished_at = datetime.now(UTC)

            call_id = uuid4().hex
            await self._calls.add(
                AgentCall(
                    call_id=call_id,
                    round_id=round_id,
                    draft_id=draft.draft_id if draft else None,
                    kind=kind,
                    slot=slot,
                    lens=lens,
                    model_binding=binding,
                    prompt_template_version=self._settings.prompt_version,
                    target_version=target_version,
                    topic_attempt=topic_attempt,
                    item_id=item_id,
                    attempt_number=attempt,
                    messages=attempt_messages,
                    output_raw=response.text if response else None,
                    output_parsed_ok=parsed is not None,
                    stop_reason=response.stop_reason if response else "",
                    citations=[each.url for each in response.citations] if response else [],
                    error=error,
                    usage=TokenUsage(**response.usage.model_dump()) if response else TokenUsage(),
                    latency_ms=int((finished_at - started_at).total_seconds() * 1000),
                    started_at=started_at,
                    finished_at=finished_at,
                )
            )
            if parsed is not None:
                return parsed, call_id

            last_error = error or ""
            logger.warning("%s attempt %d/%d failed: %s", label, attempt, max_attempts, last_error)
            if response is not None and response.truncated:
                raised = raised_output_limit(binding)
                if raised is None or attempt == max_attempts:
                    raise LLMOutputTruncatedError(f"{label} was truncated: {last_error}")
                binding = binding.model_copy(update={"max_output_tokens": raised})
                continue
            if attempt == max_attempts:
                break
            if response is None:
                await self._sleep(_BACKOFF_BASE_S * 2 ** (attempt - 1))
            elif response.text:
                attempt_messages = [
                    *messages,
                    ChatMessage(role="assistant", content=response.text),
                    ChatMessage(role="user", content=_RETRY_GUIDANCE.format(error=last_error)),
                ]
            # 본문이 빈 응답은 빈 assistant 메시지를 붙일 수 없어(벤더가 400 으로 거부한다) 같은 메시지로 다시 부른다

        raise LLMRetriesExhaustedError(f"{label} failed {max_attempts} times: {last_error}")

    async def get(self, call_id: str) -> AgentCall:
        """영수증 하나. 없으면 NotFoundError. replay 의 입력이다."""
        call = await self._calls.get(call_id)
        if call is None:
            raise NotFoundError(f"call {call_id} does not exist")
        return call

    async def replay(
        self, call: AgentCall, *, binding: CallModel | None = None, messages: list[ChatMessage] | None = None
    ) -> ReplayResult:
        """영수증의 메시지를 그대로 다시 보낸다(프롬프트 튜닝용, 설계 07 문서 로드맵 7단계).

        영수증을 남기지 않고 재시도·예산 검사도 하지 않는다. binding 으로 다른 모델에, messages 로 다른 프롬프트를 보낼 수 있다.
        파싱은 스키마까지만 본다(문맥 검사 check 는 원래 입력이 필요해 생략). 전송 실패는 LLMTransportError 그대로다.
        """
        binding = binding or call.model_binding
        timeout = float(
            self._settings.review_timeout_s if call.kind in _SHORT_KINDS else self._settings.draft_timeout_s
        )
        schema = output_schema_of(call)
        web_search = call.kind == CallKind.FACT_CHECK
        started_at = datetime.now(UTC)
        async with self._llm.gate(binding.provider).slot():
            response = await self._llm.get(binding.provider).complete(
                _to_llm_messages(messages if messages is not None else call.messages),
                schema.model_json_schema() if schema else None,
                _params(binding, web_search),
                timeout,
            )
        latency_ms = int((datetime.now(UTC) - started_at).total_seconds() * 1000)
        error: str | None = None
        if schema is not None:
            try:
                schema.model_validate_json(response.text)
            except ValidationError as exc:
                error = "; ".join(
                    f"{'.'.join(str(part) for part in item['loc'])}: {item['msg']}" for item in exc.errors()
                )
        return ReplayResult(
            text=response.text,
            usage=TokenUsage(**response.usage.model_dump()),
            parsed_ok=error is None,
            error=error,
            latency_ms=latency_ms,
        )

    async def probe_fact_check(
        self, content_markdown: str, *, binding: CallModel, transformer: CallModel, prompt_version: str
    ) -> tuple[list[FactClaim], list[CitationRef]]:
        """글 하나를 사실 확인해 주장 목록을 돌려준다. 잘못된 API 를 심은 글로 모델별 재현율·오탐을 잴 때 쓴다.

        영수증·상태를 남기지 않는다. 재시도하지 않으며 전송 실패는 LLMTransportError 그대로다.
        """
        topic = TopicForReviewer(
            title="",
            audience="",
            keywords=[],
            outline=[],
            core_mechanism="",
            out_of_scope=[],
            target_level=None,
            prerequisites="",
        )
        fact_input = FactCheckInput(
            scope="FULL",
            lens=ReviewLens.FACT_STRUCTURE,
            topic=topic,
            content_markdown=content_markdown,
            changed_section_ids=[],
            my_previous_items=[],
            resolutions=[],
            link_status=[],
            tone_preservation=TonePreservationRule(author_tone_markers=[]),
        )
        async with self._llm.gate(binding.provider).slot():
            searched = await self._llm.get(binding.provider).complete(
                _to_llm_messages(render_messages(fact_input, prompt_version)),
                None,
                _params(binding, True),
                float(self._settings.draft_timeout_s),
            )
        citations = [CitationRef(url=each.url, title=each.title) for each in searched.citations]
        transform_input = FactTransformInput(
            scope="FULL",
            lens=ReviewLens.FACT_STRUCTURE,
            section_ids=find_anchors(content_markdown),
            expected_item_ids=[],
            verification_text=searched.text,
            citations=citations,
        )
        async with self._llm.gate(transformer.provider).slot():
            transformed = await self._llm.get(transformer.provider).complete(
                _to_llm_messages(render_messages(transform_input, prompt_version)),
                FactCheckOutput.model_json_schema(),
                _params(transformer, False),
                float(self._settings.review_timeout_s),
            )
        claims = FactCheckOutput.model_validate_json(transformed.text).claims
        return normalize_claims(claims, citations), citations

    async def compare_write_prompts(
        self,
        call: AgentCall,
        *,
        versions: Iterable[str],
        runs: int,
        core_mechanism: str,
        out_of_scope: list[str],
    ) -> list[tuple[str, int, VersionDensity | None, str | None]]:
        """저장된 WRITE_DRAFT 입력을 여러 프롬프트 버전으로 다시 써 밀도를 잰다. 같은 주제를 새 라운드로 돌리면 중복 검사에 걸린다.

        옛 입력에는 범위 필드가 없어 호출자가 채운다. 영수증·상태를 바꾸지 않는다. 행은 (버전, 회차, 밀도, 오류)다.
        """
        if call.kind != CallKind.WRITE_DRAFT:
            raise ValueError(f"call {call.call_id} is {call.kind}, not WRITE_DRAFT")
        user_message = next(message.content for message in call.messages if message.role == "user")
        write_input = WriteDraftInput.model_validate_json(user_message.removeprefix("입력(JSON):\n"))
        write_input.topic = write_input.topic.model_copy(
            update={"core_mechanism": core_mechanism, "out_of_scope": out_of_scope}
        )
        rows = []
        for version in versions:
            messages = render_messages(write_input, version)
            for run in range(1, runs + 1):
                result = await self.replay(call, messages=messages)
                if result.error is not None:
                    rows.append((version, run, None, result.error))
                    continue
                rows.append(
                    (version, run, density_of(DraftOutput.model_validate_json(result.text).content_markdown), None)
                )
        return rows

    async def _check_budget(
        self, draft: PostDraft, messages: list[ChatMessage], binding: CallModel, web_search: bool
    ) -> None:
        # 추정치는 메시지 문자 수 + max_output_tokens. 폭주를 멈추는 장치라 정확할 필요가 없다 (설계 07 문서 D-33).
        used = (await self._calls.sum_usage(draft.draft_id)).total_tokens
        estimate = sum(len(message.content) for message in messages) + binding.max_output_tokens
        if web_search:
            estimate += _SEARCH_TOKEN_ESTIMATE
        if used + estimate > draft.token_limit:
            raise BudgetExceededError(
                f"draft {draft.draft_id} used {used} + estimated {estimate} tokens exceeds limit {draft.token_limit}"
            )


def output_schema_of(call: AgentCall) -> type[BaseModel] | None:
    """영수증의 종류로 출력 계약을 고른다. 검색 호출은 글이라 None 이다. REVIEW·변환은 입력 JSON 의 scope 로 FULL·SPOT_CHECK 를 가른다."""
    if call.kind == CallKind.FACT_CHECK:
        return None
    if call.kind not in (CallKind.REVIEW, CallKind.FACT_TRANSFORM):
        return _OUTPUT_SCHEMAS[call.kind]
    user_input = next((message.content for message in call.messages if message.role == "user"), "")
    is_spot_check = '"scope": "SPOT_CHECK"' in user_input
    if call.kind == CallKind.FACT_TRANSFORM:
        return FactSpotCheckOutput if is_spot_check else FactCheckOutput
    return SpotCheckOutput if is_spot_check else ReviewOutput


def _params(binding: CallModel, web_search: bool) -> LLMParams:
    return LLMParams(
        model_id=binding.model_id,
        temperature=binding.temperature,
        effort=binding.effort,
        max_output_tokens=binding.max_output_tokens,
        web_search=web_search,
    )


def _to_llm_messages(messages: list[ChatMessage]) -> list[LLMMessage]:
    return [LLMMessage(role=message.role, content=message.content) for message in messages]
