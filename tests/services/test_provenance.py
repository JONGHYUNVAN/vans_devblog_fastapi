"""제작 기록: build_provenance 순수 함수(3.3절 매핑), 발행 따라 쓰기·정리(DerivedRecordsWriter), 공개 응답 (설계 08 문서 3절)."""

from datetime import UTC, datetime

import pytest

from app.core.exceptions import InvalidRequestError
from app.models.enums import (
    AgentSlot,
    ArbitrationOutcome,
    CallKind,
    EscalationDecisionKind,
    EscalationReason,
    IssueSeverity,
    PostStatus,
    ProvenanceStageKind,
    ReviewLens,
    ReviewScope,
)
from app.models.glossary import GlossaryRecord
from app.models.post_draft import FinalRead, FinalReadFinding, LevelAssessment, PolishRecord, Publication
from app.services.provenance import ProvenanceService, build_provenance, build_provenance_public
from tests.factories import (
    GLOSSARY_BODY,
    add_reviews,
    draft_output,
    finish_post_review,
    glossary_candidate,
    glossary_output,
    make_arbitration,
    make_call,
    make_draft,
    make_round,
    new_sense,
    reject,
    review_output,
    write_next_version,
)
from tests.fakes import FakePostBE, PipelineHarness

pytestmark = pytest.mark.anyio

A, B = AgentSlot.A, AgentSlot.B
T = datetime(2026, 11, 7, 9, 0, tzinfo=UTC)


def _published_draft():
    """v1 리뷰(B 의 CRITICAL) → v2 기각 → SPOT_CHECK 미해소 → 중재 UPHOLD_AUTHOR → 발행 전 단계 → 발행."""
    draft = make_draft(A)
    write_next_version(draft)
    add_reviews(draft, {B: [IssueSeverity.CRITICAL]})
    from app.services.evaluation import evaluate

    draft.apply_evaluation(evaluate(draft, 2))
    [item] = draft.open_blocking_items(1)
    write_next_version(draft, reject(item))
    add_reviews(draft, resolved={item.item_id: False})
    draft.apply_evaluation(evaluate(draft, 2))
    draft.add_arbitration(make_arbitration(draft, item, ArbitrationOutcome.UPHOLD_AUTHOR))
    draft.apply_evaluation(evaluate(draft, 2))
    draft.polish = PolishRecord(
        base_version=2, applied=1, model_ids=["fake-1"], tells_before={"C-8": 2, "D-14": 1}, tells_after={"C-8": 1}
    )
    draft.final_read = FinalRead(
        target_version=2,
        findings=[FinalReadFinding(kind="OBSERVATION", section_id="intro", quote="본문", comment="c")],
        overall="",
        call_id="call-read",
    )
    draft.level_assessment = LevelAssessment(target_version=2, level=300, criteria_version="v1", call_id="call-level")
    draft.glossary = GlossaryRecord(target_version=2, call_id="call-glossary", extracted_at=T)
    finish_post_review(draft)
    draft.status = PostStatus.PUBLISHED
    draft.publication = Publication(published_at=T, final_version=2, external_post_id="post-1", peer_review_note="")
    return draft, item


def _calls(draft, item):
    first, second = draft.versions
    review_calls = [
        make_call(
            f"call-review-{review.review_id}",
            kind=CallKind.REVIEW,
            lens=review.lens,
            target_version=review.target_version,
            started_second=3 + index,
        )
        for index, review in enumerate(draft.reviews)
    ]
    for review, call in zip(draft.reviews, review_calls, strict=True):
        review.call_id = call.call_id
    draft.arbitrations[0].call_id = "call-arb"
    return [
        make_call("call-topic-A", draft_id=None, kind=CallKind.PROPOSE_TOPIC, prompt_tokens=10, started_second=0),
        make_call("call-topic-B", draft_id=None, kind=CallKind.PROPOSE_TOPIC, slot=B, started_second=0),  # 다른 슬롯
        make_call(
            "call-write-fail",
            kind=CallKind.WRITE_DRAFT,
            output_parsed_ok=False,
            error="schema",
            target_version=1,
            started_second=1,
        ),
        make_call(
            first.call_id,
            kind=CallKind.WRITE_DRAFT,
            prompt_tokens=100,
            completion_tokens=50,
            target_version=1,
            started_second=2,
            attempt_number=2,
        ),
        *review_calls,
        make_call(second.call_id, kind=CallKind.REVISE_DRAFT, target_version=2, started_second=10),
        make_call(
            "call-arb-search", kind=CallKind.FACT_CHECK, item_id=item.item_id, target_version=2, started_second=20
        ),
        make_call("call-arb", kind=CallKind.ARBITRATE, item_id=item.item_id, target_version=2, started_second=21),
        make_call("call-polish", kind=CallKind.POLISH_TONE, target_version=2, started_second=30),
        make_call("call-read", kind=CallKind.FINAL_READ, target_version=2, started_second=31),
        make_call("call-level", kind=CallKind.ASSESS_LEVEL, target_version=2, started_second=32),
        make_call(
            "call-glossary", kind=CallKind.GLOSSARY, target_version=2, started_second=33, prompt_template_version="v4"
        ),
        make_call("call-other-draft", draft_id="round-1:B", kind=CallKind.WRITE_DRAFT, started_second=40),
    ]


def test_build_provenance_groups_receipts_into_stages_with_outcomes():
    draft, item = _published_draft()
    calls = _calls(draft, item)

    record = build_provenance(draft, make_round(), calls, built_by="cli", now=T)

    assert record.post_id == "post-1" and record.call_count == 13 and record.failed_attempts == 1
    assert (record.final_version, record.authored_version, record.level, record.iteration) == (2, 2, 300, 1)
    assert record.writer.model_id == "fake-1" and record.author_provider == "fake"
    assert record.prompt_template_versions == ["v1", "v4"]
    kinds = [stage.stage for stage in record.stages]
    assert kinds[:2] == [ProvenanceStageKind.TOPIC, ProvenanceStageKind.WRITE]
    assert kinds[-5:] == [
        ProvenanceStageKind.ARBITRATION,
        ProvenanceStageKind.POLISH,
        ProvenanceStageKind.FINAL_READ,
        ProvenanceStageKind.ASSESS_LEVEL,
        ProvenanceStageKind.GLOSSARY,
    ]
    by_kind = {stage.stage: stage for stage in record.stages}
    assert (
        by_kind[ProvenanceStageKind.TOPIC].outcome == "시도 0회" and len(by_kind[ProvenanceStageKind.TOPIC].calls) == 1
    )
    write = by_kind[ProvenanceStageKind.WRITE]
    assert [call.succeeded for call in write.calls] == [False, True] and write.outcome == "v1 게이트 통과"
    assert write.usage.prompt_tokens == 100 and write.calls[0].error == "schema"
    reviews = [stage for stage in record.stages if stage.stage == ProvenanceStageKind.REVIEW]
    # 옛 모양의 글(슬롯 리뷰어 둘)이라 FULL 리뷰는 두 건이고, SPOT_CHECK 는 미해소 항목을 낸 B 의 렌즈만이다
    assert {(stage.lens, stage.scope, stage.target_version) for stage in reviews} == {
        (ReviewLens.FACT_STRUCTURE, ReviewScope.FULL, 1),
        (ReviewLens.CRITICAL_PRACTICAL, ReviewScope.FULL, 1),
        (ReviewLens.FACT_STRUCTURE, ReviewScope.SPOT_CHECK, 2),
    }
    assert any("CRITICAL 1" in stage.outcome for stage in reviews)
    assert by_kind[ProvenanceStageKind.REVISION].outcome == "v2 게이트 통과, 수용 0·기각 1·범위 밖 0"
    arbitration = by_kind[ProvenanceStageKind.ARBITRATION]
    assert arbitration.outcome == "UPHOLD_AUTHOR" and [c.kind for c in arbitration.calls] == [
        CallKind.FACT_CHECK,
        CallKind.ARBITRATE,
    ]
    assert by_kind[ProvenanceStageKind.POLISH].outcome == "적용 1·버림 0, 문체 패턴 3→1"
    assert by_kind[ProvenanceStageKind.FINAL_READ].outcome == "지적 1건(차단 0)"
    assert by_kind[ProvenanceStageKind.ASSESS_LEVEL].outcome == "수준 300"
    assert by_kind[ProvenanceStageKind.GLOSSARY].outcome == "용어 0건(보류 0, 버림 0)"
    assert by_kind[ProvenanceStageKind.GLOSSARY].prompt_template_versions == ["v4"]
    assert "call-other-draft" not in {call.call_id for stage in record.stages for call in stage.calls}


def test_build_provenance_keeps_receipt_order_when_calls_start_at_the_same_instant():
    # 가짜 LLM 은 바로 답해 발행 전 단계 호출들의 시작 시각이 같아질 수 있다. 그때 call_id(무작위)로 순서를 가르면
    # 단계 순서가 실행마다 달라졌다(발행 따라 쓰기 테스트가 세 번에 한 번꼴로 실패)
    draft, item = _published_draft()
    calls = _calls(draft, item)
    polish, read, level, glossary = calls[-5:-1]
    read.started_at = level.started_at = glossary.started_at = polish.started_at

    record = build_provenance(draft, make_round(), calls, built_by="cli", now=T)

    assert [stage.stage for stage in record.stages][-4:] == [
        ProvenanceStageKind.POLISH,
        ProvenanceStageKind.FINAL_READ,
        ProvenanceStageKind.ASSESS_LEVEL,
        ProvenanceStageKind.GLOSSARY,
    ]


def test_build_provenance_without_receipts_falls_back_to_slot_bindings():
    draft, _item = _published_draft()

    record = build_provenance(draft, make_round(), [], built_by="reconcile", now=T)

    assert record.stages == [] and record.call_count == 0 and record.writer.model_id == "fake-1"
    assert record.built_by == "reconcile" and record.title == draft.versions[1].title


def test_public_view_drops_error_strings_and_sums_tokens():
    draft, item = _published_draft()
    record = build_provenance(draft, make_round(), _calls(draft, item), built_by="cli", now=T)

    public = build_provenance_public(record)

    assert public.total_tokens == 160 and public.prompt_tokens == 110
    write = next(stage for stage in public.stages if stage.stage == ProvenanceStageKind.WRITE)
    assert not hasattr(write.calls[0], "error") and write.calls[0].succeeded is False
    assert public.model_dump().keys() >= {"stages", "versions", "writer"} and "built_by" not in public.model_dump()


def test_unpublished_draft_cannot_have_provenance():
    draft = make_draft(A)
    with pytest.raises(ValueError, match="not published"):
        build_provenance(draft, make_round(), [], built_by="cli")


# ---------- 발행 따라 쓰기와 정리 ----------


async def _publish_with_glossary(harness: PipelineHarness):
    await harness.seed_round(with_glossary=True)
    draft = await harness.drive(A)
    draft.record_approval("사람", requires_approval=True)
    await harness.drafts.save(draft)
    return await harness.drive(A)


def _harness(post_id: str = "post-42") -> PipelineHarness:
    return PipelineHarness(
        {
            "WRITE_DRAFT": [draft_output(GLOSSARY_BODY)],
            "REVIEW_FULL": review_output(),
            "GLOSSARY": [glossary_output(glossary_candidate(new_sense=new_sense()))],
        },
        publisher=FakePostBE([post_id]),
    )


async def test_publishing_writes_glossary_and_provenance_once_and_marks_derived_at():
    harness = _harness()

    draft = await _publish_with_glossary(harness)

    assert draft.status == PostStatus.PUBLISHED
    stored = await harness.drafts.get(draft.draft_id)
    assert stored.publication is not None and stored.publication.derived_at is not None
    post_glossary = await harness.glossary_repository.get_post_glossary("post-42")
    assert post_glossary is not None and [i.sense_key for i in post_glossary.items] == ["s00001"]
    assert (await harness.glossary_repository.get_sense("s00001")).post_ids == ["post-42"]
    record = await harness.provenance_repository.get("post-42")
    assert record is not None and record.built_by == "publish" and record.draft_id == draft.draft_id
    assert [stage.stage for stage in record.stages][-1] == ProvenanceStageKind.GLOSSARY
    await harness.round_service.reconcile()
    await harness.round_service.reconcile()
    assert len(harness.database["post_provenance"].docs) == 1 and len(harness.database["post_glossary"].docs) == 1


async def test_reconcile_writes_the_derived_records_a_crashed_publish_left_behind():
    harness = _harness()
    draft = await _publish_with_glossary(harness)
    harness.database["post_provenance"].docs.clear()
    harness.database["post_glossary"].docs.clear()
    draft = await harness.drafts.get(draft.draft_id)
    draft.publication.derived_at = None
    await harness.drafts.save(draft)
    assert [each.draft_id for each in await harness.drafts.list_published_without_derived()] == [draft.draft_id]

    await harness.round_service.reconcile()

    assert (await harness.provenance_repository.get("post-42")).built_by == "reconcile"
    assert (await harness.glossary_repository.get_post_glossary("post-42")) is not None
    assert (await harness.drafts.get(draft.draft_id)).publication.derived_at is not None
    assert await harness.drafts.list_published_without_derived() == []


async def test_confirm_published_also_writes_the_derived_records():
    harness = _harness()
    await harness.seed_round(with_glossary=True)
    draft = await harness.drive(A)
    draft.record_approval("사람", requires_approval=True)
    draft.begin_publish(requires_approval=True)  # 호출 중 죽은 것처럼 의도만 남긴다
    await harness.drafts.save(draft)
    draft = await harness.drive(A)
    assert (draft.status, draft.escalation_reason) == (PostStatus.ESCALATED, EscalationReason.PUBLISH_UNCERTAIN)

    draft = await harness.decisions.resolve_escalation(
        draft.draft_id, EscalationDecisionKind.CONFIRM_PUBLISHED, decided_by="사람", external_post_id="post-7"
    )

    assert draft.status == PostStatus.PUBLISHED
    assert (await harness.provenance_repository.get("post-7")).built_by == "publish"
    assert (await harness.glossary_repository.get_post_glossary("post-7")) is not None


async def test_service_builds_once_and_rebuilds_on_demand():
    harness = _harness()
    draft = await _publish_with_glossary(harness)
    service = ProvenanceService(
        repository=harness.provenance_repository,
        draft_repository=harness.drafts,
        round_repository=harness.rounds,
        agent_call_repository=harness.calls,
    )

    kept = await service.build_for_draft(draft.draft_id)
    assert kept.built_by == "publish"
    rebuilt = await service.build_for_draft(draft.draft_id, rebuild=True, built_by="cli")
    assert rebuilt.built_by == "cli" and (await harness.provenance_repository.get("post-42")).built_by == "cli"
    public = await service.get_public("post-42")
    assert public.post_id == "post-42" and public.call_count == rebuilt.call_count
    other = await harness.draft(B)
    with pytest.raises(InvalidRequestError, match="not PUBLISHED"):
        await service.build_for_draft(other.draft_id)
