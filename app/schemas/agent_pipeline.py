"""에이전트 내부 API 요청·응답 (설계 06 문서 3절 routers/agent_rounds.py 표). 응답은 tests/golden/agents_*.json 이 계약이다.

엔티티 → 응답 변환은 services/pipeline_views.py 가 한다. 여기서는 models 의 열거형·값 객체를 필드 타입으로만 쓴다
(전체 이력 응답이 도메인 문서 그대로라 복제하면 두 벌이 된다. CONVENTIONS 1절 예외).
"""

from datetime import datetime
from typing import Self

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.models.enums import (
    AgentSlot,
    ArbitrationOutcome,
    CallKind,
    EscalationDecisionKind,
    EscalationReason,
    FailureCode,
    IssueSeverity,
    PostStatus,
    ResolutionStatus,
    RevisionCause,
    RoundAbortReason,
    RoundStatus,
)
from app.models.model_binding import ModelBinding
from app.models.post_draft import (
    Arbitration,
    DraftVersion,
    FailureRecord,
    PeerReview,
    Publication,
    PublicationIntent,
    ReviewAssignment,
)
from app.models.publishing_round import TopicAttempt
from app.models.topic import CategoryOption, TopicProposal

# ---------- 요청 ----------


class RoundCreate(BaseModel):
    theme_keyword: str | None = None
    bindings: dict[AgentSlot, ModelBinding] | None = Field(None, description="없으면 AGENT_SLOT_BINDINGS 설정을 쓴다")


class EscalationDecision(BaseModel):
    decision: EscalationDecisionKind
    decided_by: str = Field(min_length=1)
    instruction: str | None = Field(None, description="REVISE 에 필수")
    external_post_id: str | None = Field(None, description="CONFIRM_PUBLISHED 에 필수. 블로그에서 찾은 게시글 id")

    @model_validator(mode="after")
    def _required_fields(self) -> Self:
        if self.decision == EscalationDecisionKind.REVISE and not (self.instruction and self.instruction.strip()):
            raise ValueError("REVISE needs an instruction")
        if self.decision == EscalationDecisionKind.CONFIRM_PUBLISHED and not self.external_post_id:
            raise ValueError("CONFIRM_PUBLISHED needs external_post_id")
        return self


class DraftApproval(BaseModel):
    approved_by: str = Field(min_length=1)


class DraftRetry(BaseModel):
    token_limit: int | None = Field(None, gt=0, description="새 소프트 상한. 현재 사용량보다 커야 한다")


class DraftDiscard(BaseModel):
    decided_by: str = Field(min_length=1)


# ---------- 응답 ----------


class RoundPublic(BaseModel):
    round_id: str
    round_seq: int
    status: RoundStatus
    theme_keyword: str | None
    slot_bindings: dict[AgentSlot, ModelBinding]
    draft_ids: list[str]
    topic_attempts: int
    abort_reason: RoundAbortReason | None
    created_at: datetime
    updated_at: datetime


class DraftPublic(BaseModel):
    """글 요약. 라운드 상세·결정 응답·CLI 가 같이 쓴다."""

    draft_id: str
    round_id: str
    author_slot: AgentSlot
    status: PostStatus
    iteration: int
    version_count: int
    title: str | None
    topic_title: str
    revision_cause: RevisionCause | None
    escalation_reason: EscalationReason | None
    failure: FailureRecord | None
    publish_attempts: int
    approved_by: str | None
    external_post_id: str | None  # PUBLISHED 가 아니면 의도에만 남은 회수 대상 id (04 문서 8절 9단계)
    token_limit: int
    updated_at: datetime


class RoundDetail(RoundPublic):
    categories: list[CategoryOption]
    latest_attempt: TopicAttempt | None
    drafts: list[DraftPublic]


class DraftHistory(BaseModel):
    """글 전체 이력. 도메인 문서를 그대로 내보낸다 (_id 대신 draft_id)."""

    model_config = ConfigDict(from_attributes=True)

    draft_id: str
    round_id: str
    author_slot: AgentSlot
    status: PostStatus
    iteration: int
    validation_retries: int
    revision_cause: RevisionCause | None
    human_instruction: str | None
    escalation_reason: EscalationReason | None
    publish_attempts: int
    failure: FailureRecord | None
    token_limit: int
    approved_by: str | None
    approved_at: datetime | None
    topic: TopicProposal
    reviewer_assignments: list[ReviewAssignment]
    versions: list[DraftVersion]
    reviews: list[PeerReview]
    arbitrations: list[Arbitration]
    last_reviewed_version: int | None
    publication_intent: PublicationIntent | None
    publication: Publication | None
    rejected_by: str | None
    abort_reason: RoundAbortReason | None
    created_at: datetime
    updated_at: datetime


class ItemSummary(BaseModel):
    """사람용 지적 요약: 지적과 작성자의 마지막 해소, 중재 판정."""

    item_id: str
    reviewer_slot: AgentSlot
    section_id: str
    severity: IssueSeverity
    comment: str
    suggested_fix: str | None
    resolution: ResolutionStatus | None
    resolution_reason: str | None
    arbitration: ArbitrationOutcome | None


class EscalationSummary(BaseModel):
    """GET drafts/{id}/escalation/: 최종 본문과 직전 버전과의 diff, 미해소 차단 항목, 기각 항목. ESCALATED 가 아니어도 읽을 수 있다."""

    draft_id: str
    status: PostStatus
    escalation_reason: EscalationReason | None
    failure: FailureRecord | None
    publication_intent: PublicationIntent | None
    human_instruction: str | None
    final_version: int | None
    title: str | None
    summary: str | None
    content_markdown: str | None
    diff: str = Field(description="직전 버전 → 최종 버전 unified diff. 버전이 하나면 빈 문자열")
    open_blocking_items: list[ItemSummary]
    rejected_items: list[ItemSummary]


class CallPublic(BaseModel):
    """영수증 요약. 메시지 본문은 뺀다."""

    call_id: str
    round_id: str
    draft_id: str | None
    kind: CallKind
    slot: AgentSlot
    provider: str
    model_id: str
    target_version: int | None
    topic_attempt: int | None
    item_id: str | None
    attempt_number: int
    output_parsed_ok: bool
    error: str | None
    prompt_tokens: int
    completion_tokens: int
    latency_ms: int
    started_at: datetime
    finished_at: datetime


class DraftProgress(BaseModel):
    """`agents status`·`start-round --wait` 용 글 한 줄: 러너가 멈춘 이유와 토큰."""

    draft_id: str
    status: PostStatus
    awaits_approval: bool
    iteration: int
    versions: int
    title: str | None
    escalation_reason: EscalationReason | None
    failure: FailureCode | None
    external_post_id: str | None
    tokens: int


class RoundProgress(BaseModel):
    round_id: str
    status: RoundStatus
    abort_reason: RoundAbortReason | None
    topic_attempts: int
    drafts: list[DraftProgress]
