"""PostDraft 상태 전이 테스트. 설계 03 문서 전이표의 행마다 하나 이상 둔다. DB·LLM 없음."""

from datetime import UTC, datetime

import pytest
from pydantic import ValidationError

from app.core.exceptions import InvalidTransitionError
from app.models.enums import (
    AgentSlot,
    ArbitrationOutcome,
    EscalationDecisionKind,
    EscalationReason,
    FailureCode,
    FinalReadKind,
    IssueSeverity,
    PostReviewStage,
    PostStatus,
    ResolutionStatus,
    ReviewLens,
    ReviewScope,
    RevisionCause,
    RoundAbortReason,
)
from app.models.glossary import GlossaryRecord
from app.models.post_draft import (
    EvaluationOutcome,
    FinalRead,
    FinalReadFinding,
    ItemCheck,
    ItemResolution,
    LevelAssessment,
    PeerReview,
    PolishRecord,
    PostDraft,
    Publication,
    ReviewAssignment,
    ReviewItem,
    VerificationChecklist,
    build_lens_assignments,
)
from app.services.evaluation import evaluate
from tests.factories import (
    LENSES,
    MAX_ITERATIONS,
    MAX_PUBLISH_ATTEMPTS,
    MAX_VALIDATION_RETRIES,
    accept,
    add_reviews,
    build_review_assignments,
    finish_post_review,
    make_arbitration,
    make_draft,
    make_review,
    reject,
    submit,
    write_next_version,
)

A, B, C = AgentSlot.A, AgentSlot.B, AgentSlot.C
CRITICAL, MAJOR, MINOR = IssueSeverity.CRITICAL, IssueSeverity.MAJOR, IssueSeverity.MINOR


def _apply(draft: PostDraft, max_iterations: int = MAX_ITERATIONS) -> None:
    draft.apply_evaluation(evaluate(draft, max_iterations))


def _in_review() -> PostDraft:
    draft = make_draft()
    write_next_version(draft)
    return draft


def _revising_after_full(*severities: IssueSeverity) -> tuple[PostDraft, list[ReviewItem]]:
    """v1 FULL 리뷰에서 B 가 차단 항목을 낸 뒤의 REVISING(REVIEW)."""
    draft = _in_review()
    add_reviews(draft, {B: severities or (CRITICAL,)})
    _apply(draft)
    return draft, draft.open_blocking_items(1)


def _in_spot_check(*severities: IssueSeverity, rejected: bool = False) -> tuple[PostDraft, list[ReviewItem]]:
    """v2(iteration 1) 가 게이트를 통과해 SPOT_CHECK 를 기다린다."""
    draft, items = _revising_after_full(*severities)
    write_next_version(draft, reject(*items) if rejected else accept(*items))
    return draft, items


def _in_arbitration(*severities: IssueSeverity) -> tuple[PostDraft, list[ReviewItem]]:
    """v2 가 CRITICAL 을 기각하고 B 가 받아들이지 않아 교착이다."""
    draft, items = _in_spot_check(*severities, rejected=True)
    add_reviews(draft, resolved={item.item_id: False for item in items})
    _apply(draft)
    return draft, items


def _publication(version: int = 1) -> Publication:
    return Publication(
        published_at=datetime.now(UTC), final_version=version, external_post_id="post-1", peer_review_note="노트"
    )


def _in_post_review() -> PostDraft:
    draft = _in_review()
    add_reviews(draft)
    _apply(draft)
    return draft


def _ready() -> PostDraft:
    draft = _in_post_review()
    finish_post_review(draft)
    return draft


# ── 불변식 ──────────────────────────────────────────────────────────


def test_build_review_assignments_matches_design_table():
    # 설계 03 문서 4절 표
    assert build_review_assignments(A, LENSES) == [
        ReviewAssignment(slot=B, primary_lens=ReviewLens.FACT_STRUCTURE, secondary_lens=ReviewLens.LOGIC_READABILITY),
        ReviewAssignment(slot=C, primary_lens=ReviewLens.CRITICAL_PRACTICAL),
    ]
    assert build_review_assignments(B, LENSES) == [
        ReviewAssignment(slot=A, primary_lens=ReviewLens.LOGIC_READABILITY, secondary_lens=ReviewLens.FACT_STRUCTURE),
        ReviewAssignment(slot=C, primary_lens=ReviewLens.CRITICAL_PRACTICAL),
    ]
    assert build_review_assignments(C, LENSES) == [
        ReviewAssignment(slot=A, primary_lens=ReviewLens.LOGIC_READABILITY),
        ReviewAssignment(slot=B, primary_lens=ReviewLens.FACT_STRUCTURE, secondary_lens=ReviewLens.CRITICAL_PRACTICAL),
    ]


def test_draft_rejects_repeated_primary_lenses():
    repeated = [ReviewAssignment(primary_lens=ReviewLens.FACT_STRUCTURE) for _ in range(2)]
    with pytest.raises(ValidationError, match="distinct primary lenses"):
        make_draft(reviewer_assignments=repeated)


def test_draft_rejects_author_as_reviewer():
    with pytest.raises(ValidationError, match="own draft"):
        make_draft(reviewer_assignments=build_review_assignments(B, LENSES))


def test_draft_rejects_assignments_missing_a_lens():
    assignments = [
        ReviewAssignment(slot=B, primary_lens=ReviewLens.FACT_STRUCTURE),
        ReviewAssignment(slot=C, primary_lens=ReviewLens.CRITICAL_PRACTICAL),
    ]
    with pytest.raises(ValidationError, match="all three lenses"):
        make_draft(reviewer_assignments=assignments)


def test_rejected_resolution_requires_reason():
    with pytest.raises(ValidationError, match="without a reason"):
        ItemResolution(item_id="x", status=ResolutionStatus.REJECTED_WITH_REASON, reason=" ")


def test_unresolved_item_check_requires_note():
    with pytest.raises(ValidationError, match="without a note"):
        ItemCheck(item_id="x", resolved=False)


def test_empty_checklist_is_invalid():
    with pytest.raises(ValidationError, match="checklist is empty"):
        VerificationChecklist()


def test_full_review_requires_reported_verdict():
    # 05 문서 ReviewOutput.verdict 는 필수다. 판정에는 쓰지 않지만 reported ≠ derived 지표(07 문서 4절)의 입력이다
    review = make_review(_in_review(), B)

    with pytest.raises(ValidationError, match="reported verdict"):
        PeerReview(**(review.model_dump() | {"reported_verdict": None}))


def test_full_review_items_cannot_be_new_in_version():
    fields = make_review(_in_review(), B, [MINOR]).model_dump()
    fields["items"][0]["is_new_in_version"] = True

    with pytest.raises(ValidationError, match="not new in version"):
        PeerReview(**fields)


@pytest.mark.parametrize(
    ("status", "is_active"),
    [
        (PostStatus.TOPIC_LOCKED, True),
        (PostStatus.READY_TO_PUBLISH, True),
        (PostStatus.ESCALATED, False),
        (PostStatus.FAILED, False),
        (PostStatus.PUBLISHED, False),
        (PostStatus.REJECTED, False),
    ],
)
def test_is_active_only_for_running_states(status, is_active):
    assert make_draft(status=status).is_active is is_active


# ── 2·3행: 작성 ─────────────────────────────────────────────────────


def test_start_drafting_moves_topic_locked_to_drafting_at_iteration_zero():
    draft = make_draft()

    draft.start_drafting(MAX_ITERATIONS)

    assert draft.status == PostStatus.DRAFTING
    assert draft.iteration == 0


def test_submit_version_moves_to_validating_and_clears_cause_and_approval():
    draft = make_draft()
    draft.start_drafting(MAX_ITERATIONS)
    draft.approved_by = "admin"

    version = submit(draft)

    assert draft.status == PostStatus.VALIDATING
    assert version.version_number == 1
    assert version.origin_cause is None
    assert version.passed_validation is False
    assert draft.revision_cause is None
    assert draft.approved_by is None


# ── 5·5a·29행: FAILED ───────────────────────────────────────────────


@pytest.mark.parametrize(
    "status",
    [
        PostStatus.DRAFTING,
        PostStatus.IN_REVIEW,
        PostStatus.IN_SPOT_CHECK,
        PostStatus.IN_ARBITRATION,
        PostStatus.REVISING,
    ],
)
@pytest.mark.parametrize(
    "code", [FailureCode.LLM_RETRIES_EXHAUSTED, FailureCode.BUDGET_EXCEEDED, FailureCode.INTERNAL_ERROR]
)
def test_mark_failed_records_from_status(status, code):
    draft = make_draft(status=status)

    draft.mark_failed(code, "boom")

    assert draft.status == PostStatus.FAILED
    assert draft.failure.from_status == status
    assert draft.failure.code == code


@pytest.mark.parametrize("status", [PostStatus.ESCALATED, PostStatus.FAILED, PostStatus.PUBLISHED, PostStatus.REJECTED])
def test_mark_failed_raises_when_halted(status):
    with pytest.raises(InvalidTransitionError):
        make_draft(status=status).mark_failed(FailureCode.INTERNAL_ERROR, "boom")


# ── 6a·6b·7행: 게이트 통과 ─────────────────────────────────────────


def test_pass_validation_goes_to_full_review_when_no_reviewed_version():
    draft = make_draft()
    draft.start_drafting(MAX_ITERATIONS)
    submit(draft)

    draft.pass_validation()

    assert draft.status == PostStatus.IN_REVIEW
    assert draft.current_version.passed_validation is True
    assert draft.current_review_scope == ReviewScope.FULL


def test_pass_validation_goes_to_spot_check_when_reviewed_version_exists():
    draft, _ = _in_spot_check()

    assert draft.status == PostStatus.IN_SPOT_CHECK
    assert draft.current_review_scope == ReviewScope.SPOT_CHECK


def test_pass_validation_raises_when_spot_check_has_no_reviewer():
    # 정상 흐름에서는 못 온다(REVISING(REVIEW) 는 미결 차단 항목이 있을 때만 생긴다). 상태를 강제해 가드만 확인한다.
    # 가드가 없으면 리뷰 0건인 SPOT_CHECK 가 evaluate 에서 바로 READY_TO_PUBLISH 가 된다.
    draft = _ready()
    draft.status = PostStatus.REVISING
    draft.revision_cause = RevisionCause.REVIEW
    draft.start_drafting(MAX_ITERATIONS)
    submit(draft)

    with pytest.raises(ValueError, match="no reviewer"):
        draft.pass_validation()


def test_human_revision_skips_review_and_needs_new_approval():
    # 시나리오 ⑥: 사람 REVISE → 게이트 통과 → IN_SPOT_CHECK 없이 READY_TO_PUBLISH, 이전 승인은 지워진다
    draft, items = _in_spot_check()
    add_reviews(draft, resolved={items[0].item_id: False})
    _apply(draft, max_iterations=1)
    assert draft.status == PostStatus.ESCALATED
    draft.resolve_escalation(EscalationDecisionKind.PUBLISH_AS_IS, decided_by="admin")
    draft.begin_publish(requires_approval=True)
    draft.fail_publish(max_publish_attempts=1)
    assert draft.escalation_reason == EscalationReason.PUBLISH_FAILED
    draft.resolve_escalation(EscalationDecisionKind.REVISE, decided_by="admin", instruction="결론을 줄여라")
    iteration = draft.iteration

    version = write_next_version(draft)

    assert version.origin_cause == RevisionCause.HUMAN
    assert draft.iteration == iteration
    assert draft.status == PostStatus.READY_TO_PUBLISH
    assert draft.awaits_approval(requires_approval=True)


# ── 8·9행: 게이트 실패 ─────────────────────────────────────────────


def test_fail_validation_keeps_iteration_and_origin_then_rewrite_keeps_origin():
    draft, items = _revising_after_full()
    draft.start_drafting(MAX_ITERATIONS)
    submit(draft, accept(*items))

    draft.fail_validation(["앵커 누락: tradeoff"], MAX_VALIDATION_RETRIES)

    assert draft.status == PostStatus.REVISING
    assert draft.revision_cause == RevisionCause.GATE
    assert draft.iteration == 1
    assert draft.current_version.validation_errors == ["앵커 누락: tradeoff"]
    draft.start_drafting(MAX_ITERATIONS)  # 13a행
    assert draft.iteration == 1
    rewritten = submit(draft, accept(*items))
    assert rewritten.origin_cause == RevisionCause.REVIEW


def test_rewrite_of_first_draft_still_gets_full_review():
    # 시나리오 ⑤: 게이트 실패 재작성 뒤에도 FULL, 통과하면 validation_retries=0
    draft = make_draft()
    draft.start_drafting(MAX_ITERATIONS)
    submit(draft)
    draft.fail_validation(["길이 미달"], MAX_VALIDATION_RETRIES)

    write_next_version(draft)

    assert draft.status == PostStatus.IN_REVIEW
    assert draft.current_version.version_number == 2
    assert draft.validation_retries == 0


def test_second_consecutive_gate_failure_fails_draft():
    draft = make_draft()
    draft.start_drafting(MAX_ITERATIONS)
    submit(draft)
    draft.fail_validation(["길이 미달"], MAX_VALIDATION_RETRIES)
    draft.start_drafting(MAX_ITERATIONS)
    submit(draft)

    draft.fail_validation(["길이 미달"], MAX_VALIDATION_RETRIES)

    assert draft.status == PostStatus.FAILED
    assert draft.failure.code == FailureCode.GATE_RETRIES_EXHAUSTED
    assert draft.failure.from_status == PostStatus.VALIDATING


def test_gate_failures_are_not_cumulative_across_passes():
    # 07 문서 D-36: v1·v2 의 한 번씩 실패가 합쳐져 FAILED 가 되면 안 된다
    draft = make_draft()
    draft.start_drafting(MAX_ITERATIONS)
    submit(draft)
    draft.fail_validation(["길이 미달"], MAX_VALIDATION_RETRIES)
    write_next_version(draft)
    add_reviews(draft, {B: [CRITICAL]})
    _apply(draft)
    draft.start_drafting(MAX_ITERATIONS)
    submit(draft, accept(*draft.open_blocking_items(2)))

    draft.fail_validation(["해소 누락"], MAX_VALIDATION_RETRIES)

    assert draft.status == PostStatus.REVISING


# ── 10~12행: FULL 리뷰 ─────────────────────────────────────────────


def test_add_review_keeps_status_until_complete_and_ignores_duplicates():
    draft = _in_review()
    review = make_review(draft, B, [MINOR])

    draft.add_review(review)
    draft.add_review(review)

    assert draft.status == PostStatus.IN_REVIEW
    assert len(draft.reviews) == 1
    assert draft.pending_reviewers() == [LENSES[C]]
    with pytest.raises(InvalidTransitionError, match="every response"):
        draft.apply_evaluation(EvaluationOutcome(next_status=PostStatus.POST_REVIEW))


def test_add_review_raises_when_slot_is_not_a_reviewer():
    draft = _in_review()
    review = make_review(draft, B).model_copy(update={"lens": ReviewLens.LOGIC_READABILITY})

    with pytest.raises(ValueError, match="not a required reviewer"):
        draft.add_review(review)


def test_add_review_raises_when_review_targets_other_version():
    draft = _in_review()
    review = make_review(draft, B).model_copy(update={"target_version": 2})

    with pytest.raises(ValueError, match="not a FULL review of v1"):
        draft.add_review(review)


def test_full_review_with_only_minor_items_goes_to_post_review():
    draft = _in_review()
    add_reviews(draft, {B: [MINOR], C: [IssueSeverity.NITPICK]})

    _apply(draft)

    assert draft.status == PostStatus.POST_REVIEW
    assert draft.post_review_stage == PostReviewStage.POLISH
    assert draft.last_reviewed_version == 1


def test_full_review_with_blocking_item_revises_for_review():
    draft, items = _revising_after_full(MAJOR)

    assert draft.status == PostStatus.REVISING
    assert draft.revision_cause == RevisionCause.REVIEW
    assert [item.severity for item in items] == [MAJOR]


# ── 13b·13c·14행: REVISING 출구 ────────────────────────────────────


def test_review_revision_increments_iteration_and_sets_origin():
    draft, items = _revising_after_full()

    draft.start_drafting(MAX_ITERATIONS)
    version = submit(draft, accept(*items))

    assert draft.iteration == 1
    assert version.origin_cause == RevisionCause.REVIEW


def test_review_revision_at_max_iterations_escalates():
    draft, _ = _revising_after_full()

    draft.start_drafting(max_iterations=0)

    assert draft.status == PostStatus.ESCALATED
    assert draft.escalation_reason == EscalationReason.MAX_ITERATIONS_REACHED
    assert draft.revision_cause is None


def test_human_revision_at_max_iterations_still_drafts():
    draft = _in_review()
    draft.escalate(EscalationReason.MAX_ITERATIONS_REACHED)
    draft.resolve_escalation(EscalationDecisionKind.REVISE, decided_by="admin", instruction="고쳐라")

    draft.start_drafting(max_iterations=0)

    assert draft.status == PostStatus.DRAFTING
    assert draft.iteration == 0


# ── 15~18행: SPOT_CHECK ────────────────────────────────────────────


def test_spot_check_only_asks_reviewers_with_open_items():
    draft, items = _in_spot_check()

    assert draft.required_reviewers() == [LENSES[B]]
    assert draft.previous_items_of(LENSES[B]) == items
    assert draft.previous_items_of(LENSES[C]) == []


def test_spot_check_raises_when_item_checks_do_not_match_previous_items():
    draft, _ = _in_spot_check()

    with pytest.raises(ValueError, match="exactly its previous blocking items"):
        draft.add_review(make_review(draft, B, checks={}))


def test_spot_check_with_all_items_resolved_goes_to_post_review():
    draft, _ = _in_spot_check()
    add_reviews(draft)

    _apply(draft)

    assert draft.status == PostStatus.POST_REVIEW
    assert draft.last_reviewed_version == 2


def test_spot_check_with_rejected_critical_unresolved_goes_to_arbitration_even_at_max():
    draft, items = _in_spot_check(rejected=True)
    add_reviews(draft, resolved={items[0].item_id: False})

    _apply(draft, max_iterations=1)

    assert draft.status == PostStatus.IN_ARBITRATION
    assert draft.pending_arbitration_items() == items
    assert draft.last_reviewed_version == 1


def test_spot_check_with_rejected_major_unresolved_revises_without_arbitration():
    # 설계 07 문서 Q-14 현행: MAJOR 분쟁은 중재하지 않는다
    draft, items = _in_spot_check(MAJOR, rejected=True)
    add_reviews(draft, resolved={items[0].item_id: False})

    _apply(draft)

    assert draft.status == PostStatus.REVISING


def test_spot_check_with_accepted_item_unresolved_revises_below_max():
    draft, items = _in_spot_check()
    add_reviews(draft, resolved={items[0].item_id: False})

    _apply(draft)

    assert draft.status == PostStatus.REVISING
    assert draft.revision_cause == RevisionCause.REVIEW
    assert draft.open_blocking_items(2) == items


def test_spot_check_with_unresolved_item_at_max_escalates():
    draft, items = _in_spot_check()
    add_reviews(draft, resolved={items[0].item_id: False})

    _apply(draft, max_iterations=1)

    assert draft.status == PostStatus.ESCALATED
    assert draft.escalation_reason == EscalationReason.MAX_ITERATIONS_REACHED


def test_spot_check_new_critical_is_open_in_next_version():
    draft, _ = _in_spot_check()
    add_reviews(draft, {B: [CRITICAL]})

    _apply(draft)

    assert draft.status == PostStatus.REVISING
    [new_item] = draft.open_blocking_items(2)
    assert new_item.is_new_in_version is True


# ── 19~22행: 중재 ──────────────────────────────────────────────────


def test_arbitration_waits_for_every_deadlocked_item():
    # 시나리오 ③: 교착 2건이면 item_id 별 중재 2건
    draft, items = _in_arbitration(CRITICAL, CRITICAL)
    assert len(draft.pending_arbitration_items()) == 2

    draft.add_arbitration(make_arbitration(draft, items[0], ArbitrationOutcome.UPHOLD_AUTHOR))

    assert draft.status == PostStatus.IN_ARBITRATION
    assert draft.arbitration_complete() is False
    assert draft.pending_arbitration_items() == [items[1]]
    with pytest.raises(ValueError, match="waiting for arbitrations"):
        evaluate(draft, MAX_ITERATIONS)


def test_all_upheld_author_with_nothing_else_open_goes_to_post_review():
    draft, items = _in_arbitration(CRITICAL, CRITICAL)
    for item in items:
        draft.add_arbitration(make_arbitration(draft, item, ArbitrationOutcome.UPHOLD_AUTHOR))

    _apply(draft)

    assert draft.status == PostStatus.POST_REVIEW
    assert draft.open_blocking_items(2) == []


def test_upheld_reviewer_item_stays_open_into_next_spot_check():
    # 시나리오 ④: UPHOLD_REVIEWER 판정된 v1 항목이 v3 에서도 미결 차단 항목이다 (07 문서 D-23)
    draft, items = _in_arbitration()
    draft.add_arbitration(make_arbitration(draft, items[0], ArbitrationOutcome.UPHOLD_REVIEWER))

    _apply(draft)
    assert draft.status == PostStatus.REVISING
    assert draft.open_blocking_items(2) == items
    write_next_version(draft, accept(*items))

    assert draft.status == PostStatus.IN_SPOT_CHECK
    assert draft.previous_items_of(LENSES[B]) == items


def test_item_is_arbitrated_only_once():
    # 설계 04 문서 5절. UPHOLD_REVIEWER 항목의 재기각은 게이트가 막지만, 모델도 같은 항목을 다시 중재하지 않는다
    draft, items = _in_arbitration()
    draft.add_arbitration(make_arbitration(draft, items[0], ArbitrationOutcome.UPHOLD_REVIEWER))
    _apply(draft)
    write_next_version(draft, reject(*items))
    add_reviews(draft, resolved={items[0].item_id: False})

    assert draft.pending_arbitration_items() == []
    _apply(draft)
    assert draft.status == PostStatus.ESCALATED


def test_upheld_reviewer_at_max_escalates():
    draft, items = _in_arbitration()
    draft.add_arbitration(make_arbitration(draft, items[0], ArbitrationOutcome.UPHOLD_REVIEWER))

    _apply(draft, max_iterations=1)

    assert draft.status == PostStatus.ESCALATED


def test_add_arbitration_raises_when_arbiter_is_the_reviewer():
    draft, items = _in_arbitration()
    arbitration = make_arbitration(draft, items[0], ArbitrationOutcome.UPHOLD_AUTHOR).model_copy(
        update={"arbiter_slot": B}
    )

    with pytest.raises(ValueError, match="neither the author"):
        draft.add_arbitration(arbitration)


def test_add_arbitration_raises_when_item_is_not_deadlocked():
    # 기각된 MAJOR 는 미해소여도 교착이 아니다(07 문서 Q-14 현행)
    draft, items = _in_arbitration(CRITICAL, MAJOR)
    major = next(item for item in items if item.severity == MAJOR)

    with pytest.raises(ValueError, match="not deadlocked"):
        draft.add_arbitration(make_arbitration(draft, major, ArbitrationOutcome.UPHOLD_AUTHOR))


def test_retry_from_failed_in_arbitration_keeps_saved_arbitrations():
    # 시나리오 ⑪의 중재 버전: 하나가 재시도를 소진해도 저장된 중재는 남고, 재개하면 빠진 항목만 호출한다
    draft, items = _in_arbitration(CRITICAL, CRITICAL)
    draft.add_arbitration(make_arbitration(draft, items[0], ArbitrationOutcome.UPHOLD_AUTHOR))
    draft.mark_failed(FailureCode.LLM_RETRIES_EXHAUSTED, "두 번째 중재 실패")

    draft.retry_from_failed()

    assert draft.status == PostStatus.IN_ARBITRATION
    assert len(draft.arbitrations) == 1
    assert draft.pending_arbitration_items() == [items[1]]


# ── 23~25행: 사람 결정 ─────────────────────────────────────────────


def test_publish_as_is_resets_publishing_and_records_approval():
    draft = _ready()
    draft.begin_publish(requires_approval=False)
    draft.escalate(EscalationReason.PUBLISH_UNCERTAIN)

    draft.resolve_escalation(EscalationDecisionKind.PUBLISH_AS_IS, decided_by="admin")

    assert draft.status == PostStatus.READY_TO_PUBLISH
    assert draft.approved_by == "admin"
    assert draft.publish_attempts == 0
    assert draft.publication_intent is None
    assert draft.has_uncertain_publication is False


def test_publish_as_is_raises_without_validated_version():
    draft = make_draft(status=PostStatus.DRAFTING)
    draft.escalate(EscalationReason.MAX_ITERATIONS_REACHED)

    with pytest.raises(InvalidTransitionError, match="passed validation"):
        draft.resolve_escalation(EscalationDecisionKind.PUBLISH_AS_IS, decided_by="admin")


def test_confirm_published_records_publication_only_when_uncertain():
    draft = _ready()
    draft.begin_publish(requires_approval=False)
    draft.escalate(EscalationReason.PUBLISH_UNCERTAIN)

    draft.resolve_escalation(EscalationDecisionKind.CONFIRM_PUBLISHED, decided_by="admin", publication=_publication())

    assert draft.status == PostStatus.PUBLISHED
    assert draft.publication.external_post_id == "post-1"


def test_confirm_published_raises_when_not_uncertain():
    draft = _in_review()
    draft.escalate(EscalationReason.MAX_ITERATIONS_REACHED)

    with pytest.raises(InvalidTransitionError, match="PUBLISH_UNCERTAIN"):
        draft.resolve_escalation(
            EscalationDecisionKind.CONFIRM_PUBLISHED, decided_by="admin", publication=_publication()
        )


def test_revise_decision_sets_human_cause_and_resets_publishing():
    draft = _ready()
    draft.begin_publish(requires_approval=False)
    draft.fail_publish(max_publish_attempts=1)

    draft.resolve_escalation(EscalationDecisionKind.REVISE, decided_by="admin", instruction="예제를 바꿔라")

    assert draft.status == PostStatus.REVISING
    assert draft.revision_cause == RevisionCause.HUMAN
    assert draft.human_instruction == "예제를 바꿔라"
    assert draft.publish_attempts == 0
    assert draft.publication_intent is None


def test_revise_decision_raises_without_instruction():
    draft = _in_review()
    draft.escalate(EscalationReason.MAX_ITERATIONS_REACHED)

    with pytest.raises(ValueError, match="instruction"):
        draft.resolve_escalation(EscalationDecisionKind.REVISE, decided_by="admin")


def test_reject_decision_rejects_draft():
    draft = _in_review()
    draft.escalate(EscalationReason.MAX_ITERATIONS_REACHED)

    draft.resolve_escalation(EscalationDecisionKind.REJECT, decided_by="admin")

    assert draft.status == PostStatus.REJECTED
    assert draft.rejected_by == "admin"


# ── 26·26a행: FAILED 재개·폐기 ─────────────────────────────────────


def test_retry_from_failed_returns_to_stage_and_keeps_saved_reviews():
    # 시나리오 ⑪: 리뷰 1건이 재시도를 소진해도 다른 리뷰는 남고, 재개하면 빠진 리뷰만 호출한다
    draft = _in_review()
    draft.add_review(make_review(draft, B))
    draft.mark_failed(FailureCode.LLM_RETRIES_EXHAUSTED, "C 리뷰 실패")

    draft.retry_from_failed(token_limit=200_000)

    assert draft.status == PostStatus.IN_REVIEW
    assert draft.pending_reviewers() == [LENSES[C]]
    assert draft.token_limit == 200_000
    assert draft.failure is None


def test_retry_after_gate_exhaustion_revises_with_reset_counter():
    draft = make_draft()
    draft.start_drafting(MAX_ITERATIONS)
    submit(draft)
    draft.fail_validation(["x"], max_validation_retries=1)

    draft.retry_from_failed()

    assert draft.status == PostStatus.REVISING
    assert draft.revision_cause == RevisionCause.GATE
    assert draft.validation_retries == 0


def test_retry_raises_when_token_limit_is_not_positive():
    draft = make_draft(status=PostStatus.DRAFTING)
    draft.mark_failed(FailureCode.BUDGET_EXCEEDED, "over")

    with pytest.raises(ValueError, match="positive"):
        draft.retry_from_failed(token_limit=0)


def test_discard_rejects_failed_draft():
    draft = make_draft(status=PostStatus.DRAFTING)
    draft.mark_failed(FailureCode.INTERNAL_ERROR, "bug")

    draft.discard("admin")

    assert draft.status == PostStatus.REJECTED
    assert draft.rejected_by == "admin"


# ── 27·28행: 발행 ─────────────────────────────────────────────────


def test_publish_requires_approval_when_toggle_is_on():
    draft = _ready()
    with pytest.raises(InvalidTransitionError, match="waiting for approval"):
        draft.begin_publish(requires_approval=True)

    draft.record_approval("admin", requires_approval=True)
    intent = draft.begin_publish(requires_approval=True)
    draft.publish(_publication(), requires_approval=True)

    assert intent.attempt == 1
    assert intent.final_version == 1
    assert draft.status == PostStatus.PUBLISHED


def test_approval_is_refused_when_already_approved_or_toggle_is_off():
    """재승인·토글 꺼짐 승인은 발행 코루틴과 겹치는 쓰기라 거절한다 (04 문서 1절·8절 9단계)."""
    draft = _ready()
    with pytest.raises(InvalidTransitionError, match="toggle is off"):
        draft.record_approval("admin", requires_approval=False)

    draft.record_approval("admin", requires_approval=True)

    with pytest.raises(InvalidTransitionError, match="already approved by admin"):
        draft.record_approval("other", requires_approval=True)
    assert draft.approved_by == "admin"


def test_publish_without_toggle_needs_no_approval():
    draft = _ready()
    draft.begin_publish(requires_approval=False)

    draft.publish(_publication(), requires_approval=False)

    assert draft.status == PostStatus.PUBLISHED


def test_definite_publish_failures_retry_until_limit_then_escalate():
    draft = _ready()
    for attempt in range(1, MAX_PUBLISH_ATTEMPTS):
        assert draft.begin_publish(requires_approval=False).attempt == attempt
        draft.fail_publish(MAX_PUBLISH_ATTEMPTS)
        assert draft.status == PostStatus.READY_TO_PUBLISH
        assert draft.publish_attempts == attempt

    draft.begin_publish(requires_approval=False)
    draft.fail_publish(MAX_PUBLISH_ATTEMPTS)

    assert draft.status == PostStatus.ESCALATED
    assert draft.escalation_reason == EscalationReason.PUBLISH_FAILED


def test_begin_publish_refuses_when_previous_result_is_unknown():
    # 호출 뒤 반영 전에 프로세스가 죽으면 failed_at 없는 의도가 남는다. 다시 호출하면 중복 게시가 될 수 있다.
    draft = _ready()
    draft.begin_publish(requires_approval=False)

    assert draft.has_uncertain_publication is True
    with pytest.raises(InvalidTransitionError, match="unknown result"):
        draft.begin_publish(requires_approval=False)


def test_publish_raises_when_publication_version_differs_from_intent():
    draft = _ready()
    draft.begin_publish(requires_approval=False)

    with pytest.raises(InvalidTransitionError, match="differs from the intent"):
        draft.publish(_publication(version=2), requires_approval=False)
    assert draft.status == PostStatus.READY_TO_PUBLISH


def test_publish_raises_without_publication_in_flight():
    draft = _ready()

    with pytest.raises(InvalidTransitionError, match="in flight"):
        draft.publish(_publication(), requires_approval=False)


# ── 30행: 라운드 중단 ──────────────────────────────────────────────


@pytest.mark.parametrize("halt", ["escalate", "fail"])
def test_reject_by_abort_rejects_halted_draft_and_keeps_intent(halt):
    draft = _ready()
    draft.begin_publish(requires_approval=False)
    if halt == "escalate":
        draft.escalate(EscalationReason.PUBLISH_UNCERTAIN)
    else:
        draft.mark_failed(FailureCode.INTERNAL_ERROR, "bug")

    draft.reject_by_abort(RoundAbortReason.MANUAL)

    assert draft.status == PostStatus.REJECTED
    assert draft.abort_reason == RoundAbortReason.MANUAL
    assert draft.publication_intent is not None


def test_reject_by_abort_raises_when_published():
    draft = _ready()
    draft.begin_publish(requires_approval=False)
    draft.publish(_publication(), requires_approval=False)

    with pytest.raises(InvalidTransitionError):
        draft.reject_by_abort(RoundAbortReason.MANUAL)


# ── 허용되지 않는 전이 ─────────────────────────────────────────────


@pytest.mark.parametrize(
    ("status", "action"),
    [
        (PostStatus.DRAFTING, lambda draft: draft.start_drafting(MAX_ITERATIONS)),
        (PostStatus.IN_REVIEW, lambda draft: submit(draft)),
        (PostStatus.DRAFTING, lambda draft: draft.pass_validation()),
        (PostStatus.DRAFTING, lambda draft: draft.fail_validation(["x"], MAX_VALIDATION_RETRIES)),
        (PostStatus.IN_REVIEW, lambda draft: draft.record_approval("admin", requires_approval=True)),
        (
            PostStatus.READY_TO_PUBLISH,
            lambda draft: draft.resolve_escalation(EscalationDecisionKind.REJECT, decided_by="a"),
        ),
        (PostStatus.ESCALATED, lambda draft: draft.retry_from_failed()),
        (PostStatus.ESCALATED, lambda draft: draft.discard("admin")),
        (PostStatus.ESCALATED, lambda draft: draft.escalate(EscalationReason.PUBLISH_FAILED)),
        (PostStatus.IN_REVIEW, lambda draft: draft.publish(_publication(), requires_approval=False)),
        (PostStatus.IN_REVIEW, lambda draft: draft.fail_publish(MAX_PUBLISH_ATTEMPTS)),
    ],
)
def test_transition_raises_when_status_does_not_allow_it(status, action):
    with pytest.raises(InvalidTransitionError):
        action(make_draft(status=status))


# ---------- 범위 밖 판단과 MAJOR 중재 ----------


def _out_of_scope(*items: ReviewItem) -> list[ItemResolution]:
    return [
        ItemResolution(item_id=item.item_id, status=ResolutionStatus.OUT_OF_SCOPE, reason="글의 범위 밖이다")
        for item in items
    ]


def test_out_of_scope_needs_a_reason():
    with pytest.raises(ValidationError, match="without a reason"):
        ItemResolution(item_id="x", status=ResolutionStatus.OUT_OF_SCOPE)


def test_major_disputed_as_out_of_scope_is_arbitrated_but_rejected_major_is_not():
    draft, items = _revising_after_full(MAJOR)
    write_next_version(draft, _out_of_scope(*items))
    add_reviews(draft, resolved={item.item_id: False for item in items})
    assert draft.pending_arbitration_items() == items

    other, other_items = _in_spot_check(MAJOR, rejected=True)
    add_reviews(other, resolved={item.item_id: False for item in other_items})
    assert other.pending_arbitration_items() == []


def test_uphold_author_closes_out_of_scope_major():
    draft, items = _revising_after_full(MAJOR)
    write_next_version(draft, _out_of_scope(*items))
    add_reviews(draft, resolved={item.item_id: False for item in items})
    _apply(draft)
    assert draft.status == PostStatus.IN_ARBITRATION

    draft.add_arbitration(make_arbitration(draft, items[0], ArbitrationOutcome.UPHOLD_AUTHOR))
    _apply(draft)

    assert draft.status == PostStatus.POST_REVIEW
    assert draft.open_blocking_items(2) == []


# ---------- 렌즈당 모델 하나 ----------


def test_lens_assignments_give_every_lens_a_dedicated_review():
    draft = make_draft(reviewer_assignments=build_lens_assignments())
    write_next_version(draft)

    assert draft.pending_reviewers() == list(ReviewLens)
    assert {assignment.secondary_lens for assignment in draft.reviewer_assignments} == {None}
    assert draft.checks_facts(ReviewLens.FACT_STRUCTURE) is True


def test_fact_lens_is_spot_checked_even_without_open_items():
    # 수정으로 들어온 새 주장을 확인하는 곳이 사실 확인 렌즈의 SPOT_CHECK 뿐이다
    draft = make_draft(reviewer_assignments=build_lens_assignments())
    write_next_version(draft)
    add_reviews(draft, {C: [CRITICAL]})
    _apply(draft)
    write_next_version(draft, accept(*draft.open_blocking_items(1)))

    assert draft.required_reviewers() == [ReviewLens.FACT_STRUCTURE, ReviewLens.CRITICAL_PRACTICAL]


def test_old_slot_draft_does_not_check_facts():
    assert _in_review().checks_facts(ReviewLens.FACT_STRUCTURE) is False


# ---------- 발행 전 단계 ----------


def _final_read(*kinds: FinalReadKind) -> FinalRead:
    findings = [FinalReadFinding(kind=kind, section_id="body-1", quote="본문", comment="c") for kind in kinds]
    return FinalRead(target_version=1, findings=findings, overall="총평", call_id="call-read")


def _assessment() -> LevelAssessment:
    return LevelAssessment(target_version=1, level=300, criteria_version="v1", call_id="call-level")


def test_post_review_runs_polish_read_assess_then_ready():
    draft = _in_post_review()

    draft.finish_polish(PolishRecord(base_version=1))
    assert draft.post_review_stage == PostReviewStage.FINAL_READ
    draft.record_final_read(_final_read(FinalReadKind.OBSERVATION))
    assert draft.post_review_stage == PostReviewStage.ASSESS_LEVEL
    assert draft.status == PostStatus.POST_REVIEW
    draft.record_level_assessment(_assessment())
    assert (draft.status, draft.post_review_stage) == (PostStatus.POST_REVIEW, PostReviewStage.GLOSSARY)
    draft.finish_glossary(GlossaryRecord(target_version=1, skipped="no GLOSSARY route"))

    assert draft.status == PostStatus.READY_TO_PUBLISH
    assert draft.post_review_stage == PostReviewStage.DONE
    assert draft.level_assessment is not None and draft.level_assessment.level == 300
    assert draft.glossary is not None and draft.glossary.skipped == "no GLOSSARY route"


def test_post_review_stage_cannot_be_skipped():
    draft = _in_post_review()

    with pytest.raises(InvalidTransitionError, match="at stage"):
        draft.record_final_read(_final_read())
    with pytest.raises(InvalidTransitionError, match="at stage"):
        draft.finish_glossary(GlossaryRecord(target_version=1))


@pytest.mark.parametrize("kind", [FinalReadKind.BODY_CODE_CONTRADICTION, FinalReadKind.CORE_MECHANISM_MISSING])
def test_blocking_final_read_escalates_without_assessment(kind):
    draft = _in_post_review()
    draft.finish_polish(PolishRecord(base_version=1))

    draft.record_final_read(_final_read(FinalReadKind.OBSERVATION, kind))

    assert draft.status == PostStatus.ESCALATED
    assert draft.escalation_reason == EscalationReason.FINAL_READ_FLAGGED
    assert draft.level_assessment is None


def test_escalation_decisions_work_on_final_read_flag():
    draft = _in_post_review()
    draft.finish_polish(PolishRecord(base_version=1))
    draft.record_final_read(_final_read(FinalReadKind.CORE_MECHANISM_MISSING))

    draft.resolve_escalation(EscalationDecisionKind.REVISE, decided_by="admin", instruction="모듈 코드를 넣어라")
    draft.start_drafting(MAX_ITERATIONS)
    submit(draft)
    draft.pass_validation()

    assert draft.status == PostStatus.READY_TO_PUBLISH  # 사람이 고친 본문은 이 단계를 다시 타지 않는다


def test_failed_post_review_resumes_at_the_same_stage():
    draft = _in_post_review()
    draft.finish_polish(PolishRecord(base_version=1))
    draft.mark_failed(FailureCode.LLM_RETRIES_EXHAUSTED, "읽기 실패")

    draft.retry_from_failed()

    assert (draft.status, draft.post_review_stage) == (PostStatus.POST_REVIEW, PostReviewStage.FINAL_READ)


def test_polished_version_passes_without_review_and_is_not_counted_as_authored():
    draft = _in_post_review()

    version = draft.attach_polished_version(
        content_markdown="다듬은 본문",
        section_ids=["intro"],
        record=PolishRecord(base_version=1, applied=1),
        call_id="call-polish",
    )

    assert (version.version_number, version.origin_cause, version.passed_validation) == (2, RevisionCause.POLISH, True)
    assert version.title == draft.versions[0].title
    assert draft.publishable_version == version
    assert draft.authored_publishable_version == draft.versions[0]
    assert draft.last_reviewed_version == 1
    assert draft.post_review_stage == PostReviewStage.FINAL_READ


def test_revise_drops_the_assessment_of_the_old_body():
    draft = _ready()
    draft.level_assessment = _assessment()
    draft.escalate(EscalationReason.PUBLISH_FAILED)

    draft.resolve_escalation(EscalationDecisionKind.REVISE, decided_by="admin", instruction="고쳐라")

    assert draft.level_assessment is None


def test_old_stored_draft_without_new_fields_still_loads():
    stored = _in_review().model_dump(by_alias=True)
    for field in ("post_review_stage", "final_read", "level_assessment", "polish", "glossary", "user_level"):
        del stored[field]
    for field in ("core_mechanism", "out_of_scope", "target_level", "prerequisites"):
        del stored["topic"][field]

    assert PostDraft.model_validate(stored).topic.core_mechanism == ""
