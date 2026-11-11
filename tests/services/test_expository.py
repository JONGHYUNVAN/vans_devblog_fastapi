"""설명문 파이프라인 러너: 가짜 LLM 으로 1~9단계, 버전·영수증, 이어 하기, 검사 실패 재시도, 고칠 수 없는 사실 오류."""

import json
from pathlib import Path

import pytest

from app.agents.expository import STAGE_KEYS
from app.clients.llm.base import LLMClient
from app.clients.llm.registry import LLMClientRegistry
from app.core.config import Settings
from app.core.exceptions import LLMOutputError
from app.models.pipeline import PipelineVersionBody
from app.repositories.expository_run import ExpositoryRunRepository
from app.repositories.pipeline_version import PipelineVersionRepository
from app.services.agent_calls import AgentCallService
from app.services.expository import ExpositoryPipeline, RunSeed
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
from tests.fakes import FakeAgentCallRepository, FakeAsyncDatabase, FakeLLMClient

pytestmark = pytest.mark.anyio

KOREAN_TITLE = "셀프 호출이 트랜잭션을 건너뛰는 이유"


async def _no_sleep(seconds: float) -> None:
    return None


async def _pipeline(
    run_dir: Path,
    client: LLMClient,
    *,
    receipts: FakeAgentCallRepository | None = None,
    body: PipelineVersionBody | None = None,
) -> ExpositoryPipeline:
    """버전 v1(저장소 pipeline/expository 의 프롬프트·라우트) 하나가 든 가짜 Mongo 위의 러너."""
    versions = PipelineVersionRepository(FakeAsyncDatabase())
    await versions.add(make_pipeline_version(body=body or make_expository_body(draft_min_chars=10)))
    # 키가 anthropic 하나뿐이라 사실 확인 라우트의 google 첫 후보도 anthropic 으로 내려간다
    registry = LLMClientRegistry({"anthropic": client}, concurrency_per_provider=1)
    return ExpositoryPipeline(
        calls=AgentCallService(
            receipts or FakeAgentCallRepository(), registry, Settings(_env_file=None), sleep=_no_sleep
        ),
        versions=PipelineVersionService(versions),
        providers=["anthropic"],
        repository=ExpositoryRunRepository(run_dir),
        publisher=None,
        main_categories=[],
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


async def test_run_completes_every_stage_and_writes_final_files(tmp_path: Path):
    client = FakeLLMClient(_full_script())
    receipts = FakeAgentCallRepository()
    final = await (await _pipeline(tmp_path, client, receipts=receipts)).run(RunSeed())
    assert final is not None
    assert final["pipeline_version"] == "v1"
    assert final["title"] == KOREAN_TITLE
    assert final["keywords"] == ["spring", "transaction", "proxy"]
    assert (tmp_path / "final.md").read_text(encoding="utf-8").startswith(f"# {KOREAN_TITLE}")
    assert "The concept section" in (tmp_path / "6_article_fact_check.article.md").read_text(encoding="utf-8")
    assert "**📊 Overview**" in (tmp_path / "7_summary_glossary.article.md").read_text(encoding="utf-8")
    # 단계마다 영수증 한 건: 실행 id, 단계 key, 프롬프트를 읽은 버전
    assert [receipt.item_id for receipt in receipts.calls] == list(STAGE_KEYS)
    assert {(receipt.kind, receipt.round_id, receipt.pipeline_version) for receipt in receipts.calls} == {
        ("EXPOSITORY", f"expository-{tmp_path.name}", "v1")
    }
    assert receipts.calls[3].model_binding.model_id == "claude-opus-5-5"
    searches = [call.params.web_search for call in client.calls]
    assert searches == [False, False, False, True, False, True, False, False, False]


async def test_run_skips_stages_whose_prompt_did_not_change(tmp_path: Path):
    await (await _pipeline(tmp_path, FakeLLMClient(_full_script()))).run(RunSeed())
    again = FakeLLMClient([])  # 부르면 AssertionError
    final = await (await _pipeline(tmp_path, again)).run(RunSeed())
    assert again.calls == []
    assert final is not None
    assert final["title"] == KOREAN_TITLE


async def test_run_sends_check_errors_back_and_retries(tmp_path: Path):
    topic = make_expository_topic()
    wrong = make_expository_outline("A different thesis.")
    right = make_expository_outline(topic["core_argument"])
    client = FakeLLMClient([json.dumps(wrong), json.dumps(right)])
    await (await _pipeline(tmp_path, client)).run(RunSeed(topic=topic), stop_after=2)
    retry = client.calls[1].messages
    assert [message.role for message in retry] == ["system", "user", "assistant", "user"]
    assert "core_message must be an exact" in retry[-1].content
    assert json.loads((tmp_path / "2_outline.json").read_text(encoding="utf-8")) == right


async def test_run_stops_when_outline_fact_check_finds_a_blocking_claim(tmp_path: Path):
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
    with pytest.raises(LLMOutputError, match="2_outline.json"):
        await (await _pipeline(tmp_path, client)).run(RunSeed(topic=topic))


async def test_run_raises_when_the_latest_version_has_no_expository_prompts(tmp_path: Path):
    client = FakeLLMClient([])
    pipeline = await _pipeline(tmp_path, client, body=make_pipeline_body())
    with pytest.raises(ValueError, match="설명문 프롬프트가 없다"):
        await pipeline.run(RunSeed())
    assert client.calls == []
