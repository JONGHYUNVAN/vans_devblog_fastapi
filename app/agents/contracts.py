"""호출 종류별 입출력 JSON 계약 (설계 05 문서 2~4절).

출력 모델은 AgentCallService.call 의 schema 로 넘어가 LLM 응답을 파싱한다. 입력 문맥이 필요한 검사는 parsers 에 있다.
item_id·source_review_id·reviewer_slot·is_new_in_version 은 출력에 없고 parsers 가 붙인다.
"""

from typing import Literal, Self

from pydantic import BaseModel, Field, field_validator

from app.agents.personas import PersonaProfile
from app.models.enums import (
    ArbitrationOutcome,
    ClaimStatus,
    ClaimType,
    FinalReadKind,
    IssueSeverity,
    PostLevel,
    ResolutionStatus,
    ReviewLens,
    ReviewVerdict,
)
from app.models.level import LevelCriteria
from app.models.post_draft import ItemCheck, ItemResolution, ReviewItem, VerificationChecklist
from app.models.sections import REQUIRED_SECTIONS, normalize_section_id
from app.models.topic import CategoryOption, TopicProposal

TITLE_MAX_CHARS = 100  # Post BE CreatePostDto.title 상한
SUMMARY_MAX_CHARS = 150  # 발행 때 Post BE description


class StructureRules(BaseModel):
    required_sections: list[str] = Field(default_factory=lambda: list(REQUIRED_SECTIONS))
    anchor_format: str = "<!-- sec:{id} -->"
    min_chars: int
    max_chars: int
    min_code_blocks: int = 1
    language: str = "ko"
    forbidden_syntax: list[str] = Field(
        default_factory=lambda: ["HTML 태그(섹션 앵커 제외)", "이미지", "각주", "mermaid 코드 블록"]
    )


class TonePreservationRule(BaseModel):
    may_flag: list[str] = Field(
        default_factory=lambda: [
            "사실 오류",
            "논리 결함",
            "코드 버그·컨벤션 위반",
            "이해를 방해하는 문장 구조",
            "의미 없는 완충 문구·상투적 도입/마무리",
        ]
    )
    must_not_flag: list[str] = Field(
        default_factory=lambda: [
            "유머·위트",
            "1인칭 화법",
            "문장 리듬·길이 선호",
            "어조(직설·시니컬 포함)",
            "불릿 대신 서술을 택한 것 자체",
        ]
    )
    author_tone_markers: list[str]  # 페르소나의 tone_markers. 페르소나 이름은 없다


# ── PROPOSE_TOPIC ──────────────────────────────────────────────────────


class AvoidTopic(BaseModel):
    title: str
    keywords: list[str]


class ProposeTopicInput(BaseModel):
    persona: PersonaProfile
    theme_keyword: str | None
    avoid_topics: list[AvoidTopic]
    attempt_number: int
    conflict_feedback: str | None
    categories: list[CategoryOption]
    level_criteria: LevelCriteria
    evidence: str | None = None  # 라운드에 준 실제 근거. 있으면 그 기록에서 주제를 찾는다


class ProposeTopicOutput(BaseModel):
    proposals: list[TopicProposal] = Field(min_length=3, max_length=3)  # 1순위부터


# ── WRITE_DRAFT / REVISE_DRAFT ─────────────────────────────────────────


class WriteDraftInput(BaseModel):
    persona: PersonaProfile
    topic: TopicProposal
    structure_rules: StructureRules
    evidence: str | None = None  # 실제 프로젝트 근거. 경험담은 이 안의 사실로만 쓴다


class DraftOutput(BaseModel):
    title: str = Field(min_length=1, max_length=TITLE_MAX_CHARS)
    content_markdown: str  # 앵커 포함. 04 문서 8절 변환표의 문법만 쓴다
    summary: str = Field(min_length=1, max_length=SUMMARY_MAX_CHARS)


class ReviewForAuthor(BaseModel):
    reviewer_label: str  # "리뷰어 1". 모델명은 숨긴다
    lens: ReviewLens
    items: list[ReviewItem]  # 차단 항목과, FULL 리뷰였으면 비차단 항목. severity 로 구분한다
    unresolved_notes: dict[str, str]  # 이월 항목 item_id → SPOT_CHECK 리뷰어가 아직 미해소로 본 이유
    overall_feedback: str


class ArbitrationForAuthor(BaseModel):
    item_id: str
    outcome: ArbitrationOutcome
    reasoning: str
    must_accept: bool


class ReviseDraftInput(BaseModel):
    persona: PersonaProfile
    topic: TopicProposal
    structure_rules: StructureRules
    previous: DraftOutput  # 직전 제출 버전. 게이트에 떨어진 본문도 여기 들어간다
    reviews: list[ReviewForAuthor]
    arbitration_results: list[ArbitrationForAuthor]
    validation_errors: list[str]
    human_instruction: str | None = None  # 있으면 최우선이고 resolutions 는 비워도 된다
    iteration: int
    max_iterations: int
    evidence: str | None = None


class RevisionOutput(BaseModel):
    title: str = Field(min_length=1, max_length=TITLE_MAX_CHARS)
    content_markdown: str
    summary: str = Field(min_length=1, max_length=SUMMARY_MAX_CHARS)
    resolutions: list[ItemResolution]  # 차단 항목은 open_blocking_items 와 ID 가 정확히 같아야 한다 (게이트가 검사)
    author_note: str


# ── REVIEW (FULL / SPOT_CHECK) ─────────────────────────────────────────


class TopicForReviewer(BaseModel):
    """리뷰어에게 주는 주제. rationale(왜 이 성향의 작성자가 쓰는지)은 페르소나 서술이라 뺀다 (04 문서 4절 블라인드)."""

    title: str
    audience: str
    keywords: list[str]
    outline: list[str]
    core_mechanism: str
    out_of_scope: list[str]
    target_level: PostLevel | None
    prerequisites: str

    @classmethod
    def from_topic(cls, topic: TopicProposal) -> Self:
        return cls(
            title=topic.title,
            audience=topic.audience,
            keywords=topic.keywords,
            outline=topic.outline,
            core_mechanism=topic.core_mechanism,
            out_of_scope=topic.out_of_scope,
            target_level=topic.target_level,
            prerequisites=topic.prerequisites,
        )


class ReviewItemForReviewer(BaseModel):
    """SPOT_CHECK·중재에 주는 지적. reviewer_slot·source_review_id 는 뺀다. item_id 는 SPOT_CHECK 가 그대로 돌려줘야 해 유지한다."""

    item_id: str
    section_id: str
    lens: ReviewLens
    severity: IssueSeverity
    comment: str
    suggested_fix: str | None

    @classmethod
    def from_item(cls, item: ReviewItem) -> Self:
        return cls(
            item_id=item.item_id,
            section_id=item.section_id,
            lens=item.lens,
            severity=item.severity,
            comment=item.comment,
            suggested_fix=item.suggested_fix,
        )


class ReviewInput(BaseModel):
    scope: Literal["FULL"] = "FULL"
    lens: ReviewLens
    secondary_lens: ReviewLens | None
    topic: TopicForReviewer  # 독자층·목차. 저자 정보 없음
    content_markdown: str
    tone_preservation: TonePreservationRule


class ReviewItemOutput(BaseModel):
    section_id: str  # 본문 앵커 중 하나
    lens: ReviewLens  # 배정된 lens 또는 secondary_lens
    severity: IssueSeverity
    comment: str
    suggested_fix: str | None = None

    @field_validator("section_id")
    @classmethod
    def _normalize_section_id(cls, value: str) -> str:
        return normalize_section_id(value)


class ReviewOutput(BaseModel):
    verdict: ReviewVerdict  # reported_verdict 로 저장. 판정에는 쓰지 않는다
    items: list[ReviewItemOutput]
    checklist: VerificationChecklist  # 비어 있으면 스키마 위반
    overall_feedback: str = Field(max_length=1000)


class SpotCheckInput(BaseModel):
    scope: Literal["SPOT_CHECK"] = "SPOT_CHECK"
    lens: ReviewLens
    secondary_lens: ReviewLens | None
    topic: TopicForReviewer  # 작성자의 범위 밖 판단을 핵심 메커니즘에 비춰 본다
    previous_content: str
    current_content: str
    my_previous_items: list[ReviewItemForReviewer]  # 직전 리뷰 완료 버전의 open_blocking_items 중 본인이 낸 항목
    resolutions: list[ItemResolution]  # 작성자의 결정 (기각 사유 포함)
    tone_preservation: TonePreservationRule


class SpotCheckOutput(BaseModel):
    item_checks: list[ItemCheck]  # my_previous_items 전부에 대해 하나씩
    new_critical_items: list[ReviewItemOutput]  # CRITICAL 만 남긴다. 다른 심각도는 파서가 버리고 로그를 남긴다
    checklist: VerificationChecklist


# ── ARBITRATE ──────────────────────────────────────────────────────────


class ArbitrateInput(BaseModel):
    item: ReviewItemForReviewer  # 평가 렌즈는 item.lens 하나만 쓴다
    section_before: str
    section_after: str
    dispute_kind: ResolutionStatus  # REJECTED_WITH_REASON(지적이 틀렸다) | OUT_OF_SCOPE(글의 범위 밖이다)
    author_rejection_reason: str
    reviewer_unresolved_note: str
    core_mechanism: str
    out_of_scope: list[str]
    scope_declaration: str  # 본문의 범위 선언 섹션(tradeoff, 없으면 conclusion)
    search_evidence: str | None = None  # 사실 확인 지적의 분쟁이면 중재자가 검색해 모은 근거


class ArbitrateSearchInput(BaseModel):
    """사실 확인 지적의 분쟁. 판정 전에 근거만 검색한다. 응답은 글이다."""

    item: ReviewItemForReviewer
    section_after: str
    author_rejection_reason: str


class ArbitrateOutput(BaseModel):
    outcome: ArbitrationOutcome
    reasoning: str = Field(max_length=800)


# ── FACT_CHECK (사실 확인 렌즈: 검색 호출 → 변환 호출) ──────────────────


class LinkStatus(BaseModel):
    """본문 링크의 응답 코드. 네트워크 결과라 게이트에 넣지 않고 확인 입력으로만 준다. None 은 연결 실패다."""

    url: str
    status: int | None


class FactCheckInput(BaseModel):
    """검색 호출 입력. 응답은 글과 출처다. 다음 변환 호출이 계약 JSON 으로 옮긴다."""

    scope: Literal["FULL", "SPOT_CHECK"]
    lens: ReviewLens
    topic: TopicForReviewer
    content_markdown: str
    changed_section_ids: list[
        str
    ]  # SPOT_CHECK 에서 주장을 뽑을 섹션. 비면 새로 뽑지 않는다. FULL 은 본문 전체라 쓰지 않는다
    known_claims: list[str] = Field(
        default_factory=list
    )  # 이전 버전에서 이미 확인한 주장. 중재가 끝낸 지적을 되살리지 않는다
    my_previous_items: list[ReviewItemForReviewer]
    resolutions: list[ItemResolution]
    link_status: list[LinkStatus]
    tone_preservation: TonePreservationRule


class CitationRef(BaseModel):
    url: str
    title: str = ""


class FactTransformInput(BaseModel):
    scope: Literal["FULL", "SPOT_CHECK"]
    lens: ReviewLens
    section_ids: list[str]
    expected_item_ids: list[str]  # SPOT_CHECK 의 item_checks 대상
    verification_text: str
    citations: list[CitationRef]


class FactClaimOutput(BaseModel):
    section_id: str
    claim_type: ClaimType
    claim: str
    status: ClaimStatus
    source_url: str | None = None
    note: str = ""

    @field_validator("section_id")
    @classmethod
    def _normalize_section_id(cls, value: str) -> str:
        return normalize_section_id(value)


class FactCheckOutput(ReviewOutput):
    """FULL. items 는 사실이 아닌 구조 지적이고, 주장의 심각도는 코드가 정한다."""

    claims: list[FactClaimOutput]


class FactSpotCheckOutput(SpotCheckOutput):
    claims: list[FactClaimOutput]


# ── FINAL_READ ─────────────────────────────────────────────────────────


class FinalReadInput(BaseModel):
    title: str
    topic: TopicForReviewer
    content_markdown: str


class FinalReadFindingOutput(BaseModel):
    kind: FinalReadKind
    section_id: str
    quote: str = Field(min_length=1)
    comment: str

    @field_validator("section_id")
    @classmethod
    def _normalize_section_id(cls, value: str) -> str:
        return normalize_section_id(value)


class FinalReadOutput(BaseModel):
    findings: list[FinalReadFindingOutput]
    overall: str = Field(max_length=1000)


# ── ASSESS_LEVEL ───────────────────────────────────────────────────────


class AssessLevelInput(BaseModel):
    """목표 수준은 주지 않는다. 모델이 자기 목표를 그대로 돌려주면 평가가 아니다."""

    title: str
    content_markdown: str
    level_criteria: LevelCriteria


class LevelEvidenceOutput(BaseModel):
    criterion: str
    quote: str = Field(min_length=1)


class AssessLevelOutput(BaseModel):
    level: PostLevel
    evidence: list[LevelEvidenceOutput] = Field(min_length=1)
    reasoning: str = Field(max_length=1000)


# ── POLISH ─────────────────────────────────────────────────────────────


class ParagraphForPolish(BaseModel):
    index: int
    text: str


class PolishFormatInput(BaseModel):
    paragraphs: list[ParagraphForPolish]


class PolishToneInput(BaseModel):
    paragraphs: list[ParagraphForPolish]
    tone_markers: list[str]


class ParagraphPatch(BaseModel):
    index: int
    text: str


class PolishOutput(BaseModel):
    patches: list[ParagraphPatch]
