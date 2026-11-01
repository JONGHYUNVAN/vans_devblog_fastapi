"""글 한 편의 집합 루트 PostDraft 와 그 값 객체, 상태 전이 (설계 02 문서, 03 문서 전이표).

상태 전이는 전부 PostDraft 메서드 안에 있다. 메서드는 I/O 를 하지 않는다. LLM 호출과 저장은 서비스가 한다.
잘못된 전이(전제 상태가 아님)는 InvalidTransitionError(409), 입력이 불변식을 어기면 ValueError 다.
판정 규칙은 services/evaluation.py 의 순수 함수이고, apply_evaluation 은 그 결과(EvaluationOutcome)로 전이만 한다.
"""

from collections.abc import Mapping
from datetime import UTC, datetime
from typing import Self

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.core.exceptions import InvalidTransitionError
from app.models.enums import (
    AgentSlot,
    ArbitrationOutcome,
    EscalationDecisionKind,
    EscalationReason,
    FailureCode,
    IssueSeverity,
    PostStatus,
    ResolutionStatus,
    ReviewLens,
    ReviewScope,
    ReviewVerdict,
    RevisionCause,
    RoundAbortReason,
)
from app.models.topic import TopicProposal

_TERMINAL = frozenset({PostStatus.PUBLISHED, PostStatus.REJECTED})
# 글 코루틴이 돌지 않는 상태. 사람 결정만 이 상태를 바꾼다.
_HALTED = _TERMINAL | {PostStatus.ESCALATED, PostStatus.FAILED}

# 평가 결과로 갈 수 있는 상태 (설계 03 문서 11·12, 15~18, 20~22행)
_EVALUATION_TARGETS: dict[PostStatus, frozenset[PostStatus]] = {
    PostStatus.IN_REVIEW: frozenset({PostStatus.READY_TO_PUBLISH, PostStatus.REVISING}),
    PostStatus.IN_SPOT_CHECK: frozenset(
        {PostStatus.READY_TO_PUBLISH, PostStatus.IN_ARBITRATION, PostStatus.REVISING, PostStatus.ESCALATED}
    ),
    PostStatus.IN_ARBITRATION: frozenset({PostStatus.READY_TO_PUBLISH, PostStatus.REVISING, PostStatus.ESCALATED}),
}


def _now() -> datetime:
    return datetime.now(UTC)


class ReviewAssignment(BaseModel):
    slot: AgentSlot
    primary_lens: ReviewLens
    secondary_lens: ReviewLens | None = None

    @property
    def lenses(self) -> set[ReviewLens]:
        return {self.primary_lens} | ({self.secondary_lens} if self.secondary_lens else set())


def build_review_assignments(
    author_slot: AgentSlot, primary_lenses: Mapping[AgentSlot, ReviewLens]
) -> list[ReviewAssignment]:
    """작성자를 뺀 두 슬롯에 렌즈를 배정한다 (설계 03 문서 4절, 07 문서 D-12·D-19).

    두 리뷰어의 주 렌즈는 작성자 렌즈를 뺀 둘이다. 작성자 렌즈는 한 리뷰어의 보조 렌즈로 붙인다.
    붙이는 리뷰어는 주 렌즈가 CRITICAL_PRACTICAL 이 아닌 쪽, 둘 다 아니면 FACT_STRUCTURE 쪽이다.
    세 슬롯의 주 렌즈가 서로 다르지 않으면 ValueError.
    """
    if set(primary_lenses) != set(AgentSlot) or len(set(primary_lenses.values())) != len(AgentSlot):
        raise ValueError("every slot needs a distinct primary lens")
    reviewers = [slot for slot in AgentSlot if slot != author_slot]
    candidates = [slot for slot in reviewers if primary_lenses[slot] != ReviewLens.CRITICAL_PRACTICAL]
    if len(candidates) > 1:
        candidates = [slot for slot in candidates if primary_lenses[slot] == ReviewLens.FACT_STRUCTURE]
    secondary_holder = candidates[0]
    return [
        ReviewAssignment(
            slot=slot,
            primary_lens=primary_lenses[slot],
            secondary_lens=primary_lenses[author_slot] if slot == secondary_holder else None,
        )
        for slot in reviewers
    ]


class ReviewItem(BaseModel):
    """리뷰 지적 하나. 불변이다. 수용·기각은 다음 버전의 ItemResolution 에 있다(설계 07 문서 D-07)."""

    model_config = ConfigDict(frozen=True)

    item_id: str  # 오케스트레이터가 "{review_id}:{n}" 으로 부여. LLM 출력에는 없다
    source_review_id: str
    reviewer_slot: AgentSlot
    section_id: str  # 본문 앵커 id. intro, body-1, code-1, tradeoff, conclusion
    lens: ReviewLens  # 이 지적을 낸 관점 (리뷰어의 primary 또는 secondary 중 하나)
    severity: IssueSeverity
    comment: str
    suggested_fix: str | None = None
    is_new_in_version: bool = False  # SPOT_CHECK 신규 CRITICAL 만 True. 오케스트레이터가 설정


class ItemResolution(BaseModel):
    item_id: str
    status: ResolutionStatus
    reason: str | None = None  # REJECTED_WITH_REASON 이면 필수
    section_id_after: str | None = None  # 수정으로 섹션이 바뀌었으면 새 앵커

    @model_validator(mode="after")
    def _rejection_has_reason(self) -> Self:
        if self.status == ResolutionStatus.REJECTED_WITH_REASON and not (self.reason and self.reason.strip()):
            raise ValueError(f"resolution {self.item_id} rejects without a reason")
        return self


class ItemCheck(BaseModel):
    """SPOT_CHECK 가 직전 차단 항목마다 남기는 해소 여부."""

    item_id: str
    resolved: bool
    note: str = ""  # resolved 가 False 면 왜 아직 아닌지

    @model_validator(mode="after")
    def _unresolved_has_note(self) -> Self:
        if not self.resolved and not self.note.strip():
            raise ValueError(f"item check {self.item_id} is unresolved without a note")
        return self


class VerificationChecklist(BaseModel):
    """무엇을 확인했는지 적게 해 동조 현상을 줄인다. 세 목록이 모두 비면 스키마 위반."""

    checked_claims: list[str] = Field(default_factory=list)
    checked_code_blocks: list[str] = Field(default_factory=list)  # 검토한 코드 블록 section_id
    unverifiable: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def _at_least_one_entry(self) -> Self:
        if not (self.checked_claims or self.checked_code_blocks or self.unverifiable):
            raise ValueError("checklist is empty")
        return self


class PeerReview(BaseModel):
    review_id: str
    target_version: int
    reviewer_slot: AgentSlot
    lens: ReviewLens
    secondary_lens: ReviewLens | None = None
    scope: ReviewScope
    reported_verdict: ReviewVerdict | None = None  # FULL 만. SPOT_CHECK 는 없음
    derived_verdict: ReviewVerdict  # 오케스트레이터 계산 (services/evaluation.derive_verdict)
    items: list[ReviewItem] = Field(default_factory=list)  # SPOT_CHECK 에서는 신규 CRITICAL 만
    item_checks: list[ItemCheck] = Field(default_factory=list)  # SPOT_CHECK 전용. FULL 은 빈 목록
    checklist: VerificationChecklist
    overall_feedback: str
    call_id: str

    @model_validator(mode="after")
    def _items_belong_to_review(self) -> Self:
        lenses = {self.lens} | ({self.secondary_lens} if self.secondary_lens else set())
        for item in self.items:
            if item.source_review_id != self.review_id or item.reviewer_slot != self.reviewer_slot:
                raise ValueError(f"item {item.item_id} does not belong to review {self.review_id}")
            if item.lens not in lenses:
                raise ValueError(f"item {item.item_id} uses lens {item.lens} the reviewer was not assigned")
        if len({item.item_id for item in self.items}) != len(self.items):
            raise ValueError(f"review {self.review_id} has duplicate item ids")
        if len({check.item_id for check in self.item_checks}) != len(self.item_checks):
            raise ValueError(f"review {self.review_id} has duplicate item checks")
        if self.scope == ReviewScope.FULL:
            if self.item_checks:
                raise ValueError("FULL review has no item checks")
            if self.reported_verdict is None:
                raise ValueError("FULL review needs a reported verdict")
            if any(item.is_new_in_version for item in self.items):
                raise ValueError("FULL review items are not new in version")
        if self.scope == ReviewScope.SPOT_CHECK:
            if self.reported_verdict is not None:
                raise ValueError("SPOT_CHECK review has no reported verdict")
            if any(item.severity != IssueSeverity.CRITICAL or not item.is_new_in_version for item in self.items):
                raise ValueError("SPOT_CHECK review items must be new CRITICAL items")
        return self


class Arbitration(BaseModel):
    arbitration_id: str
    target_version: int
    item_id: str  # 교착된 ReviewItem
    arbiter_slot: AgentSlot  # author 도 reviewer 도 아닌 제3 슬롯
    outcome: ArbitrationOutcome
    reasoning: str
    call_id: str


class DraftVersion(BaseModel):
    version_number: int = Field(ge=1)
    title: str
    summary: str
    content_markdown: str
    section_ids: list[str]
    origin_cause: RevisionCause | None = (
        None  # 이 버전을 만든 시작 원인(REVIEW | HUMAN | None=초안). 게이트 재시도를 거쳐도 유지
    )
    passed_validation: bool = False
    validation_errors: list[str] = Field(default_factory=list)  # 게이트 실패 사유. 통과 버전이면 빈 목록
    resolutions: list[ItemResolution] = Field(default_factory=list)
    author_note: str = ""
    call_id: str

    def resolution_for(self, item_id: str) -> ItemResolution | None:
        return next((resolution for resolution in self.resolutions if resolution.item_id == item_id), None)


class PublicationIntent(BaseModel):
    final_version: int
    requested_at: datetime
    attempt: int = Field(ge=1)  # 이번 호출 번호. 저장 시점의 publish_attempts + 1
    failed_at: datetime | None = (
        None  # 결과가 확정된 실패를 반영한 시각. READY_TO_PUBLISH 인데 None 이면 직전 호출 결과를 모른다
    )
    external_post_id: str | None = None  # 로컬이 PUBLISHED 가 되기 전에 중단되면 여기만 남는다


class Publication(BaseModel):
    published_at: datetime
    final_version: int
    external_post_id: str  # Post BE 게시글 id. 발행 응답 또는 CONFIRM_PUBLISHED 결정에서 온다
    peer_review_note: str  # 하단 부록 마크다운. 발행 때 본문과 함께 Tiptap 으로 변환한다
    approved_by: str | None = None


class FailureRecord(BaseModel):
    from_status: PostStatus
    code: FailureCode
    message: str
    occurred_at: datetime


class EvaluationOutcome(BaseModel):
    """평가 엔진이 정한 다음 상태. apply_evaluation 이 이 결과로 전이만 한다."""

    next_status: PostStatus
    escalation_reason: EscalationReason | None = None

    @model_validator(mode="after")
    def _valid_target(self) -> Self:
        allowed = {PostStatus.READY_TO_PUBLISH, PostStatus.REVISING, PostStatus.IN_ARBITRATION, PostStatus.ESCALATED}
        if self.next_status not in allowed:
            raise ValueError(f"evaluation cannot lead to {self.next_status}")
        if (self.next_status == PostStatus.ESCALATED) != (self.escalation_reason is not None):
            raise ValueError("escalation_reason is required exactly when escalating")
        return self


class PostDraft(BaseModel):
    """agent_drafts 컬렉션 문서. 버전·리뷰·중재를 내장해 글 한 편의 이력을 한 번에 읽고 원자적으로 쓴다."""

    model_config = ConfigDict(validate_by_name=True)

    draft_id: str = Field(alias="_id")  # "{round_id}:{slot}" (결정론적 ID)
    round_id: str
    author_slot: AgentSlot
    status: PostStatus = PostStatus.TOPIC_LOCKED
    iteration: int = 0  # 리뷰 반영으로 수정을 시작할 때만 +1
    validation_retries: int = 0  # 연속 게이트 실패 횟수. 실패마다 1 증가 후 상한과 비교, 통과하면 0
    revision_cause: RevisionCause | None = None  # REVISING·DRAFTING 에서 유지. submit_version 이 지운다
    human_instruction: str | None = None  # 사람이 ESCALATED → REVISE 할 때 남긴 지시문
    escalation_reason: EscalationReason | None = None
    publish_attempts: int = 0  # 결과가 확정된 발행 실패 횟수. 사람이 발행 대기로 되돌리면 0
    failure: FailureRecord | None = None  # FAILED 재개에 필요한 직전 상태와 사유
    token_limit: int = Field(gt=0)  # 글 소프트 예산 (설계 07 문서 D-20)
    approved_by: str | None = None  # 발행 승인. 그 버전에 대한 것이라 새 버전이 제출되면 지운다
    approved_at: datetime | None = None
    topic: TopicProposal
    reviewer_assignments: list[ReviewAssignment] = Field(min_length=2, max_length=2)
    versions: list[DraftVersion] = Field(default_factory=list)  # 게이트 실패본도 남는다
    reviews: list[PeerReview] = Field(default_factory=list)
    arbitrations: list[Arbitration] = Field(default_factory=list)
    # 평가(리뷰·중재)를 마친 마지막 버전. FULL·SPOT_CHECK 를 가르고 open_blocking_items 의 기준이 된다.
    # "리뷰를 마친 이전 버전"(설계 07 문서 D-21)을 매번 이력에서 다시 계산하지 않으려고 둔다.
    last_reviewed_version: int | None = None
    publication_intent: PublicationIntent | None = None
    publication: Publication | None = None
    rejected_by: str | None = None  # REJECT·discard 결정을 내린 사람
    abort_reason: RoundAbortReason | None = None  # 라운드 중단으로 REJECTED 가 됐을 때
    revision: int = 0  # 낙관적 잠금 (설계 02 문서 6절)
    created_at: datetime = Field(default_factory=_now)
    updated_at: datetime = Field(default_factory=_now)

    @model_validator(mode="after")
    def _valid_assignments(self) -> Self:
        slots = [assignment.slot for assignment in self.reviewer_assignments]
        if self.author_slot in slots:
            raise ValueError("author cannot review their own draft")
        if len(set(slots)) != len(slots):
            raise ValueError("reviewer slots must differ")
        lenses = set().union(*(assignment.lenses for assignment in self.reviewer_assignments))
        if lenses != set(ReviewLens):
            raise ValueError("the two reviewers must cover all three lenses")
        return self

    # ── 조회 ──────────────────────────────────────────────────────────

    @property
    def is_active(self) -> bool:
        """러너가 코루틴으로 돌리는 글인지. 종결·ESCALATED·FAILED 가 아니면 참."""
        return self.status not in _HALTED

    @property
    def is_terminal(self) -> bool:
        return self.status in _TERMINAL

    @property
    def current_version(self) -> DraftVersion:
        if not self.versions:
            raise ValueError(f"draft {self.draft_id} has no version yet")
        return self.versions[-1]

    @property
    def publishable_version(self) -> DraftVersion | None:
        """발행본. 마지막 passed_validation 버전이다(설계 03 문서 23행)."""
        return next((version for version in reversed(self.versions) if version.passed_validation), None)

    @property
    def current_review_scope(self) -> ReviewScope:
        """현재 버전이 받을 리뷰 범위. 리뷰를 마친 이전 버전이 없으면 FULL 이다(설계 07 문서 D-21)."""
        return ReviewScope.FULL if self.last_reviewed_version is None else ReviewScope.SPOT_CHECK

    @property
    def has_uncertain_publication(self) -> bool:
        """직전 발행 호출의 결과를 모르는지. 다시 호출하면 중복 게시가 될 수 있다(설계 04 문서 8절 4단계)."""
        return self.publication_intent is not None and self.publication_intent.failed_at is None

    def awaits_approval(self, requires_approval: bool) -> bool:
        return self.status == PostStatus.READY_TO_PUBLISH and requires_approval and self.approved_by is None

    def assignment_of(self, slot: AgentSlot) -> ReviewAssignment:
        for assignment in self.reviewer_assignments:
            if assignment.slot == slot:
                return assignment
        raise ValueError(f"slot {slot} does not review draft {self.draft_id}")

    def reviews_of(self, version_number: int) -> list[PeerReview]:
        return [review for review in self.reviews if review.target_version == version_number]

    def find_item(self, item_id: str) -> ReviewItem:
        """원본 ReviewItem. item_id 문자열을 파싱하지 않고 리뷰에서 찾는다. 없으면 ValueError."""
        for review in self.reviews:
            for item in review.items:
                if item.item_id == item_id:
                    return item
        raise ValueError(f"unknown review item {item_id}")

    def _is_upheld_author(self, item_id: str) -> bool:
        return any(
            arbitration.item_id == item_id and arbitration.outcome == ArbitrationOutcome.UPHOLD_AUTHOR
            for arbitration in self.arbitrations
        )

    def open_blocking_items(self, version_number: int) -> list[ReviewItem]:
        """버전 리뷰·중재 종료 시점의 미결 차단 항목 (설계 04 문서 5절, 07 문서 D-23).

        FULL 버전은 차단 등급 항목, SPOT_CHECK 버전은 resolved=False 이월 항목 ∪ 신규 CRITICAL 이고,
        어느 쪽이든 UPHOLD_AUTHOR 로 종결된 항목은 뺀다. 수정 입력·게이트·다음 SPOT_CHECK 가 이 집합을 공유한다.

        현재 이력으로 다시 계산하므로 리뷰 종료 시점의 스냅샷이 아니다. 나중 버전의 중재가 UPHOLD_AUTHOR 로 끝나면
        이전 버전의 집합도 줄어든다. 흐름은 last_reviewed_version 의 집합만 쓰므로 문제없고, 이력을 보여주는 곳
        (격리 요약)은 이 점을 감안한다.
        """
        reviews = self.reviews_of(version_number)
        if any(review.scope == ReviewScope.FULL for review in reviews):
            items = [item for review in reviews for item in review.items if item.severity.is_blocking]
        else:
            carried_over = [
                self.find_item(check.item_id)
                for review in reviews
                for check in review.item_checks
                if not check.resolved
            ]
            new_criticals = [
                item for review in reviews for item in review.items if item.severity == IssueSeverity.CRITICAL
            ]
            items = carried_over + new_criticals
        return [item for item in items if not self._is_upheld_author(item.item_id)]

    def previous_items_of(self, slot: AgentSlot) -> list[ReviewItem]:
        """SPOT_CHECK 리뷰어 my_previous_items. 직전 리뷰 완료 버전의 미결 차단 항목 중 그 슬롯이 낸 것."""
        if self.last_reviewed_version is None:
            return []
        return [item for item in self.open_blocking_items(self.last_reviewed_version) if item.reviewer_slot == slot]

    def required_reviewers(self) -> list[AgentSlot]:
        """FULL 은 배정된 두 슬롯, SPOT_CHECK 는 직전 리뷰 완료 버전에 본인 미결 항목이 남은 슬롯."""
        slots = [assignment.slot for assignment in self.reviewer_assignments]
        if self.current_review_scope == ReviewScope.FULL:
            return slots
        return [slot for slot in slots if self.previous_items_of(slot)]

    def pending_reviewers(self) -> list[AgentSlot]:
        """아직 응답하지 않은 필수 리뷰어. 서비스는 이 슬롯만 호출한다(재개하면 빠진 리뷰만)."""
        scope = self.current_review_scope
        responded = {
            review.reviewer_slot
            for review in self.reviews_of(self.current_version.version_number)
            if review.scope == scope
        }
        return [slot for slot in self.required_reviewers() if slot not in responded]

    def is_review_complete(self) -> bool:
        return not self.pending_reviewers()

    def deadlocked_items(self) -> list[ReviewItem]:
        """현재 버전의 교착 항목 (설계 04 문서 5절).

        SPOT_CHECK 에서 resolved=False 이고, 이번 버전의 해소가 REJECTED_WITH_REASON 이며, CRITICAL 이고,
        이전 버전에서 중재받지 않은 항목. 신규 CRITICAL 과 ACCEPTED 인데 미해소인 항목은 교착이 아니다.
        """
        version = self.current_version
        earlier_arbitrated = {
            arbitration.item_id
            for arbitration in self.arbitrations
            if arbitration.target_version < version.version_number
        }
        deadlocked: list[ReviewItem] = []
        for review in self.reviews_of(version.version_number):
            for check in review.item_checks:
                if check.resolved or check.item_id in earlier_arbitrated:
                    continue
                resolution = version.resolution_for(check.item_id)
                if resolution is None or resolution.status != ResolutionStatus.REJECTED_WITH_REASON:
                    continue
                item = self.find_item(check.item_id)
                if item.severity == IssueSeverity.CRITICAL:
                    deadlocked.append(item)
        return deadlocked

    def pending_arbitration_items(self) -> list[ReviewItem]:
        """교착 항목 중 이번 버전 중재가 아직 없는 것. 서비스는 이 항목만 ARBITRATE 를 호출한다."""
        arbitrated = {
            arbitration.item_id
            for arbitration in self.arbitrations
            if arbitration.target_version == self.current_version.version_number
        }
        return [item for item in self.deadlocked_items() if item.item_id not in arbitrated]

    def arbitration_complete(self) -> bool:
        return not self.pending_arbitration_items()

    # ── 전이 ──────────────────────────────────────────────────────────

    def _require(self, *statuses: PostStatus) -> None:
        if self.status not in statuses:
            expected = ", ".join(statuses)
            raise InvalidTransitionError(f"draft {self.draft_id} is {self.status}, expected {expected}")

    def _require_active(self) -> None:
        if self.status in _HALTED:
            raise InvalidTransitionError(f"draft {self.draft_id} is {self.status}")

    def start_drafting(self, max_iterations: int) -> None:
        """작성자 호출 전. TOPIC_LOCKED·REVISING → DRAFTING (03 문서 2, 13a~14행).

        REVIEW 원인만 iteration 을 올리고, 이미 상한이면 ESCALATED(MAX_ITERATIONS_REACHED) 다(방어 행).
        revision_cause 는 지우지 않는다. DRAFTING 에서 재시작해도 같은 호출을 만들기 위해서다(07 문서 D-11).
        """
        self._require(PostStatus.TOPIC_LOCKED, PostStatus.REVISING)
        if self.status == PostStatus.REVISING:
            if self.revision_cause is None:
                raise ValueError(f"draft {self.draft_id} is REVISING without a revision cause")
            if self.revision_cause == RevisionCause.REVIEW:
                if self.iteration >= max_iterations:
                    self.revision_cause = None
                    self.escalate(EscalationReason.MAX_ITERATIONS_REACHED)
                    return
                self.iteration += 1
        self.status = PostStatus.DRAFTING

    def submit_version(
        self,
        *,
        title: str,
        summary: str,
        content_markdown: str,
        section_ids: list[str],
        resolutions: list[ItemResolution],
        author_note: str,
        call_id: str,
    ) -> DraftVersion:
        """작성자 응답을 새 버전으로 붙이고 VALIDATING 으로 간다 (03 문서 3행).

        새 버전의 origin_cause 는 revision_cause 로 정한다. GATE 면 직전 버전 값을 잇는다.
        revision_cause 와 이전 승인을 지운다(사람이 보지 않은 본문이 이전 승인으로 발행되면 안 된다, 07 문서 D-17).
        """
        self._require(PostStatus.DRAFTING)
        match self.revision_cause:
            case None:
                origin_cause = None
            case RevisionCause.GATE:
                origin_cause = self.current_version.origin_cause
            case cause:
                origin_cause = cause
        version = DraftVersion(
            version_number=len(self.versions) + 1,
            title=title,
            summary=summary,
            content_markdown=content_markdown,
            section_ids=section_ids,
            origin_cause=origin_cause,
            resolutions=resolutions,
            author_note=author_note,
            call_id=call_id,
        )
        self.versions.append(version)
        self.revision_cause = None
        self.approved_by = None
        self.approved_at = None
        self.status = PostStatus.VALIDATING
        return version

    def pass_validation(self) -> None:
        """게이트 통과 (03 문서 6a·6b·7행). 사람 재수정은 리뷰 없이 발행 대기, 그 외에는 FULL 또는 SPOT_CHECK."""
        self._require(PostStatus.VALIDATING)
        version = self.current_version
        version.passed_validation = True
        version.validation_errors = []
        self.validation_retries = 0
        if version.origin_cause == RevisionCause.HUMAN:
            self.status = PostStatus.READY_TO_PUBLISH
        elif self.last_reviewed_version is None:
            self.status = PostStatus.IN_REVIEW
        else:
            if not self.required_reviewers():
                # 정상 흐름에서는 못 온다. REVISING(REVIEW) 는 미결 차단 항목이 있을 때만 생기므로 SPOT_CHECK 리뷰어가
                # 적어도 하나 있다. 생기면 리뷰 0건으로 발행되는 대신 여기서 멈춘다(러너가 FAILED(INTERNAL_ERROR) 로 둔다).
                raise ValueError(f"draft {self.draft_id} enters SPOT_CHECK with no reviewer holding an open item")
            self.status = PostStatus.IN_SPOT_CHECK

    def fail_validation(self, errors: list[str], max_validation_retries: int) -> None:
        """게이트 실패 (03 문서 8·9행). 연속 실패 수를 1 올려 상한 미만이면 REVISING(GATE), 같으면 FAILED.

        iteration 과 origin_cause 는 유지한다(07 문서 D-11·D-36).
        """
        self._require(PostStatus.VALIDATING)
        if not errors:
            raise ValueError("gate failure needs at least one error")
        self.current_version.validation_errors = list(errors)
        self.validation_retries += 1
        if self.validation_retries < max_validation_retries:
            self.revision_cause = RevisionCause.GATE
            self.status = PostStatus.REVISING
        else:
            self.mark_failed(FailureCode.GATE_RETRIES_EXHAUSTED, "; ".join(errors))

    def add_review(self, review: PeerReview) -> None:
        """리뷰 하나를 저장한다 (03 문서 10행). 상태는 바뀌지 않는다. 같은 버전·슬롯·범위의 중복은 무시한다."""
        self._require(PostStatus.IN_REVIEW, PostStatus.IN_SPOT_CHECK)
        version_number = self.current_version.version_number
        scope = self.current_review_scope
        if review.target_version != version_number or review.scope != scope:
            raise ValueError(f"review {review.review_id} is not a {scope} review of v{version_number}")
        if any(
            existing.reviewer_slot == review.reviewer_slot and existing.scope == review.scope
            for existing in self.reviews_of(version_number)
        ):
            return
        if review.reviewer_slot not in self.required_reviewers():
            raise ValueError(f"slot {review.reviewer_slot} is not a required reviewer of v{version_number}")
        assignment = self.assignment_of(review.reviewer_slot)
        if (review.lens, review.secondary_lens) != (assignment.primary_lens, assignment.secondary_lens):
            raise ValueError(f"review {review.review_id} lenses differ from the assignment")
        if scope == ReviewScope.SPOT_CHECK:
            expected = {item.item_id for item in self.previous_items_of(review.reviewer_slot)}
            if {check.item_id for check in review.item_checks} != expected:
                raise ValueError(f"review {review.review_id} must check exactly its previous blocking items")
        self.reviews.append(review)

    def add_arbitration(self, arbitration: Arbitration) -> None:
        """중재 하나를 저장한다 (03 문서 19행). 상태는 바뀌지 않는다. 같은 버전·항목의 중복은 무시한다."""
        self._require(PostStatus.IN_ARBITRATION)
        version_number = self.current_version.version_number
        if arbitration.target_version != version_number:
            raise ValueError(f"arbitration {arbitration.arbitration_id} does not target v{version_number}")
        if any(
            existing.item_id == arbitration.item_id and existing.target_version == version_number
            for existing in self.arbitrations
        ):
            return
        item = next((item for item in self.deadlocked_items() if item.item_id == arbitration.item_id), None)
        if item is None:
            raise ValueError(f"item {arbitration.item_id} is not deadlocked in v{version_number}")
        if arbitration.arbiter_slot in (self.author_slot, item.reviewer_slot):
            raise ValueError("arbiter must be neither the author nor the item's reviewer")
        self.arbitrations.append(arbitration)

    def apply_evaluation(self, outcome: EvaluationOutcome) -> None:
        """평가 결과로 전이한다 (03 문서 11·12, 15~18, 20~22행). REVISING 이면 cause=REVIEW."""
        self._require(*_EVALUATION_TARGETS)
        if outcome.next_status not in _EVALUATION_TARGETS[self.status]:
            raise InvalidTransitionError(f"evaluation cannot move {self.status} to {outcome.next_status}")
        is_ready = (
            self.arbitration_complete() if self.status == PostStatus.IN_ARBITRATION else self.is_review_complete()
        )
        if not is_ready:
            raise InvalidTransitionError(f"draft {self.draft_id} has not received every response in {self.status}")
        if outcome.next_status == PostStatus.IN_ARBITRATION:
            self.status = PostStatus.IN_ARBITRATION
            return
        self.last_reviewed_version = self.current_version.version_number
        match outcome.next_status:
            case PostStatus.READY_TO_PUBLISH:
                self.status = PostStatus.READY_TO_PUBLISH
            case PostStatus.REVISING:
                self.revision_cause = RevisionCause.REVIEW
                self.status = PostStatus.REVISING
            case PostStatus.ESCALATED:
                if outcome.escalation_reason is None:  # EvaluationOutcome 검증기가 보장한다. 타입을 좁히려고 둔다
                    raise ValueError("escalation needs a reason")
                self.escalate(outcome.escalation_reason)

    def escalate(self, reason: EscalationReason) -> None:
        """사람 판단 대기로 보낸다. 종결·ESCALATED·FAILED 에서는 할 수 없다."""
        self._require_active()
        self.escalation_reason = reason
        self.status = PostStatus.ESCALATED

    def resolve_escalation(
        self,
        decision: EscalationDecisionKind,
        *,
        decided_by: str,
        instruction: str | None = None,
        publication: Publication | None = None,
    ) -> None:
        """사람 결정 (03 문서 23~25행). PUBLISH_UNCERTAIN 이면 어느 결정이든 사람이 블로그를 먼저 확인한다.

        PUBLISH_AS_IS·REVISE 는 발행 카운터와 의도를 지운다(07 문서 D-36). 발행할 버전이 없는 PUBLISH_AS_IS,
        PUBLISH_UNCERTAIN 이 아닌 CONFIRM_PUBLISHED 는 InvalidTransitionError. REVISE 에 지시문이 없거나
        CONFIRM_PUBLISHED 에 Publication 이 없으면 ValueError.
        """
        self._require(PostStatus.ESCALATED)
        match decision:
            case EscalationDecisionKind.PUBLISH_AS_IS:
                if self.publishable_version is None:
                    raise InvalidTransitionError(f"draft {self.draft_id} has no version that passed validation")
                self._reset_publishing()
                self.approved_by = decided_by
                self.approved_at = _now()
                self.status = PostStatus.READY_TO_PUBLISH
            case EscalationDecisionKind.REVISE:
                if not (instruction and instruction.strip()):
                    raise ValueError("REVISE needs an instruction")
                self._reset_publishing()
                self.human_instruction = instruction
                self.revision_cause = RevisionCause.HUMAN
                self.status = PostStatus.REVISING
            case EscalationDecisionKind.REJECT:
                self.rejected_by = decided_by
                self.status = PostStatus.REJECTED
            case EscalationDecisionKind.CONFIRM_PUBLISHED:
                if self.escalation_reason != EscalationReason.PUBLISH_UNCERTAIN:
                    raise InvalidTransitionError("CONFIRM_PUBLISHED is only for PUBLISH_UNCERTAIN")
                if publication is None:
                    raise ValueError("CONFIRM_PUBLISHED needs the publication found on the blog")
                self.publication = publication
                self.status = PostStatus.PUBLISHED

    def _reset_publishing(self) -> None:
        self.escalation_reason = None
        self.publish_attempts = 0
        self.publication_intent = None

    def mark_failed(self, code: FailureCode, message: str) -> None:
        """재시도 소진·예산 초과·내부 오류로 멈춘다 (03 문서 5·5a·9·29행). 재개 지점으로 직전 상태를 남긴다."""
        self._require_active()
        self.failure = FailureRecord(from_status=self.status, code=code, message=message, occurred_at=_now())
        self.status = PostStatus.FAILED

    def retry_from_failed(self, token_limit: int | None = None) -> None:
        """FAILED 재개 (03 문서 26행). 저장된 리뷰·중재는 그대로라 서비스는 빠진 호출만 한다.

        게이트 실패면 REVISING(GATE) 로 가고 연속 실패 수를 0 으로 둔다. 어느 사유든 새 token_limit 을 받는다.
        현재 사용량보다 큰지는 사용량을 아는 서비스가 검사한다.
        """
        self._require(PostStatus.FAILED)
        if self.failure is None:
            raise ValueError(f"draft {self.draft_id} is FAILED without a failure record")
        if token_limit is not None:
            if token_limit <= 0:
                raise ValueError("token_limit must be positive")
            self.token_limit = token_limit
        if self.failure.code == FailureCode.GATE_RETRIES_EXHAUSTED:
            self.validation_retries = 0
            self.revision_cause = RevisionCause.GATE
            self.status = PostStatus.REVISING
        else:
            self.status = self.failure.from_status
        self.failure = None

    def discard(self, decided_by: str) -> None:
        """FAILED 글 하나만 폐기한다 (03 문서 26a행). 레지스트리 RELEASED 는 서비스가 따라 쓴다."""
        self._require(PostStatus.FAILED)
        self.rejected_by = decided_by
        self.status = PostStatus.REJECTED

    def reject_by_abort(self, reason: RoundAbortReason) -> None:
        """라운드 중단 (03 문서 30행). ESCALATED·FAILED 도 대상이다. 발행 의도는 지우지 않는다(07 문서 D-34)."""
        if self.is_terminal:
            raise InvalidTransitionError(f"draft {self.draft_id} is already {self.status}")
        self.abort_reason = reason
        self.status = PostStatus.REJECTED

    def record_approval(self, approved_by: str) -> None:
        """발행 승인. 그 버전에 대한 것이다. 상태는 바뀌지 않는다."""
        self._require(PostStatus.READY_TO_PUBLISH)
        self.approved_by = approved_by
        self.approved_at = _now()

    def _require_publishable(self, requires_approval: bool) -> DraftVersion:
        self._require(PostStatus.READY_TO_PUBLISH)
        if self.awaits_approval(requires_approval):
            raise InvalidTransitionError(f"draft {self.draft_id} is waiting for approval")
        version = self.publishable_version
        if version is None:
            raise InvalidTransitionError(f"draft {self.draft_id} has no version that passed validation")
        return version

    def begin_publish(self, requires_approval: bool) -> PublicationIntent:
        """발행 호출 직전에 의도를 남긴다 (04 문서 8절 6단계). 결과를 모르는 의도가 이미 있으면 InvalidTransitionError.

        서비스는 has_uncertain_publication 을 먼저 보고 ESCALATED(PUBLISH_UNCERTAIN) 로 보낸다.
        """
        version = self._require_publishable(requires_approval)
        if self.has_uncertain_publication:
            raise InvalidTransitionError(f"draft {self.draft_id} has a publication with an unknown result")
        intent = PublicationIntent(
            final_version=version.version_number, requested_at=_now(), attempt=self.publish_attempts + 1
        )
        self.publication_intent = intent
        return intent

    def _intent_in_flight(self) -> PublicationIntent:
        """결과를 아직 반영하지 않은 발행 의도. begin_publish 뒤, fail_publish·publish 전에만 있다."""
        intent = self.publication_intent
        if intent is None or intent.failed_at is not None:
            raise InvalidTransitionError(f"draft {self.draft_id} has no publication in flight")
        return intent

    def fail_publish(self, max_publish_attempts: int) -> None:
        """결과가 확정된 발행 실패 (03 문서 28a·28행). 상한 미만이면 READY_TO_PUBLISH 로 남아 다시 호출한다."""
        self._require(PostStatus.READY_TO_PUBLISH)
        intent = self._intent_in_flight()
        intent.failed_at = _now()
        self.publish_attempts = intent.attempt
        if self.publish_attempts >= max_publish_attempts:
            self.escalate(EscalationReason.PUBLISH_FAILED)

    def publish(self, publication: Publication, requires_approval: bool) -> None:
        """발행 성공 (03 문서 27행). 승인 토글이 켜져 있으면 승인이 있어야 한다.

        begin_publish 가 남긴 의도가 있어야 하고, publication 의 버전은 그 의도의 버전(실제로 보낸 본문)과 같아야 한다.
        """
        self._require_publishable(requires_approval)
        intent = self._intent_in_flight()
        if publication.final_version != intent.final_version:
            raise InvalidTransitionError(
                f"publication final_version {publication.final_version} differs from the intent's {intent.final_version}"
            )
        self.publication = publication
        self.status = PostStatus.PUBLISHED
