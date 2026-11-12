"""설명문 파이프라인 러너: 가짜 LLM 으로 1~9단계, 버전·영수증, 이어 하기, 검사 실패 재시도, 고칠 수 없는 사실 오류."""

import json

import pytest

from app.agents.expository import STAGE_KEYS
from app.clients.llm.base import LLMClient
from app.clients.llm.registry import LLMClientRegistry
from app.clients.publisher.base import PostCategory
from app.core.config import Settings
from app.core.exceptions import (
    InvalidRequestError,
    LLMOutputError,
    LLMRetriesExhaustedError,
    NotFoundError,
    PublishFailedError,
    PublishUncertainError,
)
from app.models.pipeline import PipelineVersionBody
from app.repositories.expository_run import ExpositoryRunRepository
from app.repositories.pipeline_version import PipelineVersionRepository
from app.services.agent_calls import AgentCallService
from app.services.expository import TOPIC_KEYS, ExpositoryPipeline, RunSeed
from app.services.pipeline_versions import PipelineVersionService
from tests.factories import (
    make_expository_body,
    make_expository_english_article,
    make_expository_korean_article,
    make_expository_outline,
    make_expository_summaries,
    make_expository_terms,
    make_expository_topic,
    make_pipeline_body,
    make_pipeline_version,
)
from tests.fakes import FakeAgentCallRepository, FakeAsyncDatabase, FakeLLMClient, FakePostBE

pytestmark = pytest.mark.anyio

KOREAN_TITLE = "셀프 호출이 트랜잭션을 건너뛰는 이유"


RUN_ID = "expository-test"


async def _no_sleep(seconds: float) -> None:
    return None


class Harness:
    """버전 v1(저장소 pipeline/expository 의 프롬프트·라우트) 하나가 든 가짜 Mongo. 러너를 새로 만들어도 실행 문서는 남는다."""

    def __init__(self, body: PipelineVersionBody | None = None) -> None:
        self.database = FakeAsyncDatabase()
        self.receipts = FakeAgentCallRepository()
        self.runs = ExpositoryRunRepository(self.database)
        self._body = body or make_expository_body(draft_min_chars=10)
        self._seeded = False

    async def pipeline(self, client: LLMClient, publisher: FakePostBE | None = None) -> ExpositoryPipeline:
        versions = PipelineVersionRepository(self.database)
        if not self._seeded:
            await versions.add(make_pipeline_version(body=self._body))
            self._seeded = True
        # 키가 anthropic 하나뿐이라 사실 확인 라우트의 google 첫 후보도 anthropic 으로 내려간다
        registry = LLMClientRegistry({"anthropic": client}, concurrency_per_provider=1)
        return ExpositoryPipeline(
            calls=AgentCallService(self.receipts, registry, Settings(_env_file=None), sleep=_no_sleep),
            versions=PipelineVersionService(versions),
            providers=["anthropic"],
            repository=self.runs,
            publisher=publisher,
            main_categories=["backend"],
        )


def _full_script() -> list[str]:
    topic = make_expository_topic()
    outline = make_expository_outline(topic["core_argument"])
    patch = {"section_id": "concept", "before": "The concept part", "after": "The concept section", "reason": "r"}
    outputs = [
        topic,
        outline,
        {"outline": outline, "review_log": []},
        {"outline": outline, "claims": []},
        {"title": "Self-invocation and transactions", "content_markdown": make_expository_english_article()},
        {"claims": [], "patches": [patch]},
        {"section_summaries": make_expository_summaries(), "terms": make_expository_terms()},
        {"title": KOREAN_TITLE, "content_markdown": make_expository_korean_article()},
        {"summary": "같은 객체 안의 호출은 프록시를 거치지 않아 트랜잭션 설정이 적용되지 않습니다."},
    ]
    return [json.dumps(output, ensure_ascii=False) for output in outputs]


async def test_run_completes_every_stage_and_stores_the_article_and_receipts():
    harness = Harness()
    client = FakeLLMClient(_full_script())

    result = await (await harness.pipeline(client)).run(RUN_ID, RunSeed())

    assert (result.title, result.pipeline_version) == (KOREAN_TITLE, "v1")
    assert result.finished_stages == list(STAGE_KEYS)
    assert result.markdown is not None and result.markdown.startswith(f"# {KOREAN_TITLE}\n\n> 같은 객체")
    stored = await harness.runs.get(RUN_ID)
    assert stored is not None and stored.article is not None
    assert stored.article.keywords == ["spring", "transaction", "proxy"]
    assert "The concept section" in (stored.checked_markdown or "")
    assert "**📊 Overview**" in (stored.assembled_markdown or "")
    # 단계마다 영수증 한 건: 실행 id, 단계 key, 프롬프트를 읽은 버전
    assert [receipt.item_id for receipt in harness.receipts.calls] == list(STAGE_KEYS)
    assert {(receipt.kind, receipt.round_id, receipt.pipeline_version) for receipt in harness.receipts.calls} == {
        ("EXPOSITORY", RUN_ID, "v1")
    }
    assert harness.receipts.calls[3].model_binding.model_id == "claude-opus-5-5"
    searches = [call.params.web_search for call in client.calls]
    assert searches == [False, False, False, True, False, True, False, False, False]


async def test_run_skips_stages_whose_prompt_did_not_change():
    harness = Harness()
    await (await harness.pipeline(FakeLLMClient(_full_script()))).run(RUN_ID, RunSeed())
    again = FakeLLMClient([])  # 부르면 AssertionError

    result = await (await harness.pipeline(again)).run(RUN_ID, RunSeed())

    assert again.calls == []
    assert result.title == KOREAN_TITLE


async def test_run_redo_from_calls_that_stage_and_the_ones_after_it_again():
    harness = Harness()
    await (await harness.pipeline(FakeLLMClient(_full_script()))).run(RUN_ID, RunSeed())
    again = FakeLLMClient(_full_script()[7:])

    result = await (await harness.pipeline(again)).run(RUN_ID, RunSeed(), redo_from=8)

    assert len(again.calls) == 2
    assert result.finished_stages == list(STAGE_KEYS)


async def test_run_sends_check_errors_back_and_retries():
    harness = Harness()
    topic = make_expository_topic()
    wrong = make_expository_outline("A different thesis.")
    right = make_expository_outline(topic["core_argument"])
    client = FakeLLMClient([json.dumps(wrong), json.dumps(right)])

    result = await (await harness.pipeline(client)).run(RUN_ID, RunSeed(topic=topic), stop_after=2)

    retry = client.calls[1].messages
    assert [message.role for message in retry] == ["system", "user", "assistant", "user"]
    assert "core_message must be an exact" in retry[-1].content
    assert result.finished_stages == ["1_topic_thesis", "2_outline"] and result.markdown is None
    stored = await harness.runs.get(RUN_ID)
    assert stored is not None and stored.stage_outputs["2_outline"] == right


async def test_run_keeps_the_old_result_out_of_the_cache_when_a_changed_stage_fails():
    harness = Harness()
    topic = make_expository_topic()
    right = make_expository_outline(topic["core_argument"])
    await (await harness.pipeline(FakeLLMClient([json.dumps(right)]))).run(RUN_ID, RunSeed(topic=topic), stop_after=2)
    changed = {**topic, "out_of_scope": ["Reactive transactions"]}
    wrong = json.dumps(make_expository_outline("A different thesis."))

    with pytest.raises(LLMRetriesExhaustedError):
        await (await harness.pipeline(FakeLLMClient([wrong] * 3))).run(RUN_ID, RunSeed(topic=changed), stop_after=2)

    # 실패한 호출의 프롬프트가 옛 결과에 붙지 않았으므로 다음 실행이 그 단계를 다시 부른다
    retry = FakeLLMClient([json.dumps(right)])
    await (await harness.pipeline(retry)).run(RUN_ID, RunSeed(topic=changed), stop_after=2)
    assert len(retry.calls) == 1


async def test_run_stops_when_outline_fact_check_finds_a_blocking_claim():
    harness = Harness()
    topic = make_expository_topic()
    outline = make_expository_outline(topic["core_argument"])
    claim = {
        "location": "deep_dive_sections[0].principle",
        "claim_type": "API",
        "claim": "claim",
        "verdict": "REFUTED",
        "source_url": "https://docs.spring.io",
        "evidence": "evidence",
        "corrected_text": "",
        "blocking": True,
    }
    outputs = [outline, {"outline": outline, "review_log": []}, {"outline": outline, "claims": [claim]}]
    client = FakeLLMClient([json.dumps(output) for output in outputs])

    with pytest.raises(LLMOutputError, match="--redo-from 2"):
        await (await harness.pipeline(client)).run(RUN_ID, RunSeed(topic=topic))


async def test_run_raises_when_the_latest_version_has_no_expository_prompts():
    harness = Harness(body=make_pipeline_body())
    client = FakeLLMClient([])

    with pytest.raises(ValueError, match="설명문 프롬프트가 없다"):
        await (await harness.pipeline(client)).run(RUN_ID, RunSeed())

    assert client.calls == []


async def test_get_result_raises_for_an_unknown_run():
    harness = Harness()
    with pytest.raises(NotFoundError):
        await (await harness.pipeline(FakeLLMClient([]))).get_result("expository-none")


CATEGORIES = [
    PostCategory(id="main-1", value="backend", label="Backend"),
    PostCategory(id="sub-1", parent_id="main-1", value="spring", label="Spring"),
]


async def _finished(harness: Harness, topic: dict | None = None) -> None:
    """9단계까지 끝난 실행을 만든다. topic 을 주면 1단계를 건너뛴 실행이다(카테고리·키워드 없음)."""
    script = _full_script() if topic is None else _full_script()[1:]
    await (await harness.pipeline(FakeLLMClient(script))).run(RUN_ID, RunSeed(topic=topic))


async def test_publish_posts_the_article_with_the_note_and_records_it_once():
    harness = Harness()
    await _finished(harness)
    post_be = FakePostBE(categories=CATEGORIES)
    pipeline = await harness.pipeline(FakeLLMClient([]), post_be)

    result = await pipeline.publish(RUN_ID)

    assert result.external_post_id == "post-1"
    [payload] = post_be.payloads
    assert (payload.title, payload.main_category_id, payload.sub_category_id, payload.language) == (
        KOREAN_TITLE,
        "main-1",
        "sub-1",
        "ko",
    )
    assert payload.tags == ["spring", "transaction", "proxy"]
    assert post_be.providers == ["anthropic"]  # 작성(5단계) 모델의 벤더 계정
    stored = await harness.runs.get(RUN_ID)
    assert stored is not None and stored.publication is not None
    # 사실 확인은 키가 있는 anthropic 후보로 돌았다. 그 모델이 리뷰 줄에 렌즈 라벨과 함께 남는다
    assert stored.publication.note.splitlines()[2:] == [
        "## AI Information",
        "",
        "- 작성: claude-opus-5-5",
        "- 리뷰: claude-opus-5-5(사실·구조)",
    ]
    assert "AI Information" in str(payload.content) and "sec:intro" not in str(payload.content)
    with pytest.raises(InvalidRequestError, match="이미 게시글 post-1"):
        await pipeline.publish(RUN_ID)
    assert len(post_be.payloads) == 1


async def test_publish_lists_category_choices_when_the_run_has_none_and_takes_them_as_arguments():
    harness = Harness()
    await _finished(harness, topic={key: make_expository_topic()[key] for key in TOPIC_KEYS})
    post_be = FakePostBE(categories=CATEGORIES)
    pipeline = await harness.pipeline(FakeLLMClient([]), post_be)

    with pytest.raises(
        InvalidRequestError, match="Backend / Spring: --main-category-id main-1 --sub-category-id sub-1"
    ):
        await pipeline.publish(RUN_ID)
    assert post_be.payloads == []

    await pipeline.publish(RUN_ID, main_category_id="main-1", sub_category_id="sub-1", keywords=["spring"])

    assert post_be.payloads[0].tags == ["spring"]
    stored = await harness.runs.get(RUN_ID)
    assert stored is not None and stored.article is not None
    assert (stored.article.main_category_id, stored.article.keywords) == ("main-1", ["spring"])


async def test_publish_can_be_retried_after_a_confirmed_failure_but_not_after_an_unknown_result():
    harness = Harness()
    await _finished(harness)
    post_be = FakePostBE([PublishFailedError("400"), PublishUncertainError("timeout")], categories=CATEGORIES)
    pipeline = await harness.pipeline(FakeLLMClient([]), post_be)

    with pytest.raises(PublishFailedError):
        await pipeline.publish(RUN_ID)
    with pytest.raises(PublishUncertainError):  # 확정 실패 뒤에는 다시 올릴 수 있다
        await pipeline.publish(RUN_ID)
    with pytest.raises(InvalidRequestError, match="--force"):  # 결과를 모르면 막는다
        await pipeline.publish(RUN_ID)
    assert len(post_be.payloads) == 2

    result = await pipeline.publish(RUN_ID, force=True)
    assert result.external_post_id == "post-3"


async def test_publish_refuses_an_unfinished_or_unknown_run_and_a_missing_post_be():
    harness = Harness()
    topic = make_expository_topic()
    outline = json.dumps(make_expository_outline(topic["core_argument"]))
    await (await harness.pipeline(FakeLLMClient([outline]))).run(RUN_ID, RunSeed(topic=topic), stop_after=2)
    pipeline = await harness.pipeline(FakeLLMClient([]), FakePostBE(categories=CATEGORIES))

    with pytest.raises(InvalidRequestError, match="9단계"):
        await pipeline.publish(RUN_ID)
    with pytest.raises(NotFoundError):
        await pipeline.publish("expository-none")

    other = Harness()
    await _finished(other)
    with pytest.raises(ValueError, match="POST_BE_URL"):
        await (await other.pipeline(FakeLLMClient([]))).publish(RUN_ID)
