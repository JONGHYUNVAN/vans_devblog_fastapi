"""에이전트 내부 API 테스트. 인증(AGENTS_INTERNAL_API_KEY), 응답 계약(golden), 결정 엔드포인트의 상태 코드를 확인한다.

FakeAsyncDatabase 를 get_async_database 로 꽂아 실제 리포지토리·서비스가 돈다. 시각은 고정해 golden 과 그대로 비교한다.
"""

import asyncio
from datetime import UTC, datetime

import pytest

from app.clients.publisher.base import PostCategory
from app.core.config import Settings, get_settings
from app.dependencies import build_pipeline_version_service, get_async_database, get_round_service
from app.main import app
from app.models.enums import (
    AgentSlot,
    ArbitrationOutcome,
    CallKind,
    EscalationReason,
    FailureCode,
    IssueSeverity,
    PostStatus,
    RoundStatus,
)
from app.models.post_draft import FailureRecord
from app.repositories.agent_call import AgentCallRepository
from app.repositories.agent_round import PublishingRoundRepository
from app.repositories.model_route import ModelRouteRepository
from app.repositories.pipeline_version import PipelineVersionRepository
from app.repositories.post_draft import PostDraftRepository
from app.repositories.topic_registry import TopicRegistryRepository
from app.services.evaluation import evaluate
from app.services.round import RoundService
from tests.clients.publisher.test_post_be import ROWS
from tests.conftest import load_golden
from tests.factories import (
    FAKE_VENDORS,
    add_reviews,
    make_arbitration,
    make_call,
    make_draft,
    make_pipeline_version,
    make_registry_entry,
    make_round,
    reject,
    route_documents,
    write_next_version,
)
from tests.fakes import FakeAsyncDatabase, FakePostBE

A, B, C = AgentSlot.A, AgentSlot.B, AgentSlot.C
KEY = {"X-Internal-Key": "agents-secret"}
T = datetime(2026, 11, 2, 9, 0, tzinfo=UTC)
ROUND = "round-1"


async def _seed(database: FakeAsyncDatabase) -> None:
    rounds = PublishingRoundRepository(database)
    drafts = PostDraftRepository(database)
    calls = AgentCallRepository(database)
    topics = TopicRegistryRepository(database)
    await rounds.add(
        make_round(
            ROUND,
            status=RoundStatus.IN_PROGRESS,
            draft_ids=[f"{ROUND}:{slot}" for slot in AgentSlot],
            created_at=T,
            updated_at=T,
        )
    )
    # A: v1 리뷰(B 의 CRITICAL) → v2 에서 기각 → SPOT_CHECK 미해소 → 중재 UPHOLD_REVIEWER → 격리
    escalated = make_draft(A, created_at=T, updated_at=T)
    write_next_version(escalated)
    add_reviews(escalated, {B: [IssueSeverity.CRITICAL]})
    escalated.apply_evaluation(evaluate(escalated, 2))
    [item] = escalated.open_blocking_items(1)
    write_next_version(escalated, reject(item))
    add_reviews(escalated, resolved={item.item_id: False})
    escalated.apply_evaluation(evaluate(escalated, 2))
    escalated.add_arbitration(make_arbitration(escalated, item, ArbitrationOutcome.UPHOLD_REVIEWER))
    escalated.apply_evaluation(evaluate(escalated, 2))
    escalated.escalate(EscalationReason.MAX_ITERATIONS_REACHED)
    escalated.versions[1].content_markdown += "\n수정한 문단"  # diff 가 보이게
    escalated.updated_at = T
    # B: 예산 초과로 FAILED. C: 아직 TOPIC_LOCKED
    failed = make_draft(B, created_at=T, updated_at=T)
    failed.status = PostStatus.FAILED
    failed.failure = FailureRecord(
        from_status=PostStatus.IN_REVIEW, code=FailureCode.BUDGET_EXCEEDED, message="over", occurred_at=T
    )
    for draft in (escalated, failed, make_draft(C, created_at=T, updated_at=T)):
        assert await drafts.add_if_missing(draft)
    for draft_id in (f"{ROUND}:{slot}" for slot in AgentSlot):
        await topics.add_if_missing(make_registry_entry(draft_id, created_at=T))
    await calls.add(make_call(call_id="call-1", prompt_tokens=100, completion_tokens=20))
    await calls.add(make_call(call_id="call-2", draft_id=None, kind=CallKind.PROPOSE_TOPIC, started_second=1))
    await calls.add(make_call(call_id="call-3", draft_id=f"{ROUND}:B", prompt_tokens=100, completion_tokens=20))


@pytest.fixture
def database() -> FakeAsyncDatabase:
    database = FakeAsyncDatabase()
    app.dependency_overrides[get_settings] = lambda: Settings(
        _env_file=None, agents_internal_api_key="agents-secret", internal_api_key="search-secret"
    )
    app.dependency_overrides[get_async_database] = lambda: database
    asyncio.run(_seed(database))
    asyncio.run(ModelRouteRepository(database).replace_all(route_documents()))
    asyncio.run(PipelineVersionRepository(database).add(make_pipeline_version()))  # 라운드 생성·승인 토글의 버전 v1
    return database


def test_agents_api_requires_the_agents_key_not_the_search_key(client, database):
    assert client.get(f"/api/v1/agents/rounds/{ROUND}/").status_code == 403
    assert client.get(f"/api/v1/agents/rounds/{ROUND}/").json() == load_golden("internal_index_nokey")
    assert client.get(f"/api/v1/agents/rounds/{ROUND}/", headers={"X-Internal-Key": "search-secret"}).status_code == 403
    assert client.get(f"/api/v1/agents/rounds/{ROUND}/", headers=KEY).status_code == 200


def test_round_detail_matches_golden(client, database):
    response = client.get(f"/api/v1/agents/rounds/{ROUND}/", headers=KEY)

    assert response.status_code == 200
    assert response.json() == load_golden("agents_round_detail")
    assert client.get("/api/v1/agents/rounds/nope/", headers=KEY).status_code == 404


def test_list_rounds_filters_by_status(client, database):
    assert [each["round_id"] for each in client.get("/api/v1/agents/rounds/", headers=KEY).json()] == [ROUND]
    assert client.get("/api/v1/agents/rounds/", headers=KEY, params={"status": "COMPLETED"}).json() == []
    assert client.get("/api/v1/agents/rounds/", headers=KEY, params={"status": "nope"}).status_code == 400


def test_create_round_400_without_post_be_and_201_with_categories(client, database):
    response = client.post("/api/v1/agents/rounds/", headers=KEY, json={"theme_keyword": "mongodb"})
    assert response.status_code == 400 and response.json()["error"] == "Invalid request"

    def with_post_be():
        return RoundService(
            PublishingRoundRepository(database),
            PostDraftRepository(database),
            TopicRegistryRepository(database),
            version_service=build_pipeline_version_service(database),
            publisher=FakePostBE(
                categories=[
                    PostCategory(id=row["id"], parent_id=row["parentId"], value=row["value"], label=row["label"])
                    for row in ROWS
                ]
            ),
            route_repository=ModelRouteRepository(database),
            providers=["fake", *FAKE_VENDORS.values()],
            main_categories=["backend"],
        )

    app.dependency_overrides[get_round_service] = with_post_be
    bindings = {
        slot: {"provider": "fake", "model_id": "m", "persona_id": persona, "max_output_tokens": 100}
        for slot, persona in (("A", "architect"), ("B", "systems_engineer"), ("C", "troubleshooter"))
    }
    response = client.post(
        "/api/v1/agents/rounds/", headers=KEY, json={"theme_keyword": "mongodb", "bindings": bindings}
    )

    assert response.status_code == 201
    body = response.json()
    assert (body["status"], body["round_seq"], body["theme_keyword"], body["draft_ids"]) == (
        "TOPIC_SELECTION",
        2,
        "mongodb",
        [],
    )
    same_lens = bindings | {"B": bindings["A"]}
    assert client.post("/api/v1/agents/rounds/", headers=KEY, json={"bindings": same_lens}).status_code == 400


def test_abort_round_rejects_active_drafts(client, database):
    response = client.post(f"/api/v1/agents/rounds/{ROUND}/abort/", headers=KEY)

    assert response.status_code == 200 and response.json()["status"] == "ABORTED"
    detail = client.get(f"/api/v1/agents/rounds/{ROUND}/", headers=KEY).json()
    assert [draft["status"] for draft in detail["drafts"]] == ["REJECTED"] * 3
    assert client.post(f"/api/v1/agents/rounds/{ROUND}/abort/", headers=KEY).status_code == 409
    assert client.post("/api/v1/agents/rounds/nope/abort/", headers=KEY).status_code == 404


def test_draft_history_exposes_the_whole_document(client, database):
    response = client.get(f"/api/v1/agents/drafts/{ROUND}:A/", headers=KEY)

    assert response.status_code == 200
    body = response.json()
    assert (
        body["draft_id"],
        body["status"],
        len(body["versions"]),
        len(body["reviews"]),
        len(body["arbitrations"]),
    ) == (
        f"{ROUND}:A",
        "ESCALATED",
        2,
        3,
        1,
    )
    assert "_id" not in body and "revision" not in body
    assert client.get("/api/v1/agents/drafts/nope/", headers=KEY).status_code == 404


def test_escalation_summary_matches_golden(client, database):
    response = client.get(f"/api/v1/agents/drafts/{ROUND}:A/escalation/", headers=KEY)

    assert response.status_code == 200
    assert response.json() == load_golden("agents_escalation")


def test_escalation_decisions_validate_the_body_and_change_state(client, database):
    url = f"/api/v1/agents/drafts/{ROUND}:A/escalation/"
    assert client.post(url, headers=KEY, json={"decision": "REVISE", "decided_by": "사람"}).status_code == 400
    assert (
        client.post(url, headers=KEY, json={"decision": "CONFIRM_PUBLISHED", "decided_by": "사람"}).status_code == 400
    )
    confirm = {"decision": "CONFIRM_PUBLISHED", "decided_by": "사람", "external_post_id": "p"}
    assert client.post(url, headers=KEY, json=confirm).status_code == 409  # PUBLISH_UNCERTAIN 이 아니다

    response = client.post(url, headers=KEY, json={"decision": "REVISE", "decided_by": "사람", "instruction": "줄여라"})

    assert response.status_code == 200
    assert (response.json()["status"], response.json()["revision_cause"]) == ("REVISING", "HUMAN")
    assert client.post(url, headers=KEY, json={"decision": "REJECT", "decided_by": "사람"}).status_code == 409


def test_approve_retry_discard(client, database):
    approve = f"/api/v1/agents/drafts/{ROUND}:C/approve/"
    assert client.post(approve, headers=KEY, json={"approved_by": "사람"}).status_code == 409  # TOPIC_LOCKED
    database.agent_drafts.docs[2]["status"] = PostStatus.READY_TO_PUBLISH.value
    response = client.post(approve, headers=KEY, json={"approved_by": "사람"})
    assert response.status_code == 200 and response.json()["approved_by"] == "사람"
    assert client.post(approve, headers=KEY, json={"approved_by": "사람"}).status_code == 409  # 재승인

    retry = f"/api/v1/agents/drafts/{ROUND}:B/retry/"
    assert client.post(retry, headers=KEY, json={"token_limit": 0}).status_code == 400
    assert client.post(retry, headers=KEY, json={"token_limit": 50}).status_code == 400  # 사용량 120 이하
    response = client.post(retry, headers=KEY, json={"token_limit": 500})
    assert response.status_code == 200
    assert (response.json()["status"], response.json()["token_limit"], response.json()["failure"]) == (
        "IN_REVIEW",
        500,
        None,
    )

    discard = f"/api/v1/agents/drafts/{ROUND}:B/discard/"
    assert client.post(discard, headers=KEY, json={"decided_by": "사람"}).status_code == 409  # 이제 FAILED 가 아니다
    database.agent_drafts.docs[1]["status"] = PostStatus.FAILED.value
    response = client.post(discard, headers=KEY, json={"decided_by": "사람"})
    assert response.status_code == 200 and response.json()["status"] == "REJECTED"
    assert database.agent_topics.docs[1]["status"] == "RELEASED"


def test_calls_match_golden_and_require_a_filter(client, database):
    response = client.get("/api/v1/agents/calls/", headers=KEY, params={"round_id": ROUND})

    assert response.status_code == 200
    assert response.json() == load_golden("agents_calls")
    by_draft = client.get("/api/v1/agents/calls/", headers=KEY, params={"draft_id": f"{ROUND}:A"}).json()
    assert [call["call_id"] for call in by_draft] == ["call-1"]
    assert client.get("/api/v1/agents/calls/", headers=KEY).status_code == 400


def test_metrics_cover_the_round_and_404_for_unknown_round(client, database):
    response = client.get("/api/v1/agents/metrics/", headers=KEY, params={"round_id": ROUND})

    assert response.status_code == 200
    body = response.json()
    assert (body["drafts"], body["status_counts"]) == (3, {"ESCALATED": 1, "FAILED": 1, "TOPIC_LOCKED": 1})
    assert body["tokens_by_kind"] == {"WRITE_DRAFT": 240}
    assert client.get("/api/v1/agents/metrics/", headers=KEY).json()["drafts"] == 3
    assert client.get("/api/v1/agents/metrics/", headers=KEY, params={"round_id": "nope"}).status_code == 404


def test_pipeline_prompt_edit_makes_a_child_version_and_rejects_unknown_or_same(client, database):  # D-89
    text = "[template:polish_tone] 새 교정 규칙. 입력(JSON)을 읽고 JSON 으로만 답한다."
    url = "/api/v1/agents/pipeline/prompts/common/polish_tone/"
    asyncio.run(PipelineVersionRepository(database).next_seq())  # 픽스처가 v1 을 순번 발급 없이 넣었다
    assert client.post(url, json={"text": text}).status_code == 403

    response = client.post(url, headers=KEY, json={"text": text, "note": "D-90"})

    assert response.status_code == 201
    created = response.json()
    assert (created["version_id"], created["parent_version_id"], created["source"]) == ("v2", "v1", "edit-prompt")
    assert created["note"] == "D-90" and created["warnings"] == []
    stored = asyncio.run(PipelineVersionRepository(database).get("v2"))
    assert stored is not None and stored.prompts["common"]["polish_tone"] == text and stored.created_by == "api"
    # 같은 내용, 없는 템플릿·가족, 빈 본문
    assert client.post(url, headers=KEY, json={"text": text}).status_code == 400
    assert (
        client.post("/api/v1/agents/pipeline/prompts/common/nope/", headers=KEY, json={"text": text}).status_code == 400
    )
    assert (
        client.post("/api/v1/agents/pipeline/prompts/x/polish_tone/", headers=KEY, json={"text": text}).status_code
        == 400
    )
    assert client.post(url, headers=KEY, json={"text": ""}).status_code == 400  # 형식 오류도 이 앱은 400


def test_pipeline_parameter_edit_makes_a_child_version_and_rejects_bad_paths(client, database):  # D-89
    url = "/api/v1/agents/pipeline/parameters/"
    asyncio.run(PipelineVersionRepository(database).next_seq())  # 픽스처가 v1 을 순번 발급 없이 넣었다

    response = client.post(url, headers=KEY, json={"assignments": {"polish.length_slack_chars": 40}})

    assert response.status_code == 201 and response.json()["source"] == "edit-param"
    stored = asyncio.run(PipelineVersionRepository(database).get(response.json()["version_id"]))
    assert stored is not None and stored.parameters.polish.length_slack_chars == 40
    assert client.post(url, headers=KEY, json={"assignments": {"polish.nope": 1}}).status_code == 400
    assert client.post(url, headers=KEY, json={"assignments": {"max_iterations": "many"}}).status_code == 400
    assert client.post(url, headers=KEY, json={"assignments": {"max_iterations": 0}}).status_code == 400  # R-09
    assert client.post(url, headers=KEY, json={"assignments": {}}).status_code == 400  # 형식 오류도 이 앱은 400


def test_pipeline_route_edit_makes_a_child_version_and_rejects_unknown_routes(client, database):  # D-102
    url = "/api/v1/agents/pipeline/routes/FACT_TRANSFORM/"
    candidates = [{"provider": "fake-b", "model_id": "fake-b-2", "max_output_tokens": 2000}]
    asyncio.run(PipelineVersionRepository(database).next_seq())  # 픽스처가 v1 을 순번 발급 없이 넣었다
    assert client.post(url, json={"candidates": candidates}).status_code == 403

    response = client.post(url, headers=KEY, json={"candidates": candidates, "note": "모델 교체"})

    assert response.status_code == 201
    created = response.json()
    assert (created["parent_version_id"], created["source"], created["note"]) == ("v1", "edit-route", "모델 교체")
    stored = asyncio.run(PipelineVersionRepository(database).get(created["version_id"]))
    assert stored is not None and stored.routes["FACT_TRANSFORM"][0].model_id == "fake-b-2"
    # 같은 내용, 없는 라우트, 빈 후보
    assert client.post(url, headers=KEY, json={"candidates": candidates}).status_code == 400
    assert (
        client.post("/api/v1/agents/pipeline/routes/NOPE/", headers=KEY, json={"candidates": candidates}).status_code
        == 400
    )
    assert client.post(url, headers=KEY, json={"candidates": []}).status_code == 400  # 형식 오류도 이 앱은 400
