"""제작 기록 공개 API 응답 (설계 08 문서 3.5절, D-67).

저장 문서(models/provenance.py)와 따로 두는 이유는 뺄 것이 있어서다: built_by 는 운영 정보고, 호출의 error 문자열은
벤더 응답을 담을 수 있어 키 없는 공개 API 로 내보내지 않는다. 토큰은 TokenUsage 중첩 대신 prompt/completion 평면 필드로 풀어
프런트가 합계를 다시 계산하지 않게 한다. 변환은 services/provenance.py 의 build_provenance_public, 골든은
tests/golden/provenance.json 이고, 필드를 바꾸면 vans_story 의 src/types/provenance.ts 와 골든을 같이 고친다.
"""

from datetime import datetime

from pydantic import BaseModel

from app.models.enums import AgentSlot, CallKind, PostLevel, ProvenanceStageKind, ReviewLens, ReviewScope, RevisionCause


class ProvenanceModelPublic(BaseModel):
    """(벤더, 모델, effort) 한 조합."""

    provider: str
    model_id: str
    effort: str | None


class ProvenanceCallPublic(BaseModel):
    """호출 시도 하나. 실패한 시도도 들어가므로 succeeded 를 봐야 한다. 오류 문자열은 없다(모듈 docstring)."""

    call_id: str
    kind: CallKind
    provider: str
    model_id: str
    effort: str | None
    prompt_template_version: str | None
    pipeline_version: str | None
    attempt_number: int  # 같은 단계 안 재시도 순번. 실패 시도도 목록에 있다
    succeeded: bool  # 파싱 성공이고 오류 없음. 사유 문자열은 공개하지 않는다
    prompt_tokens: int
    completion_tokens: int
    latency_ms: int
    started_at: datetime


class ProvenanceStagePublic(BaseModel):
    """단계 하나. label 은 서버가 정한 한국어 표시 이름이라 프런트가 stage 열거형을 번역하지 않는다. outcome 은 한 줄 결과 문장이다."""

    stage: ProvenanceStageKind
    label: str
    lens: ReviewLens | None
    scope: ReviewScope | None
    target_version: int | None
    item_id: str | None
    models: list[ProvenanceModelPublic]
    prompt_template_versions: list[str]
    pipeline_versions: list[str]
    calls: list[ProvenanceCallPublic]
    prompt_tokens: int
    completion_tokens: int
    outcome: str


class ProvenanceVersionPublic(BaseModel):
    """글 버전 한 줄. 저장 문서의 call_id 는 내부 식별자라 뺐다."""

    version_number: int
    origin_cause: RevisionCause | None
    model_id: str | None
    passed_validation: bool


class ProvenancePublic(BaseModel):
    """GET /api/v1/agents/posts/{post_id}/provenance/ 응답. 글 끝 "제작 기록" 블록 하나를 그리는 데 필요한 전부다."""

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
    pipeline_versions: list[str]
    stages: list[ProvenanceStagePublic]
    prompt_tokens: int
    completion_tokens: int
    total_tokens: int  # prompt + completion. 서버가 더해 주어 프런트가 합계를 다시 계산하지 않는다
    call_count: int  # 실패 시도 포함
    failed_attempts: int
    schema_version: int
    built_at: datetime
