"""버전 문서가 실행에 닿는 자리: 버전 없는 러너·코루틴, 라운드의 버전 고정, 재배포 없는 프롬프트 수정 (설계 09 문서 2.7절·D-73, 이행 1a)."""

import logging

import pytest

from app.models.enums import PostStatus, RoundStatus
from app.models.pipeline import Parameters
from tests.factories import draft_output, make_category, make_round, review_output
from tests.fakes import PipelineHarness, call_input_of

pytestmark = pytest.mark.anyio


def _fast_track() -> dict:
    return {"WRITE_DRAFT": [draft_output()] * 3, "REVIEW_FULL": review_output()}


async def test_runner_skips_rounds_without_any_pipeline_version_and_logs(caplog):
    """옛 라운드(버전 고정 없음)인데 버전이 하나도 없으면 러너는 로그를 남기고 그 라운드를 건너뛴다 (2.7절)."""
    harness = PipelineHarness(_fast_track(), with_version=False)
    await harness.seed_round()
    publishing_round = await harness.rounds.get("round-1")
    assert publishing_round is not None
    publishing_round.pipeline_version_id = None
    await harness.rounds.save(publishing_round)

    with caplog.at_level(logging.ERROR):
        await harness.runner.scan()
        await harness.draft_pipeline.drive("round-1:A")

    assert harness.runner.running == []
    assert (await harness.draft("A")).status == PostStatus.TOPIC_LOCKED
    assert harness.llm.all_kinds == []
    assert any("no pipeline version" in record.message for record in caplog.records)


async def test_a_pinned_round_cannot_run_when_its_version_is_missing(caplog):
    harness = PipelineHarness(_fast_track())
    await harness.seed_round()
    publishing_round = await harness.rounds.get("round-1")
    assert publishing_round is not None
    publishing_round.pipeline_version_id = "v9"
    await harness.rounds.save(publishing_round)

    with caplog.at_level(logging.ERROR):
        await harness.runner.scan()

    assert harness.runner.running == []
    assert any("v9 which does not exist" in record.message for record in caplog.records)


async def test_old_round_without_a_pinned_version_runs_on_the_latest():  # ㉒
    harness = PipelineHarness(_fast_track())
    await harness.seed_round()
    publishing_round = await harness.rounds.get("round-1")
    assert publishing_round is not None
    publishing_round.pipeline_version_id = None
    await harness.rounds.save(publishing_round)

    draft = await harness.drive("A")

    assert draft.status == PostStatus.READY_TO_PUBLISH
    receipts = await harness.calls.list_by_draft(draft.draft_id)
    assert {call.pipeline_version for call in receipts} == {"v1"}
    assert {call.prompt_template_version for call in receipts} == {None}


async def test_running_round_keeps_its_version_while_the_next_round_uses_the_new_prompt():  # ㉑
    """edit-prompt 는 재배포 없이 다음 라운드부터 적용되고, 진행 중인 라운드는 처음 버전으로 끝까지 돈다 (D-73)."""
    harness = PipelineHarness(
        {"WRITE_DRAFT": [draft_output()] * 6, "REVIEW_FULL": review_output()},
        **{"publish_requires_approval": True},
    )
    await harness.seed_round()
    newer = await harness.versions.edit_prompt(
        "common", "write_draft", "[template:write_draft] 글 한 편을 한국어로 쓴다. 두 번째 문구. JSON", note="문구 조정"
    )
    later = await harness.round_service.create_round(theme_keyword=None, categories=[make_category()])

    first = await harness.drive("A")

    assert first.status == PostStatus.READY_TO_PUBLISH
    write_calls = [call for call in harness.llm.calls if "글 한 편을 한국어로 쓴다" in call.messages[0].content]
    assert "두 번째 문구" not in write_calls[0].messages[0].content
    receipts = await harness.calls.list_by_draft(first.draft_id)
    assert {call.pipeline_version for call in receipts} == {"v1"}
    assert (newer.version_id, later.pipeline_version_id) == ("v2", "v2")
    assert (await harness.versions.for_round(later)).prompts["common"]["write_draft"].endswith("두 번째 문구. JSON")


async def test_parameters_of_the_pinned_version_drive_the_gate_and_limits():
    """조절 값은 라운드 버전에서 읽는다: 길이 하한을 올린 버전의 라운드는 같은 본문이 게이트에 떨어진다."""
    harness = PipelineHarness({"WRITE_DRAFT": [draft_output()] * 2, "REVISE_DRAFT": [draft_output()]})
    strict = await harness.versions.edit_parameters({"draft_min_chars": 50_000, "max_validation_retries": 1})
    await harness.seed_round()
    publishing_round = await harness.rounds.get("round-1")
    assert publishing_round is not None
    publishing_round.pipeline_version_id = strict.version_id
    await harness.rounds.save(publishing_round)

    draft = await harness.drive("A")

    assert draft.status == PostStatus.FAILED
    assert draft.failure is not None and draft.failure.code == "GATE_RETRIES_EXHAUSTED"
    assert draft.versions[0].validation_errors and "자 이상이어야 한다" in draft.versions[0].validation_errors[0]
    assert (await harness.draft("A")).versions[0].validation_errors == draft.versions[0].validation_errors


async def test_topic_selection_reads_personas_and_limits_from_the_round_version():
    """주제 제안 입력의 페르소나(탐색 도메인)와 수준 상한이 버전 문서에서 온다."""
    harness = PipelineHarness()
    body = harness.version.body()
    body.personas["troubleshooter"] = body.personas["troubleshooter"].model_copy(
        update={"writer_bias": "Custom failure domain", "max_level_without_evidence": None}
    )
    custom = await harness.versions.create(body, source="import", note="페르소나 수정")
    await harness.rounds.add(make_round(pipeline_version_id=custom.version_id))
    harness.llm.scripts["PROPOSE_TOPIC"] = [RuntimeError("stop")] * 3

    await harness.runner.scan()
    while harness.runner.running:
        await __import__("asyncio").sleep(0)

    proposals = [call for call in harness.llm.calls if "주제를 세 개 제안" in call.messages[0].content]
    assert len(proposals) == 3
    assert any("Custom failure domain" in call.messages[0].content for call in proposals)
    assert all(call_input_of(call.messages)["level_criteria"]["version"] == "v1" for call in proposals)
    assert (await harness.rounds.get("round-1")).status == RoundStatus.ABORTED  # RuntimeError → INTERNAL_ERROR


def test_parameters_default_matches_the_fixture_harness_overrides():
    """하네스는 길이 하한 1·재시도 2·승인 켜짐만 바꾸고 나머지는 기본값이다. 시나리오가 기본 동작 위에서 돈다는 뜻이다."""
    harness = PipelineHarness()
    expected = Parameters(
        draft_min_chars=1,
        draft_max_chars=100_000,
        draft_reject_chars=100_000,
        max_llm_retries=2,
        publish={"requires_approval": True},
    )

    assert harness.version.parameters == expected
