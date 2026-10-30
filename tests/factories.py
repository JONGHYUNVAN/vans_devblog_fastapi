"""테스트 데이터 생성 함수. 모델 테스트와 서비스 테스트가 함께 쓴다."""

from collections.abc import Iterable, Mapping

from app.models.enums import (
    AgentSlot,
    ArbitrationOutcome,
    IssueSeverity,
    ResolutionStatus,
    ReviewLens,
    ReviewScope,
    ReviewVerdict,
)
from app.models.post_draft import (
    Arbitration,
    DraftVersion,
    ItemCheck,
    ItemResolution,
    PeerReview,
    PostDraft,
    ReviewItem,
    VerificationChecklist,
    build_review_assignments,
)
from app.models.topic import TopicProposal
from app.services.evaluation import derive_verdict

# 기본 바인딩의 주 렌즈 (설계 03 문서 4절 표)
LENSES = {
    AgentSlot.A: ReviewLens.LOGIC_READABILITY,
    AgentSlot.B: ReviewLens.FACT_STRUCTURE,
    AgentSlot.C: ReviewLens.CRITICAL_PRACTICAL,
}
MAX_ITERATIONS = 2
MAX_VALIDATION_RETRIES = 2
MAX_PUBLISH_ATTEMPTS = 3
SECTION_IDS = ["intro", "body-1", "code-1", "tradeoff", "conclusion"]


def make_topic() -> TopicProposal:
    return TopicProposal(
        title="MongoDB 낙관적 잠금 실전",
        audience="백엔드 개발자",
        keywords=["mongodb", "concurrency", "locking"],
        outline=["문제", "해결", "트레이드오프"],
        rationale="운영에서 겪은 경쟁 조건",
        main_category_id="main-1",
        sub_category_id="sub-1",
    )


def make_draft(author: AgentSlot = AgentSlot.A, **overrides) -> PostDraft:
    fields = {
        "draft_id": f"round-1:{author}",
        "round_id": "round-1",
        "author_slot": author,
        "token_limit": 100_000,
        "topic": make_topic(),
        "reviewer_assignments": build_review_assignments(author, LENSES),
    }
    return PostDraft(**(fields | overrides))


def accept(*items: ReviewItem) -> list[ItemResolution]:
    return [ItemResolution(item_id=item.item_id, status=ResolutionStatus.ACCEPTED) for item in items]


def reject(*items: ReviewItem) -> list[ItemResolution]:
    return [
        ItemResolution(item_id=item.item_id, status=ResolutionStatus.REJECTED_WITH_REASON, reason="근거가 있다")
        for item in items
    ]


def submit(draft: PostDraft, resolutions: Iterable[ItemResolution] = ()) -> DraftVersion:
    number = len(draft.versions) + 1
    return draft.submit_version(
        title=f"제목 v{number}",
        summary="요약",
        content_markdown="<!-- sec:intro -->\n본문",
        section_ids=SECTION_IDS,
        resolutions=list(resolutions),
        author_note="",
        call_id=f"call-write-{number}",
    )


def write_next_version(draft: PostDraft, resolutions: Iterable[ItemResolution] = ()) -> DraftVersion:
    """REVISING·TOPIC_LOCKED 에서 다음 버전을 써서 게이트를 통과시킨다."""
    draft.start_drafting(MAX_ITERATIONS)
    version = submit(draft, resolutions)
    draft.pass_validation()
    return version


def make_review(
    draft: PostDraft,
    slot: AgentSlot,
    severities: Iterable[IssueSeverity] = (),
    checks: Mapping[str, bool] | None = None,
) -> PeerReview:
    """현재 버전·범위의 리뷰. severities 마다 지적 하나, checks 는 item_id → resolved."""
    version_number = draft.current_version.version_number
    scope = draft.current_review_scope
    review_id = f"{draft.draft_id}:v{version_number}:{slot}:{scope}"
    assignment = draft.assignment_of(slot)
    items = [
        ReviewItem(
            item_id=f"{review_id}:{n}",
            source_review_id=review_id,
            reviewer_slot=slot,
            section_id="body-1",
            lens=assignment.primary_lens,
            severity=severity,
            comment="지적",
            is_new_in_version=scope == ReviewScope.SPOT_CHECK,
        )
        for n, severity in enumerate(severities, start=1)
    ]
    item_checks = [
        ItemCheck(item_id=item_id, resolved=resolved, note="" if resolved else "아직 고쳐지지 않았다")
        for item_id, resolved in (checks or {}).items()
    ]
    return PeerReview(
        review_id=review_id,
        target_version=version_number,
        reviewer_slot=slot,
        lens=assignment.primary_lens,
        secondary_lens=assignment.secondary_lens,
        scope=scope,
        reported_verdict=ReviewVerdict.APPROVE if scope == ReviewScope.FULL else None,
        derived_verdict=derive_verdict(scope, items, item_checks),
        items=items,
        item_checks=item_checks,
        checklist=VerificationChecklist(checked_claims=["버전 표기 확인"]),
        overall_feedback="",
        call_id=f"call-{review_id}",
    )


def add_reviews(
    draft: PostDraft,
    severities: Mapping[AgentSlot, Iterable[IssueSeverity]] | None = None,
    resolved: Mapping[str, bool] | None = None,
) -> None:
    """필수 리뷰어 전원의 리뷰를 넣는다. SPOT_CHECK 는 resolved 에 없는 이전 항목을 해소된 것으로 본다."""
    severities = severities or {}
    resolved = resolved or {}
    for slot in draft.required_reviewers():
        checks = {item.item_id: resolved.get(item.item_id, True) for item in draft.previous_items_of(slot)}
        draft.add_review(make_review(draft, slot, severities.get(slot, ()), checks))


def make_arbitration(draft: PostDraft, item: ReviewItem, outcome: ArbitrationOutcome) -> Arbitration:
    arbiter = next(slot for slot in AgentSlot if slot not in (draft.author_slot, item.reviewer_slot))
    version_number = draft.current_version.version_number
    return Arbitration(
        arbitration_id=f"{item.item_id}:arb:v{version_number}",
        target_version=version_number,
        item_id=item.item_id,
        arbiter_slot=arbiter,
        outcome=outcome,
        reasoning="판단 근거",
        call_id=f"call-arb-{item.item_id}",
    )
