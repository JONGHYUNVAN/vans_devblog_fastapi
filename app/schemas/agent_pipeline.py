"""에이전트 내부 API 요청·응답 (설계 06 문서 3절 routers/agent_rounds.py 표). 응답은 tests/golden/agents_*.json 이 계약이다."""

import difflib
from datetime import datetime
from typing import Self

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.models.agent_call import AgentCall
from app.models.enums import (
    AgentSlot,
    ArbitrationOutcome,
    CallKind,
    EscalationDecisionKind,
    EscalationReason,
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
    PostDraft,
    Publication,
    PublicationIntent,
    ReviewAssignment,
)
from app.models.publishing_round import PublishingRound, TopicAttempt
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

    @classmethod
    def from_round(cls, publishing_round: PublishingRound) -> Self:
        return cls(
            round_id=publishing_round.round_id,
            round_seq=publishing_round.round_seq,
            status=publishing_round.status,
            theme_keyword=publishing_round.theme_keyword,
            slot_bindings=publishing_round.slot_bindings,
            draft_ids=publishing_round.draft_ids,
            topic_attempts=len(publishing_round.topic_attempts),
            abort_reason=publishing_round.abort_reason,
            created_at=publishing_round.created_at,
            updated_at=publishing_round.updated_at,
        )


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

    @classmethod
    def from_draft(cls, draft: PostDraft) -> Self:
        return cls(
            draft_id=draft.draft_id,
            round_id=draft.round_id,
            author_slot=draft.author_slot,
            status=draft.status,
            iteration=draft.iteration,
            version_count=len(draft.versions),
            title=draft.current_version.title if draft.versions else None,
            topic_title=draft.topic.title,
            revision_cause=draft.revision_cause,
            escalation_reason=draft.escalation_reason,
            failure=draft.failure,
            publish_attempts=draft.publish_attempts,
            approved_by=draft.approved_by,
            external_post_id=draft.external_post_id,
            token_limit=draft.token_limit,
            updated_at=draft.updated_at,
        )


class RoundDetail(RoundPublic):
    categories: list[CategoryOption]
    latest_attempt: TopicAttempt | None
    drafts: list[DraftPublic]

    @classmethod
    def from_round_and_drafts(cls, publishing_round: PublishingRound, drafts: list[PostDraft]) -> Self:
        return cls(
            **RoundPublic.from_round(publishing_round).model_dump(),
            categories=publishing_round.categories,
            latest_attempt=publishing_round.latest_attempt,
            drafts=[DraftPublic.from_draft(draft) for draft in drafts],
        )


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

    @classmethod
    def from_draft(cls, draft: PostDraft) -> Self:
        version = draft.publishable_version or (draft.current_version if draft.versions else None)
        previous = next(
            (each for each in reversed(draft.versions) if version and each.version_number < version.version_number),
            None,
        )
        diff = ""
        if version is not None and previous is not None:
            diff = "".join(
                difflib.unified_diff(
                    previous.content_markdown.splitlines(keepends=True),
                    version.content_markdown.splitlines(keepends=True),
                    fromfile=f"v{previous.version_number}",
                    tofile=f"v{version.version_number}",
                )
            )
        open_items = draft.open_blocking_items(draft.last_reviewed_version) if draft.last_reviewed_version else []
        resolutions = {
            resolution.item_id: resolution for each in draft.versions for resolution in each.resolutions
        }  # 마지막 버전의 해소가 남는다
        arbitrations = {arbitration.item_id: arbitration for arbitration in draft.arbitrations}

        def summarize(item) -> ItemSummary:
            resolution = resolutions.get(item.item_id)
            arbitration = arbitrations.get(item.item_id)
            return ItemSummary(
                item_id=item.item_id,
                reviewer_slot=item.reviewer_slot,
                section_id=item.section_id,
                severity=item.severity,
                comment=item.comment,
                suggested_fix=item.suggested_fix,
                resolution=resolution.status if resolution else None,
                resolution_reason=resolution.reason if resolution else None,
                arbitration=arbitration.outcome if arbitration else None,
            )

        rejected = [
            item
            for review in draft.reviews
            for item in review.items
            if item.item_id in resolutions and resolutions[item.item_id].status == ResolutionStatus.REJECTED_WITH_REASON
        ]
        return cls(
            draft_id=draft.draft_id,
            status=draft.status,
            escalation_reason=draft.escalation_reason,
            failure=draft.failure,
            publication_intent=draft.publication_intent,
            human_instruction=draft.human_instruction,
            final_version=version.version_number if version else None,
            title=version.title if version else None,
            summary=version.summary if version else None,
            content_markdown=version.content_markdown if version else None,
            diff=diff,
            open_blocking_items=[summarize(item) for item in open_items],
            rejected_items=[summarize(item) for item in rejected],
        )


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

    @classmethod
    def from_call(cls, call: AgentCall) -> Self:
        return cls(
            call_id=call.call_id,
            round_id=call.round_id,
            draft_id=call.draft_id,
            kind=call.kind,
            slot=call.slot,
            provider=call.model_binding.provider,
            model_id=call.model_binding.model_id,
            target_version=call.target_version,
            topic_attempt=call.topic_attempt,
            item_id=call.item_id,
            attempt_number=call.attempt_number,
            output_parsed_ok=call.output_parsed_ok,
            error=call.error,
            prompt_tokens=call.usage.prompt_tokens,
            completion_tokens=call.usage.completion_tokens,
            latency_ms=call.latency_ms,
            started_at=call.started_at,
            finished_at=call.finished_at,
        )
