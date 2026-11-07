"""제작 기록 공개 API 응답 (설계 08 문서 3.5절). 저장 문서(models/provenance.py)에서 built_by 와 호출 오류 문자열을 뺀 것이다."""

from datetime import datetime

from pydantic import BaseModel

from app.models.enums import AgentSlot, CallKind, PostLevel, ProvenanceStageKind, ReviewLens, ReviewScope, RevisionCause


class ProvenanceModelPublic(BaseModel):
    provider: str
    model_id: str
    effort: str | None


class ProvenanceCallPublic(BaseModel):
    call_id: str
    kind: CallKind
    provider: str
    model_id: str
    effort: str | None
    prompt_template_version: str
    attempt_number: int
    succeeded: bool
    prompt_tokens: int
    completion_tokens: int
    latency_ms: int
    started_at: datetime


class ProvenanceStagePublic(BaseModel):
    stage: ProvenanceStageKind
    label: str
    lens: ReviewLens | None
    scope: ReviewScope | None
    target_version: int | None
    item_id: str | None
    models: list[ProvenanceModelPublic]
    prompt_template_versions: list[str]
    calls: list[ProvenanceCallPublic]
    prompt_tokens: int
    completion_tokens: int
    outcome: str


class ProvenanceVersionPublic(BaseModel):
    version_number: int
    origin_cause: RevisionCause | None
    model_id: str | None
    passed_validation: bool


class ProvenancePublic(BaseModel):
    post_id: str
    draft_id: str
    round_id: str
    round_seq: int
    author_slot: AgentSlot
    author_provider: str
    writer: ProvenanceModelPublic
    title: str
    published_at: datetime
    final_version: int
    authored_version: int
    versions: list[ProvenanceVersionPublic]
    iteration: int
    level: PostLevel | None
    user_level: PostLevel | None
    prompt_template_versions: list[str]
    stages: list[ProvenanceStagePublic]
    prompt_tokens: int
    completion_tokens: int
    total_tokens: int
    call_count: int
    failed_attempts: int
    schema_version: int
    built_at: datetime
