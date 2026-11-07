"""글 단위 제작 기록 (설계 08 문서 3절). 영수증에서 만들되 필요한 값을 복사해 자족적이다. 영수증이 정리돼도 남는다."""

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from app.models.agent_call import TokenUsage
from app.models.enums import AgentSlot, CallKind, PostLevel, ProvenanceStageKind, ReviewLens, ReviewScope, RevisionCause

STAGE_LABELS: dict[ProvenanceStageKind, str] = {
    ProvenanceStageKind.TOPIC: "주제 제안",
    ProvenanceStageKind.WRITE: "작성",
    ProvenanceStageKind.REVIEW: "리뷰",
    ProvenanceStageKind.FACT_CHECK: "사실 확인",
    ProvenanceStageKind.ARBITRATION: "중재",
    ProvenanceStageKind.REVISION: "수정",
    ProvenanceStageKind.POLISH: "다듬기",
    ProvenanceStageKind.FINAL_READ: "최종 읽기",
    ProvenanceStageKind.ASSESS_LEVEL: "수준 판정",
    ProvenanceStageKind.GLOSSARY: "용어 추출",
}


class ProvenanceModel(BaseModel):
    provider: str
    model_id: str
    effort: str | None = None


class ProvenanceCall(BaseModel):
    """영수증 한 건의 요약. 메시지·출력은 복사하지 않는다."""

    call_id: str
    kind: CallKind
    provider: str
    model_id: str
    effort: str | None = None
    prompt_template_version: str
    attempt_number: int
    succeeded: bool  # output_parsed_ok 이고 error 가 없다
    error: str | None = None  # 공개 응답에는 넣지 않는다 (벤더 응답을 담을 수 있다)
    usage: TokenUsage = Field(default_factory=TokenUsage)
    latency_ms: int = 0
    started_at: datetime


class ProvenanceStage(BaseModel):
    stage: ProvenanceStageKind
    label: str  # 한국어 표시 이름. 프런트가 그대로 보인다
    lens: ReviewLens | None = None  # REVIEW·FACT_CHECK
    scope: ReviewScope | None = None  # REVIEW·FACT_CHECK
    target_version: int | None = None
    item_id: str | None = None  # ARBITRATION
    models: list[ProvenanceModel] = Field(default_factory=list)  # 이 단계에 쓰인 (벤더, 모델, effort) 집합
    prompt_template_versions: list[str] = Field(default_factory=list)
    calls: list[ProvenanceCall] = Field(default_factory=list)  # 실패한 시도 포함. 시작 순
    usage: TokenUsage = Field(default_factory=TokenUsage)  # calls 합계
    outcome: str = ""  # 한 줄 결과


class ProvenanceVersion(BaseModel):
    version_number: int
    origin_cause: RevisionCause | None = None  # None=초안 | REVIEW | HUMAN | POLISH. 게이트 재작성은 직전 값을 잇는다
    model_id: str | None = None
    passed_validation: bool
    call_id: str


class PostProvenance(BaseModel):
    """post_provenance 문서."""

    model_config = ConfigDict(validate_by_name=True)

    post_id: str = Field(alias="_id")  # Post BE 게시글 id
    draft_id: str
    round_id: str
    round_seq: int
    author_slot: AgentSlot
    author_provider: str  # 발행 계정의 벤더 (D-45). 블로그 작성자 이름의 근거
    writer: ProvenanceModel  # v1 을 쓴 모델
    title: str
    published_at: datetime
    final_version: int  # 발행된 본문 버전
    authored_version: int  # 다듬기 전 발행본 (머리말의 "v1 → vN")
    versions: list[ProvenanceVersion] = Field(default_factory=list)
    iteration: int = 0
    level: PostLevel | None = None  # 평가자 라벨. 사람 경로 글은 None
    user_level: PostLevel | None = None
    prompt_template_versions: list[str] = Field(default_factory=list)  # 전체 집합
    stages: list[ProvenanceStage] = Field(default_factory=list)
    usage_total: TokenUsage = Field(default_factory=TokenUsage)
    call_count: int = 0
    failed_attempts: int = 0  # error 가 있거나 파싱 실패한 시도 수
    schema_version: int = 1
    built_at: datetime
    built_by: Literal["publish", "reconcile", "cli"]
