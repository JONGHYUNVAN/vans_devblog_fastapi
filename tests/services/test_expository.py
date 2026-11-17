"""설명문 파이프라인 러너: 가짜 LLM 으로 1~9단계, 버전·영수증, 이어 하기, 검사 실패 재시도, 고칠 수 없는 사실 오류."""

import json

import pytest

from app.agents.expository import STAGE_KEYS, stage_prompts
from app.clients.llm.base import LLMClient
from app.clients.llm.registry import LLMClientRegistry
from app.clients.publisher.base import PostCategory
from app.core.config import Settings
from app.core.exceptions import (
    InvalidRequestError,
    LLMOutputError,
    LLMRetriesExhaustedError,
    LLMTransportError,
    NotFoundError,
    PublishFailedError,
    PublishUncertainError,
)
from app.models.enums import GlossaryStatus
from app.models.model_binding import CallModel
from app.models.pipeline import PipelineVersionBody
from app.models.routing import ROUTE_GLOSSARY
from app.repositories.expository_run import ExpositoryRunRepository
from app.repositories.glossary import GlossaryRepository
from app.repositories.pipeline_version import PipelineVersionRepository
from app.services.agent_calls import AgentCallService
from app.services.expository import TOPIC_KEYS, ExpositoryPipeline, RunSeed
from app.services.glossary import GlossaryService
from app.services.pipeline_versions import PipelineVersionService
from tests.factories import (
    CONCEPT_SECTION_IDS,
    glossary_candidate,
    glossary_output,
    make_expository_body,
    make_expository_concept_outline,
    make_expository_concept_topic,
    make_expository_english_article,
    make_expository_korean_article,
    make_expository_outline,
    make_expository_summaries,
    make_expository_terms,
    make_expository_topic,
    make_pipeline_body,
    make_pipeline_version,
    new_sense,
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
        self.glossary_repository = GlossaryRepository(self.database)
        self._body = body or make_expository_body(draft_min_chars=10)
        self._seeded = False

    async def pipeline(
        self,
        client: LLMClient,
        publisher: FakePostBE | None = None,
        *,
        with_glossary: bool = True,
        glossary: GlossaryService | None = None,
    ) -> ExpositoryPipeline:
        """with_glossary 면 용어 서비스를 붙인다. glossary 를 주면 그 서비스를 쓴다(따라 쓰기 실패 대역)."""
        versions = PipelineVersionRepository(self.database)
        if not self._seeded:
            await versions.add(make_pipeline_version(body=self._body))
            self._seeded = True
        # 키가 anthropic 하나뿐이라 사실 확인 라우트의 google 첫 후보도 anthropic 으로 내려간다
        registry = LLMClientRegistry({"anthropic": client}, concurrency_per_provider=1)
        calls = AgentCallService(self.receipts, registry, Settings(_env_file=None), sleep=_no_sleep)
        if glossary is None and with_glossary:
            glossary = GlossaryService(
                repository=self.glossary_repository, settings=Settings(_env_file=None), agent_call_service=calls
            )
        return ExpositoryPipeline(
            calls=calls,
            versions=PipelineVersionService(versions),
            providers=["anthropic"],
            repository=self.runs,
            publisher=publisher,
            main_categories=["backend"],
            glossary=glossary,
        )


def _full_script() -> list[str]:
    topic = make_expository_topic()
    outline = make_expository_outline(topic["core_argument"])
    patch = {"section_id": "concept", "before": "The concept part", "after": "The concept section", "reason": "r"}
    # 작성(5)·번역(8)은 "# 제목" + 본문 문서로, 나머지는 JSON 으로 답한다 (D-103)
    outputs = [
        topic,
        outline,
        {"outline": outline, "review_log": []},
        {"outline": outline, "claims": []},
        f"# Self-invocation and transactions\n\n{make_expository_english_article()}",
        {"claims": [], "patches": [patch]},
        {"section_summaries": make_expository_summaries(), "terms": make_expository_terms()},
        f"# {KOREAN_TITLE}\n\n{make_expository_korean_article()}",
        {"summary": "같은 객체 안의 호출은 프록시를 거치지 않아 트랜잭션 설정이 적용되지 않습니다."},
    ]
    return [output if isinstance(output, str) else json.dumps(output, ensure_ascii=False) for output in outputs]


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


async def test_run_sends_the_article_outside_the_json_when_the_layout_is_documents_first():
    body = make_expository_body(draft_min_chars=10, expository={"layouts": {"default": "documents_first"}})
    harness = Harness(body=body)
    client = FakeLLMClient(_full_script())

    result = await (await harness.pipeline(client)).run(RUN_ID, RunSeed())

    assert result.title == KOREAN_TITLE
    stored = await harness.runs.get(RUN_ID)
    assert stored is not None and stored.assembled_markdown is not None
    translate = client.calls[7].messages[1].content
    # 번역 입력 본문이 이스케이프 없이 원문 그대로 지시문 위에 있다
    assert translate.startswith(f"<documents>\n<content_markdown>\n{stored.assembled_markdown}\n</content_markdown>")
    instructions = json.loads(translate.split("\n\n<instructions>\n")[1].removesuffix("\n</instructions>"))
    assert instructions["documents"] == {"content_markdown": "provided above in <content_markdown>"}
    # 실행 문서에 남는 프롬프트는 배치와 무관한 중심 양식이다. 배치만 바꾼 뒤 이어 해도 이 단계를 다시 부르지 않는다
    assert json.loads(stored.stage_prompts["8_translate"])["documents"]["content_markdown"] == stored.assembled_markdown
    # 문서 입력이 없는 단계는 배치와 무관하게 JSON 하나다
    assert client.calls[1].messages[1].content == stored.stage_prompts["2_outline"]


async def test_run_asks_for_the_whole_document_again_when_the_title_line_is_missing():
    harness = Harness()
    topic = make_expository_topic()
    outline = make_expository_outline(topic["core_argument"])
    article = make_expository_english_article()
    script = [
        json.dumps(outline),
        json.dumps({"outline": outline, "review_log": []}),
        json.dumps({"outline": outline, "claims": []}),
        article,  # 제목 줄 없이 본문만
        f"# Self-invocation and transactions\n\n{article}",
    ]
    client = FakeLLMClient(script)

    result = await (await harness.pipeline(client)).run(RUN_ID, RunSeed(topic=topic), stop_after=5)

    retry = client.calls[4].messages
    assert [message.role for message in retry] == ["system", "user", "assistant", "user"]
    assert "'# ' title line" in retry[-1].content and "complete corrected document" in retry[-1].content
    assert result.finished_stages[-1] == "5_write"
    stored = await harness.runs.get(RUN_ID)
    assert stored is not None and stored.stage_outputs["5_write"] == {
        "title": "Self-invocation and transactions",
        "content_markdown": article.strip("\n"),
    }


async def test_run_writes_a_concept_article_without_a_review_call_or_a_problem_section():
    harness = Harness()
    topic = make_expository_concept_topic()
    outline = make_expository_concept_outline(topic["core_argument"])
    article = make_expository_english_article(ids=CONCEPT_SECTION_IDS)
    script = [json.dumps(outline), json.dumps({"outline": outline, "claims": []}), f"# B+tree lookups\n\n{article}"]
    client = FakeLLMClient(script)

    result = await (await harness.pipeline(client)).run(RUN_ID, RunSeed(topic=topic), stop_after=5)

    # 개념 양식은 여러 번 올 수 있는 본론 역할이 없어 뼈대 검토를 부르지 않고 뼈대를 그대로 넘긴다
    assert [receipt.item_id for receipt in harness.receipts.calls] == ["2_outline", "4_outline_fact_check", "5_write"]
    assert result.finished_stages == [
        "1_topic_thesis",
        "2_outline",
        "3_outline_review",
        "4_outline_fact_check",
        "5_write",
    ]
    stored = await harness.runs.get(RUN_ID)
    assert stored is not None and stored.stage_outputs["3_outline_review"] == {"outline": outline, "review_log": []}
    # 지시문의 form 자리에 그 글의 양식이 들어간다: 뼈대 단계는 역할 목록, 작성 단계는 섹션마다 앵커 id 와 쓰는 법
    outline_prompt = json.loads(client.calls[0].messages[1].content)
    assert [region["role"] for region in outline_prompt["form"]["regions"]] == [
        "definition",
        "structure",
        "mechanism",
        "rationale",
        "contrast",
        "synthesis",
    ]
    assert "form" not in outline_prompt["inputs"]
    write_prompt = json.loads(client.calls[2].messages[1].content)
    assert [section["section_id"] for section in write_prompt["form"]["sections"]] == CONCEPT_SECTION_IDS
    assert all(section["guidance"] for section in write_prompt["form"]["sections"])


async def test_run_offers_only_the_form_given_by_the_seed():
    harness = Harness()
    client = FakeLLMClient([json.dumps(make_expository_topic())])

    await (await harness.pipeline(client)).run(RUN_ID, RunSeed(form="diagnosis"), stop_after=1)

    offered = json.loads(client.calls[0].messages[1].content)["inputs"]["forms"]
    assert [form["id"] for form in offered] == ["diagnosis"]
    assert (offered[0]["focus_points_min"], offered[0]["focus_points_max"]) == (3, None)

    # 양식 없이 부르면 최신 버전의 양식 전부가 선택지다
    other = Harness()
    everything = FakeLLMClient([json.dumps(make_expository_topic())])
    await (await other.pipeline(everything)).run(RUN_ID, RunSeed(), stop_after=1)
    offered = json.loads(everything.calls[0].messages[1].content)["inputs"]["forms"]
    assert [form["id"] for form in offered] == ["concept", "diagnosis", "procedure", "taxonomy"]


async def test_run_raises_before_any_call_for_an_unknown_form_or_a_version_without_forms():
    client = FakeLLMClient([])

    with pytest.raises(ValueError, match="모르는 글 양식 'essay'"):
        await (await Harness().pipeline(client)).run(RUN_ID, RunSeed(form="essay"))

    body = make_expository_body(draft_min_chars=10)
    del body.prompts["expository_form"]  # D-104 전에 넣은 버전의 모양
    with pytest.raises(ValueError, match="글 양식이 없다"):
        await (await Harness(body=body).pipeline(client)).run(RUN_ID, RunSeed())

    assert client.calls == []


async def test_run_refuses_a_form_that_differs_from_the_stored_topic():
    harness = Harness()
    await (await harness.pipeline(FakeLLMClient([json.dumps(make_expository_topic())]))).run(
        RUN_ID, RunSeed(), stop_after=1
    )

    with pytest.raises(ValueError, match="--redo-from 1"):
        await (await harness.pipeline(FakeLLMClient([]))).run(RUN_ID, RunSeed(form="concept"), stop_after=1)


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
        "location": "regions[2].key_sentence",
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
        "- 작성: claude-fable-5-2",
        "- 리뷰: claude-opus-5-5(사실·구조)",
    ]
    assert "AI Information" in str(payload.content) and "sec:intro" not in str(payload.content)
    with pytest.raises(InvalidRequestError, match="이미 게시글 post-1"):
        await pipeline.publish(RUN_ID)
    assert len(post_be.payloads) == 1


async def test_publish_lists_category_choices_when_the_run_has_none_and_takes_them_as_arguments():
    harness = Harness()
    await _finished(harness, topic={key: make_expository_topic()[key] for key in ("form", *TOPIC_KEYS)})
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


# ---------- 끝난 글의 용어 사전 등록 ----------

GLOSSARY_MODEL = CallModel(provider="anthropic", model_id="claude-haiku-4-5", max_output_tokens=1000)
PROXY = glossary_candidate(
    term_ko="프록시",
    term_en="proxy",
    surface="프록시가",
    section_id="intro",
    new_sense=new_sense(definition="다른 객체 대신 호출을 받아 앞뒤 처리를 붙이는 객체"),
)
TRANSACTION = glossary_candidate(
    term_ko="트랜잭션",
    term_en="transaction",
    surface="트랜잭션을",
    section_id="concept",
    new_sense=new_sense(domains=("db",), definition="한 단위로 함께 반영되거나 함께 취소되는 작업 묶음"),
)


def _glossary_body() -> PipelineVersionBody:
    """GLOSSARY 라우트에 키가 있는 벤더(anthropic) 후보를 둔 버전. 기본 픽스처의 용어 라우트는 키 없는 가짜 벤더다."""
    body = make_expository_body(draft_min_chars=10)
    body.routes[ROUTE_GLOSSARY] = [GLOSSARY_MODEL]
    return body


async def test_run_registers_the_finished_article_terms_with_the_run_as_source():
    harness = Harness(body=_glossary_body())
    client = FakeLLMClient([*_full_script(), glossary_output(PROXY)])

    result = await (await harness.pipeline(client, FakePostBE(categories=CATEGORIES))).run(RUN_ID, RunSeed())

    # 번호 단계는 그대로 9개이고 용어 추출은 그 뒤 한 번이다
    assert result.finished_stages == list(STAGE_KEYS)
    receipt = harness.receipts.calls[-1]
    assert (receipt.kind, receipt.round_id, receipt.draft_id, receipt.item_id) == ("GLOSSARY", RUN_ID, None, None)
    assert (receipt.target_version, receipt.pipeline_version, receipt.model_binding) == (1, "v1", GLOSSARY_MODEL)
    sent = json.loads(client.calls[-1].messages[1].content.removeprefix("입력(JSON):\n"))
    reader = stage_prompts(harness._body.prompts)["5_write"]["system_config"]["reader"]
    assert (sent["title"], sent["audience"], sent["prerequisites"], sent["main_category"]) == (
        KOREAN_TITLE,
        reader,
        "",
        "Backend",  # 실행에는 카테고리 id 만 있다. 라벨은 Post BE 카테고리 목록에서 찾는다
    )
    assert sent["keywords"] == ["spring", "transaction", "proxy"]
    stored = await harness.runs.get(RUN_ID)
    assert stored is not None and stored.article is not None and stored.glossary is not None
    assert sent["content_markdown"] == stored.article.content_markdown
    record = stored.glossary
    assert (record.skipped, record.call_id, record.target_version) == (None, receipt.call_id, 1)
    assert [(item.surface, item.section_id, item.resolution) for item in record.items] == [("프록시가", "intro", "NEW")]
    headword = await harness.glossary_repository.get_headword("proxy")
    assert headword is not None and headword.status == GlossaryStatus.PROPOSED
    assert headword.proposed_by is not None and headword.proposed_by.draft_id == RUN_ID


async def test_run_finishes_and_records_why_when_the_glossary_is_skipped():
    failing = Harness(body=_glossary_body())
    client = FakeLLMClient([*_full_script(), *[LLMTransportError("boom")] * 5])

    result = await (await failing.pipeline(client)).run(RUN_ID, RunSeed())

    assert result.title == KOREAN_TITLE
    stored = await failing.runs.get(RUN_ID)
    assert stored is not None and stored.glossary is not None
    assert stored.glossary.skipped is not None and stored.glossary.skipped.startswith("LLMRetriesExhaustedError")
    # 카테고리 목록이 없으면(Post BE 미설정) 라벨 없이 뽑는다. id 를 보내지 않는다
    assert '"main_category": ""' in client.calls[9].messages[1].content

    no_route = Harness()  # 기본 픽스처의 용어 라우트 후보에는 키가 없다
    await (await no_route.pipeline(FakeLLMClient(_full_script()))).run(RUN_ID, RunSeed())
    stored = await no_route.runs.get(RUN_ID)
    assert stored is not None and stored.glossary is not None and stored.glossary.skipped == "no GLOSSARY route"


async def test_run_redo_clears_the_glossary_with_the_article():
    harness = Harness(body=_glossary_body())
    await (await harness.pipeline(FakeLLMClient([*_full_script(), glossary_output(PROXY)]))).run(RUN_ID, RunSeed())

    await (await harness.pipeline(FakeLLMClient(_full_script()[7:8]))).run(RUN_ID, RunSeed(), redo_from=8, stop_after=8)

    stored = await harness.runs.get(RUN_ID)
    assert stored is not None and (stored.article, stored.glossary) == (None, None)


async def test_publish_links_the_glossary_to_the_post_and_approves_the_proposals():
    harness = Harness(body=_glossary_body())
    await (await harness.pipeline(FakeLLMClient([*_full_script(), glossary_output(PROXY)]))).run(RUN_ID, RunSeed())

    await (await harness.pipeline(FakeLLMClient([]), FakePostBE(categories=CATEGORIES))).publish(RUN_ID)

    post_glossary = await harness.glossary_repository.get_post_glossary("post-1")
    assert post_glossary is not None
    assert (post_glossary.draft_id, post_glossary.round_id, post_glossary.content_version) == (RUN_ID, RUN_ID, 1)
    assert (post_glossary.pipeline_version, post_glossary.prompt_template_version) == ("v1", None)
    assert post_glossary.call_id == harness.receipts.calls[-1].call_id
    assert [item.surface for item in post_glossary.items] == ["프록시가"]
    [sense_key] = [item.sense_key for item in post_glossary.items]
    sense = await harness.glossary_repository.get_sense(sense_key)
    assert sense is not None and (sense.status, sense.approved_by, sense.post_ids) == (
        GlossaryStatus.APPROVED,
        "auto",
        ["post-1"],
    )


class FailingGlossaryService(GlossaryService):
    """발행 뒤 용어 연결이 실패하는 대역."""

    async def link_post(self, *args, **kwargs) -> None:
        raise RuntimeError("glossary store down")


async def test_publish_succeeds_when_linking_the_glossary_fails():
    harness = Harness()
    await _finished(harness)
    failing = FailingGlossaryService(repository=harness.glossary_repository, settings=Settings(_env_file=None))

    result = await (
        await harness.pipeline(FakeLLMClient([]), FakePostBE(categories=CATEGORIES), glossary=failing)
    ).publish(RUN_ID)

    assert result.external_post_id == "post-1"
    stored = await harness.runs.get(RUN_ID)
    assert stored is not None and stored.publication is not None


async def test_extract_glossary_reextracts_and_overwrites_the_post_glossary_of_a_published_run():
    harness = Harness(body=_glossary_body())
    await (await harness.pipeline(FakeLLMClient([*_full_script(), glossary_output(PROXY)]))).run(RUN_ID, RunSeed())
    await (await harness.pipeline(FakeLLMClient([]), FakePostBE(categories=CATEGORIES))).publish(RUN_ID)

    summary = await (await harness.pipeline(FakeLLMClient([glossary_output(TRANSACTION)]))).extract_glossary(RUN_ID)

    assert summary == {
        "run_id": RUN_ID,
        "post_id": "post-1",
        "items": 1,
        "pending": 0,
        "discarded": 0,
        "skipped": None,
    }
    post_glossary = await harness.glossary_repository.get_post_glossary("post-1")
    assert post_glossary is not None and [item.surface for item in post_glossary.items] == ["트랜잭션을"]
    stored = await harness.runs.get(RUN_ID)
    assert stored is not None and stored.glossary is not None
    assert [item.surface for item in stored.glossary.items] == ["트랜잭션을"]


async def test_extract_glossary_refuses_unknown_and_unfinished_runs_and_a_runner_without_the_service():
    harness = Harness(body=_glossary_body())
    topic = make_expository_topic()
    outline = json.dumps(make_expository_outline(topic["core_argument"]))
    await (await harness.pipeline(FakeLLMClient([outline]))).run(RUN_ID, RunSeed(topic=topic), stop_after=2)
    pipeline = await harness.pipeline(FakeLLMClient([]))

    with pytest.raises(NotFoundError):
        await pipeline.extract_glossary("expository-none")
    with pytest.raises(InvalidRequestError, match="9단계"):
        await pipeline.extract_glossary(RUN_ID)
    with pytest.raises(ValueError, match="glossary service"):
        await (await harness.pipeline(FakeLLMClient([]), with_glossary=False)).extract_glossary(RUN_ID)
