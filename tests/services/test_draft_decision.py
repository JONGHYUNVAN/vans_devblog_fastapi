"""DraftDecisionService 테스트: 전제 상태 검사와 409, 충돌 시 재적용, 종결 결정 뒤 레지스트리·라운드 완료, 재개의 token_limit 검사."""

import pytest

from app.core.exceptions import InvalidRequestError, InvalidTransitionError, NotFoundError
from app.models.enums import (
    AgentSlot,
    EscalationDecisionKind,
    EscalationReason,
    FailureCode,
    PostStatus,
    RoundStatus,
    TopicRegistryStatus,
)
from app.repositories.post_draft import PostDraftRepository
from app.services.draft_decision import DraftDecisionService
from tests.factories import draft_output, make_call, review_output
from tests.fakes import PipelineHarness

pytestmark = pytest.mark.anyio

A, B, C = AgentSlot.A, AgentSlot.B, AgentSlot.C


def _fast_track() -> dict:
    return {"WRITE_DRAFT": [draft_output()], "REVIEW_FULL": review_output()}


async def _ready(harness: PipelineHarness):
    await harness.seed_round()
    draft = await harness.drive(A)
    assert draft.status == PostStatus.READY_TO_PUBLISH
    return draft


async def test_approve_records_approver_and_rejects_other_states():
    harness = PipelineHarness(_fast_track())
    draft = await _ready(harness)

    approved = await harness.decisions.approve(draft.draft_id, approved_by="사람")

    assert (approved.approved_by, approved.status) == ("사람", PostStatus.READY_TO_PUBLISH)
    assert (await harness.draft(A)).approved_by == "사람"
    with pytest.raises(InvalidTransitionError, match="already approved"):  # 재승인은 발행 코루틴과 겹친다
        await harness.decisions.approve(draft.draft_id, approved_by="다른 사람")
    with pytest.raises(InvalidTransitionError):
        await harness.decisions.approve((await harness.draft(B)).draft_id, approved_by="사람")
    with pytest.raises(NotFoundError):
        await harness.decisions.approve("nope", approved_by="사람")


async def test_approve_is_refused_when_the_approval_toggle_is_off():
    harness = PipelineHarness(_fast_track())
    draft = await _ready(harness)
    decisions = DraftDecisionService(
        draft_repository=harness.drafts,
        agent_call_repository=harness.calls,
        topic_registry_repository=harness.topics,
        round_service=harness.round_service,
        settings=harness.settings.model_copy(update={"publish_requires_approval": False}),
    )

    with pytest.raises(InvalidTransitionError, match="toggle is off"):
        await decisions.approve(draft.draft_id, approved_by="사람")
    assert (await harness.draft(A)).approved_by is None


async def test_conflict_rereads_and_reapplies_when_the_premise_still_holds():
    harness = PipelineHarness(_fast_track())
    draft = await _ready(harness)
    stale_reads = {"count": 0}
    repository = harness.decisions._drafts  # noqa: SLF001 — 충돌을 흉내 내려고 get 을 감싼다
    original_get = repository.get

    async def stale_get(draft_id: str):
        current = await original_get(draft_id)
        if stale_reads["count"] == 0 and current is not None:
            stale_reads["count"] += 1
            harness.database.agent_drafts.docs[0]["revision"] += 1  # 읽은 뒤 다른 쓰기가 끼어들었다
        return current

    repository.get = stale_get  # type: ignore[method-assign]

    approved = await harness.decisions.approve(draft.draft_id, approved_by="사람")

    assert approved.approved_by == "사람" and stale_reads["count"] == 1
    assert (await harness.draft(A)).revision == draft.revision + 2


async def test_reject_releases_registry_and_closes_the_round():
    harness = PipelineHarness(_fast_track())
    draft = await _ready(harness)
    draft.escalate(EscalationReason.MAX_ITERATIONS_REACHED)
    await harness.drafts.save(draft)
    for slot in (B, C):
        await harness.set_status(await harness.draft(slot), PostStatus.REJECTED)

    rejected = await harness.decisions.resolve_escalation(
        draft.draft_id, EscalationDecisionKind.REJECT, decided_by="사람"
    )

    assert (rejected.status, rejected.rejected_by) == (PostStatus.REJECTED, "사람")
    assert (await harness.topics.get(draft.draft_id)).status == TopicRegistryStatus.RELEASED
    assert (await harness.rounds.get(draft.round_id)).status == RoundStatus.COMPLETED


async def test_revise_needs_an_instruction_and_confirm_needs_a_post_id():
    harness = PipelineHarness(_fast_track())
    draft = await _ready(harness)
    draft.escalate(EscalationReason.MAX_ITERATIONS_REACHED)
    await harness.drafts.save(draft)

    with pytest.raises(InvalidRequestError):
        await harness.decisions.resolve_escalation(draft.draft_id, EscalationDecisionKind.REVISE, decided_by="사람")
    with pytest.raises(InvalidRequestError):
        await harness.decisions.resolve_escalation(
            draft.draft_id, EscalationDecisionKind.CONFIRM_PUBLISHED, decided_by="사람"
        )
    with pytest.raises(InvalidTransitionError):  # PUBLISH_UNCERTAIN 이 아니다
        await harness.decisions.resolve_escalation(
            draft.draft_id, EscalationDecisionKind.CONFIRM_PUBLISHED, decided_by="사람", external_post_id="p"
        )
    revised = await harness.decisions.resolve_escalation(
        draft.draft_id, EscalationDecisionKind.REVISE, decided_by="사람", instruction="결론을 줄여라"
    )
    assert (revised.status, revised.human_instruction) == (PostStatus.REVISING, "결론을 줄여라")


async def test_retry_checks_token_limit_against_usage_and_resumes_the_failed_step():
    harness = PipelineHarness(_fast_track())
    draft = await _ready(harness)
    draft.mark_failed(FailureCode.BUDGET_EXCEEDED, "over")
    await harness.drafts.save(draft)
    await harness.calls.add(make_call(call_id="big", draft_id=draft.draft_id, prompt_tokens=900, completion_tokens=100))

    with pytest.raises(InvalidRequestError):
        await harness.decisions.retry(draft.draft_id, token_limit=1000)
    with pytest.raises(InvalidRequestError):
        await harness.decisions.retry(draft.draft_id, token_limit=0)

    resumed = await harness.decisions.retry(draft.draft_id, token_limit=5000)

    assert (resumed.status, resumed.token_limit, resumed.failure) == (PostStatus.READY_TO_PUBLISH, 5000, None)


async def test_discard_releases_registry_and_rejects_non_failed_drafts():
    harness = PipelineHarness(_fast_track())
    draft = await _ready(harness)
    with pytest.raises(InvalidTransitionError):
        await harness.decisions.discard(draft.draft_id, decided_by="사람")
    draft.mark_failed(FailureCode.INTERNAL_ERROR, "boom")
    await harness.drafts.save(draft)

    discarded = await harness.decisions.discard(draft.draft_id, decided_by="사람")

    assert (discarded.status, discarded.rejected_by) == (PostStatus.REJECTED, "사람")
    assert (await harness.topics.get(draft.draft_id)).status == TopicRegistryStatus.RELEASED
    assert (await harness.rounds.get(draft.round_id)).status == RoundStatus.IN_PROGRESS  # 형제 글이 남아 있다


def test_repository_type_is_the_real_one():
    assert isinstance(PipelineHarness().decisions._drafts, PostDraftRepository)  # noqa: SLF001


async def test_label_stores_a_valid_level_and_refuses_anything_else():
    # 틀린 값이 저장되면 글을 읽을 때마다 실패해 러너 스캔이 멈춘다
    harness = PipelineHarness()
    await harness.seed_round()

    draft = await harness.decisions.label_level("round-1:A", 300)
    assert draft.user_level == 300

    with pytest.raises(InvalidRequestError):
        await harness.decisions.label_level("round-1:A", 350)
    assert (await harness.draft(AgentSlot.A)).user_level == 300
