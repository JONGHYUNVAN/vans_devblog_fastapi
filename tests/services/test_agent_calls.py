"""AgentCallService 테스트. 재시도 메시지 구성, 백오프, 소진, 소프트 예산, 영수증을 확인한다 (설계 06 문서 6절)."""

import asyncio
import json

import pytest
from pydantic import BaseModel

from app.agents.builders import render_messages
from app.agents.contracts import FactSpotCheckOutput, FinalReadOutput, StructureRules, WriteDraftInput
from app.agents.personas import get_persona
from app.clients.llm.base import LLMCitation, LLMResponse, LLMUsage
from app.clients.llm.registry import LLMClientRegistry
from app.core.config import Settings
from app.core.exceptions import BudgetExceededError, LLMOutputError, LLMRetriesExhaustedError, LLMTransportError
from app.models.agent_call import AgentCall, ChatMessage
from app.models.enums import AgentSlot, CallKind
from app.services.agent_calls import AgentCallService, output_schema_of
from tests.factories import draft_output, make_call, make_call_model, make_draft, make_topic
from tests.fakes import FakeAgentCallRepository, FakeLLMClient

pytestmark = pytest.mark.anyio

MESSAGES = [ChatMessage(role="system", content="너는 작성자다"), ChatMessage(role="user", content="글을 써라")]
GOOD = '{"title": "제목", "score": 3}'


class Answer(BaseModel):
    title: str
    score: int


class Harness:
    def __init__(self, outcomes, *, max_llm_retries: int = 3, concurrency: int = 2) -> None:
        self.llm = FakeLLMClient(outcomes)
        self.calls = FakeAgentCallRepository()
        self.sleeps: list[float] = []
        self.settings = Settings(
            _env_file=None,
            max_llm_retries=max_llm_retries,
            review_timeout_s=300,
            draft_timeout_s=600,
            prompt_version="v1",
        )

        async def sleep(seconds: float) -> None:
            self.sleeps.append(seconds)

        self.registry = LLMClientRegistry({"fake": self.llm}, concurrency_per_provider=concurrency)
        self.service = AgentCallService(
            agent_call_repository=self.calls,
            llm_registry=self.registry,
            settings=self.settings,
            sleep=sleep,
        )

    async def call(self, kind: CallKind = CallKind.WRITE_DRAFT, draft=None, **overrides) -> Answer:
        result = await self.service.call(
            kind=kind,
            round_id="round-1",
            slot=AgentSlot.A,
            binding=make_call_model(),
            messages=MESSAGES,
            schema=Answer,
            draft=draft,
            **overrides,
        )
        return result.output


async def test_call_returns_parsed_model_and_records_receipt():
    harness = Harness([GOOD])
    draft = make_draft()

    answer = await harness.call(draft=draft, target_version=1)

    assert answer == Answer(title="제목", score=3)
    [receipt] = harness.calls.calls
    assert (receipt.kind, receipt.slot, receipt.draft_id, receipt.round_id) == (
        "WRITE_DRAFT",
        "A",
        draft.draft_id,
        "round-1",
    )
    assert (receipt.attempt_number, receipt.target_version, receipt.output_parsed_ok, receipt.error) == (
        1,
        1,
        True,
        None,
    )
    assert (receipt.output_raw, receipt.usage.prompt_tokens, receipt.usage.completion_tokens) == (GOOD, 100, 50)
    assert receipt.messages == MESSAGES
    assert receipt.prompt_template_version == "v1"
    assert receipt.model_binding == make_call_model()
    [sent] = harness.llm.calls
    assert [m.content for m in sent.messages] == [m.content for m in MESSAGES]
    assert sent.schema == Answer.model_json_schema()


async def test_schema_violation_retries_with_previous_output_and_guidance():
    harness = Harness(['{"title": "제목"}', GOOD])

    answer = await harness.call()

    assert answer.score == 3
    first, second = harness.calls.calls
    assert (first.output_parsed_ok, second.output_parsed_ok) == (False, True)
    assert first.error.startswith("output:") and "score" in first.error
    assert (first.attempt_number, second.attempt_number) == (1, 2)
    retry_messages = harness.llm.calls[1].messages
    assert [m.role for m in retry_messages] == ["system", "user", "assistant", "user"]
    assert retry_messages[2].content == '{"title": "제목"}'
    assert first.error in retry_messages[3].content
    assert second.messages[-1].content == retry_messages[3].content  # 영수증에는 실제로 보낸 메시지가 남는다
    assert harness.sleeps == []


async def test_transport_error_backs_off_and_resends_same_messages():
    harness = Harness([LLMTransportError("503"), LLMTransportError("timeout"), GOOD])

    await harness.call()

    assert harness.sleeps == [1.0, 2.0]
    assert [c.output_parsed_ok for c in harness.calls.calls] == [False, False, True]
    assert harness.calls.calls[0].error == "transport: 503"
    assert harness.calls.calls[0].output_raw is None
    assert harness.calls.calls[0].usage.total_tokens == 0
    assert all([m.content for m in c.messages] == [m.content for m in MESSAGES] for c in harness.llm.calls)


async def test_exhausting_retries_raises_after_max_attempts_without_final_sleep():
    harness = Harness([LLMTransportError("down")] * 5, max_llm_retries=3)

    with pytest.raises(LLMRetriesExhaustedError, match="failed 3 times"):
        await harness.call()

    assert len(harness.calls.calls) == 3
    assert len(harness.llm.calls) == 3
    assert harness.sleeps == [1.0, 2.0]


async def test_budget_exceeded_before_call_leaves_no_receipt():
    harness = Harness([GOOD])
    draft = make_draft(token_limit=1_000)
    harness.calls.preload("round-1:A", prompt_tokens=900, completion_tokens=50)

    with pytest.raises(BudgetExceededError):
        await harness.call(draft=draft)

    assert harness.llm.calls == []
    assert len(harness.calls.calls) == 1  # preload 만 남아 있다


async def test_budget_is_checked_on_every_attempt():
    # 첫 시도 응답의 usage 가 합산돼 두 번째 시도 직전에 상한을 넘는다
    harness = Harness(['{"title": "제목"}', GOOD])
    draft = make_draft(token_limit=1_200)  # 추정치 = 메시지 문자 수 + 1000 < 1200, 첫 응답 150 토큰 뒤에는 초과

    with pytest.raises(BudgetExceededError):
        await harness.call(draft=draft)

    assert len(harness.llm.calls) == 1


async def test_topic_proposal_skips_budget_check():
    harness = Harness([GOOD])
    harness.calls.preload("round-1:A", prompt_tokens=10**9)

    answer = await harness.call(kind=CallKind.PROPOSE_TOPIC, topic_attempt=1)

    assert answer.title == "제목"
    assert harness.calls.calls[-1].draft_id is None
    assert harness.calls.calls[-1].topic_attempt == 1


@pytest.mark.parametrize(
    ("kind", "timeout"),
    [
        (CallKind.WRITE_DRAFT, 600.0),
        (CallKind.REVISE_DRAFT, 600.0),
        (CallKind.PROPOSE_TOPIC, 600.0),
        (CallKind.REVIEW, 300.0),
        (CallKind.ARBITRATE, 300.0),
        (CallKind.FINAL_READ, 300.0),
        (CallKind.POLISH_TONE, 300.0),
    ],
)
async def test_call_kind_picks_the_timeout(kind, timeout):
    harness = Harness([GOOD])

    await harness.call(kind=kind)

    [sent] = harness.llm.calls
    assert (sent.params.max_output_tokens, sent.timeout) == (1000, timeout)


async def test_timeout_grows_with_the_raised_output_limit():
    truncated = LLMResponse(text="{", usage=LLMUsage(), stop_reason="max_tokens", truncated=True)
    harness = Harness([truncated, GOOD])

    await harness.call(kind=CallKind.REVIEW)

    first, second = harness.llm.calls
    assert (first.params.max_output_tokens, first.timeout) == (1000, 300.0)
    assert (second.params.max_output_tokens, second.timeout) == (2000, 600.0)


async def test_temperature_and_effort_come_only_from_the_route_candidate():
    harness = Harness([GOOD, GOOD])

    await harness.call()
    await harness.service.call(
        kind=CallKind.REVIEW,
        round_id="round-1",
        binding=make_call_model(temperature=0.3, effort="high"),
        messages=MESSAGES,
        schema=Answer,
    )

    first, second = harness.llm.calls
    assert (first.params.temperature, first.params.effort) == (None, None)
    assert (second.params.temperature, second.params.effort) == (0.3, "high")


async def test_empty_body_retries_without_an_empty_assistant_message():
    # 빈 assistant 메시지는 벤더가 400 으로 거부한다. 거부(refusal)·생각만 한 응답에서 생긴다
    harness = Harness([LLMResponse(text="", usage=LLMUsage(), stop_reason="refusal"), GOOD])

    await harness.call()

    assert [m.role for m in harness.llm.calls[1].messages] == ["system", "user"]
    assert harness.calls.calls[0].stop_reason == "refusal"


async def test_search_returns_text_and_citations_and_records_them():
    response = LLMResponse(
        text="확인 결과",
        usage=LLMUsage(prompt_tokens=1, completion_tokens=1),
        citations=[LLMCitation(url="https://docs.test/a", title="A")],
    )
    harness = Harness([response])

    result = await harness.service.search(
        round_id="round-1", binding=make_call_model(), messages=MESSAGES, draft=make_draft()
    )

    assert result.text == "확인 결과"
    assert [citation.url for citation in result.citations] == ["https://docs.test/a"]
    [sent] = harness.llm.calls
    assert (sent.params.web_search, sent.schema, sent.timeout) == (True, None, 600.0)
    [receipt] = harness.calls.calls
    assert (receipt.kind, receipt.citations, result.call_id) == (
        CallKind.FACT_CHECK,
        ["https://docs.test/a"],
        receipt.call_id,
    )


async def test_search_budget_estimate_counts_search_results():
    harness = Harness([GOOD])

    with pytest.raises(BudgetExceededError):
        await harness.service.search(
            round_id="round-1", binding=make_call_model(), messages=MESSAGES, draft=make_draft(token_limit=20_000)
        )
    assert harness.llm.calls == []


async def test_old_receipt_with_persona_id_still_loads_and_replays():
    harness = Harness([GOOD])
    stored = make_call().model_dump(by_alias=True)
    stored["model_binding"] = {
        "provider": "fake",
        "model_id": "old",
        "persona_id": "architect",
        "temperature": 0.7,
        "max_output_tokens": 500,
    }
    stored["slot"] = "A"

    call = AgentCall.model_validate(stored)

    assert call.model_binding.model_id == "old"
    assert (await harness.service.replay(call)).text == GOOD


async def test_compare_write_prompts_rebuilds_the_input_per_version_and_measures_density():
    harness = Harness([draft_output(), draft_output()])
    write_input = WriteDraftInput(
        persona=get_persona("architect"),
        topic=make_topic(core_mechanism="", out_of_scope=[]),
        structure_rules=StructureRules(min_chars=1, max_chars=100),
    )
    call = make_call()
    call.messages = render_messages(write_input, "v2")

    rows = await harness.service.compare_write_prompts(
        call, versions=["v2", "v3"], runs=1, core_mechanism="메커니즘", out_of_scope=["범위 밖"]
    )

    assert [(row[0], row[1], row[3]) for row in rows] == [("v2", 1, None), ("v3", 1, None)]
    assert rows[0][2] is not None and rows[0][2].paragraphs == 4
    sent_v3 = harness.llm.calls[1].messages
    assert "topic.core_mechanism" in sent_v3[0].content and '"core_mechanism": "메커니즘"' in sent_v3[1].content
    assert len(harness.calls.calls) == 0


async def test_probe_fact_check_searches_transforms_and_checks_urls_without_receipts():
    claim = {
        "section_id": "body-1",
        "claim_type": "API",
        "claim": "c",
        "status": "SUPPORTED",
        "source_url": "https://made-up.test/x",
        "note": "",
    }
    transformed = {
        "verdict": "APPROVE",
        "items": [],
        "checklist": {"checked_claims": ["c"], "checked_code_blocks": [], "unverifiable": []},
        "overall_feedback": "",
        "claims": [claim],
    }
    searched = LLMResponse(
        text="확인 결과", usage=LLMUsage(), citations=[LLMCitation(url="https://docs.test/a", title="A")]
    )
    harness = Harness([searched, json.dumps(transformed)])

    claims, citations = await harness.service.probe_fact_check(
        "<!-- sec:body-1 -->\n본문",
        binding=make_call_model(effort="high"),
        transformer=make_call_model(),
        prompt_version="v3",
    )

    assert [(each.status, each.source_url) for each in claims] == [("UNVERIFIABLE", None)]
    assert [each.url for each in citations] == ["https://docs.test/a"]
    search, transform = harness.llm.calls
    assert (search.params.web_search, search.schema, transform.params.web_search) == (True, None, False)
    assert harness.calls.calls == []


def test_output_schema_of_search_and_transform_receipts():
    search = make_call(kind=CallKind.FACT_CHECK)
    transform = make_call(kind=CallKind.FACT_TRANSFORM)
    transform.messages = [ChatMessage(role="user", content='입력(JSON):\n{\n  "scope": "SPOT_CHECK"\n}')]

    assert output_schema_of(search) is None
    assert output_schema_of(transform) is FactSpotCheckOutput
    assert output_schema_of(make_call(kind=CallKind.FINAL_READ)) is FinalReadOutput


async def test_usage_from_vendor_is_recorded_per_attempt():
    harness = Harness([LLMResponse(text=GOOD, usage=LLMUsage(prompt_tokens=7, completion_tokens=3))])

    await harness.call()

    assert harness.calls.calls[0].usage.total_tokens == 10


async def test_calls_to_one_provider_are_limited_by_semaphore():
    harness = Harness([GOOD] * 4, concurrency=2)
    harness.llm.delay_s = 0.01

    await asyncio.gather(*(harness.call() for _ in range(4)))

    assert harness.llm.max_active == 2


async def test_rate_limit_throttles_the_provider_gate_but_other_transport_errors_do_not():
    harness = Harness([LLMTransportError("503"), GOOD, LLMTransportError("429", rate_limited=True), GOOD])

    await harness.call()
    assert not harness.registry.gate("fake").throttled

    await harness.call()

    assert harness.registry.gate("fake").throttled  # 04 문서 9절: 429 는 벤더 세마포어도 일시 축소
    assert harness.sleeps == [1.0, 1.0]


async def test_check_hook_failure_retries_like_a_schema_violation():
    harness = Harness([GOOD, GOOD])
    seen: list[Answer] = []

    def check(answer: Answer) -> None:
        seen.append(answer)
        if len(seen) == 1:
            raise LLMOutputError("score must be 5")

    result = await harness.service.call(
        kind=CallKind.WRITE_DRAFT,
        round_id="round-1",
        slot=AgentSlot.A,
        binding=make_call_model(),
        messages=MESSAGES,
        schema=Answer,
        check=check,
    )

    assert result.output.score == 3 and len(seen) == 2
    first, second = harness.calls.calls
    assert result.call_id == second.call_id
    assert (first.output_parsed_ok, first.error) == (False, "output: score must be 5")
    assert second.output_parsed_ok is True
    assert "score must be 5" in harness.llm.calls[1].messages[-1].content


async def test_check_hook_raising_validation_error_is_a_schema_violation_too():
    harness = Harness([GOOD, GOOD])
    seen: list[Answer] = []

    def check(answer: Answer) -> None:
        seen.append(answer)
        if len(seen) == 1:
            Answer.model_validate({"title": "x", "score": "not a number"})  # pydantic ValidationError

    result = await harness.service.call(
        kind=CallKind.WRITE_DRAFT,
        round_id="round-1",
        slot=AgentSlot.A,
        binding=make_call_model(),
        messages=MESSAGES,
        schema=Answer,
        check=check,
    )

    first, second = harness.calls.calls
    assert result.call_id == second.call_id and len(seen) == 2
    assert first.output_parsed_ok is False and first.error is not None and first.error.startswith("output: score")


# ---------- replay (로드맵 7단계) ----------


async def test_replay_resends_the_receipt_messages_without_a_new_receipt():
    harness = Harness([GOOD, '{"title": "다시"}'])
    await harness.service.call(
        kind=CallKind.WRITE_DRAFT,
        round_id="round-1",
        slot=AgentSlot.A,
        binding=make_call_model(),
        messages=MESSAGES,
        schema=Answer,
        draft=make_draft(),
    )
    [call] = harness.calls.calls
    other = make_call_model(provider="fake", model_id="fake-2", temperature=0.1)

    result = await harness.service.replay(call, binding=other)

    assert (result.parsed_ok, result.text) == (False, '{"title": "다시"}')  # DraftOutput 스키마로 검사한다
    assert result.error and "content_markdown" in result.error
    assert len(harness.calls.calls) == 1
    replayed = harness.llm.calls[-1]
    assert [message.content for message in replayed.messages] == [message.content for message in MESSAGES]
    assert (replayed.params.model_id, replayed.params.temperature) == ("fake-2", 0.1)


def test_output_schema_of_review_receipts_depends_on_scope():
    from app.agents.contracts import ReviewOutput, SpotCheckOutput

    full = make_call(kind=CallKind.REVIEW)
    full.messages = [ChatMessage(role="user", content='입력(JSON):\n{\n  "scope": "FULL"\n}')]
    spot = make_call(kind=CallKind.REVIEW)
    spot.messages = [ChatMessage(role="user", content='입력(JSON):\n{\n  "scope": "SPOT_CHECK"\n}')]

    assert (output_schema_of(full), output_schema_of(spot)) == (ReviewOutput, SpotCheckOutput)
