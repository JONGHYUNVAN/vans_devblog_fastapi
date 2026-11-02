"""LLM 호출 한 단계를 끝까지 책임진다: 소프트 예산 검사, 벤더 호출, 재시도, 영수증 (설계 04 문서 6절, 06 문서 3절)."""

import asyncio
import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from uuid import uuid4

from pydantic import BaseModel, ValidationError

from app.agents.contracts import (
    ArbitrateOutput,
    DraftOutput,
    ProposeTopicOutput,
    ReviewOutput,
    RevisionOutput,
    SpotCheckOutput,
)
from app.clients.llm.base import LLMMessage, LLMParams, LLMResponse
from app.clients.llm.registry import LLMClientRegistry
from app.core.config import Settings
from app.core.exceptions import BudgetExceededError, LLMOutputError, LLMRetriesExhaustedError, LLMTransportError
from app.models.agent_call import AgentCall, ChatMessage, TokenUsage
from app.models.enums import AgentSlot, CallKind
from app.models.model_binding import ModelBinding
from app.models.post_draft import PostDraft
from app.repositories.agent_call import AgentCallRepository

logger = logging.getLogger(__name__)

_BACKOFF_BASE_S = 1.0
_RATE_LIMIT_COOLDOWN_S = 30.0  # 429 뒤 그 벤더의 동시 호출을 1건으로 줄여 두는 시간 (설계 04 문서 9절)
_REVIEW_KINDS = frozenset({CallKind.REVIEW, CallKind.ARBITRATE})
_OUTPUT_SCHEMAS: dict[CallKind, type[BaseModel]] = {
    CallKind.PROPOSE_TOPIC: ProposeTopicOutput,
    CallKind.WRITE_DRAFT: DraftOutput,
    CallKind.REVISE_DRAFT: RevisionOutput,
    CallKind.ARBITRATE: ArbitrateOutput,
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
        slot: AgentSlot,
        binding: ModelBinding,
        messages: list[ChatMessage],
        schema: type[T],
        check: Callable[[T], None] | None = None,
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
        """
        client = self._llm.get(binding.provider)
        is_review = kind in _REVIEW_KINDS
        params = LLMParams(
            model_id=binding.model_id,
            temperature=self._settings.review_temperature if is_review else binding.temperature,
            max_output_tokens=binding.max_output_tokens,
        )
        timeout = float(self._settings.review_timeout_s if is_review else self._settings.draft_timeout_s)
        json_schema = schema.model_json_schema()
        attempt_messages = list(messages)
        max_attempts = self._settings.max_llm_retries
        last_error = ""

        for attempt in range(1, max_attempts + 1):
            if draft is not None:
                await self._check_budget(draft, attempt_messages, binding)

            started_at = datetime.now(UTC)
            response: LLMResponse | None = None
            parsed: T | None = None
            error: str | None = None
            try:
                async with self._llm.gate(binding.provider).slot():
                    response = await client.complete(_to_llm_messages(attempt_messages), json_schema, params, timeout)
            except LLMTransportError as exc:
                error = f"transport: {exc.message}"
                if exc.rate_limited:
                    self._llm.gate(binding.provider).throttle(_RATE_LIMIT_COOLDOWN_S)
            else:
                try:
                    parsed = schema.model_validate_json(response.text)
                    if check is not None:
                        check(parsed)
                except ValidationError as exc:
                    parsed = None  # check 가 던진 ValidationError 도 실패다
                    error = "output: " + "; ".join(
                        f"{'.'.join(str(part) for part in item['loc'])}: {item['msg']}" for item in exc.errors()
                    )
                except LLMOutputError as exc:
                    parsed = None
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
                    model_binding=binding,
                    prompt_template_version=self._settings.prompt_version,
                    target_version=target_version,
                    topic_attempt=topic_attempt,
                    item_id=item_id,
                    attempt_number=attempt,
                    messages=attempt_messages,
                    output_raw=response.text if response else None,
                    output_parsed_ok=parsed is not None,
                    error=error,
                    usage=TokenUsage(**response.usage.model_dump()) if response else TokenUsage(),
                    latency_ms=int((finished_at - started_at).total_seconds() * 1000),
                    started_at=started_at,
                    finished_at=finished_at,
                )
            )
            if parsed is not None:
                return CallResult(output=parsed, call_id=call_id)

            last_error = error or ""
            logger.warning("%s %s/%s attempt %d/%d failed: %s", kind, round_id, slot, attempt, max_attempts, last_error)
            if attempt == max_attempts:
                break
            if response is None:
                await self._sleep(_BACKOFF_BASE_S * 2 ** (attempt - 1))
            else:
                attempt_messages = [
                    *messages,
                    ChatMessage(role="assistant", content=response.text),
                    ChatMessage(role="user", content=_RETRY_GUIDANCE.format(error=last_error)),
                ]

        raise LLMRetriesExhaustedError(f"{kind} for {round_id}/{slot} failed {max_attempts} times: {last_error}")

    async def replay(self, call: AgentCall, *, binding: ModelBinding | None = None) -> ReplayResult:
        """영수증의 메시지를 그대로 다시 보낸다(프롬프트 튜닝용, 설계 07 문서 로드맵 7단계).

        영수증을 남기지 않고 재시도·예산 검사도 하지 않는다. binding 으로 다른 모델에 같은 메시지를 보낼 수 있다.
        파싱은 스키마까지만 본다(문맥 검사 check 는 원래 입력이 필요해 생략). 전송 실패는 LLMTransportError 그대로다.
        """
        binding = binding or call.model_binding
        is_review = call.kind in _REVIEW_KINDS
        params = LLMParams(
            model_id=binding.model_id,
            temperature=self._settings.review_temperature if is_review else binding.temperature,
            max_output_tokens=binding.max_output_tokens,
        )
        timeout = float(self._settings.review_timeout_s if is_review else self._settings.draft_timeout_s)
        schema = output_schema_of(call)
        started_at = datetime.now(UTC)
        async with self._llm.gate(binding.provider).slot():
            response = await self._llm.get(binding.provider).complete(
                _to_llm_messages(call.messages), schema.model_json_schema(), params, timeout
            )
        latency_ms = int((datetime.now(UTC) - started_at).total_seconds() * 1000)
        error: str | None = None
        try:
            schema.model_validate_json(response.text)
        except ValidationError as exc:
            error = "; ".join(f"{'.'.join(str(part) for part in item['loc'])}: {item['msg']}" for item in exc.errors())
        return ReplayResult(
            text=response.text,
            usage=TokenUsage(**response.usage.model_dump()),
            parsed_ok=error is None,
            error=error,
            latency_ms=latency_ms,
        )

    async def _check_budget(self, draft: PostDraft, messages: list[ChatMessage], binding: ModelBinding) -> None:
        # 추정치는 메시지 문자 수 + max_output_tokens. 폭주를 멈추는 장치라 정확할 필요가 없다 (설계 07 문서 D-33).
        used = (await self._calls.sum_usage(draft.draft_id)).total_tokens
        estimate = sum(len(message.content) for message in messages) + binding.max_output_tokens
        if used + estimate > draft.token_limit:
            raise BudgetExceededError(
                f"draft {draft.draft_id} used {used} + estimated {estimate} tokens exceeds limit {draft.token_limit}"
            )


def output_schema_of(call: AgentCall) -> type[BaseModel]:
    """영수증의 종류로 출력 계약을 고른다. REVIEW 는 입력 JSON 의 scope 로 FULL·SPOT_CHECK 를 가른다."""
    if call.kind != CallKind.REVIEW:
        return _OUTPUT_SCHEMAS[call.kind]
    user_input = next((message.content for message in call.messages if message.role == "user"), "")
    return SpotCheckOutput if '"scope": "SPOT_CHECK"' in user_input else ReviewOutput


def _to_llm_messages(messages: list[ChatMessage]) -> list[LLMMessage]:
    return [LLMMessage(role=message.role, content=message.content) for message in messages]
