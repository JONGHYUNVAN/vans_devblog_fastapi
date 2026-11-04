"""호출 종류별 입출력 JSON 계약 (설계 05 문서 2~4절).

출력 모델은 AgentCallService.call 의 schema 로 넘어가 LLM 응답을 파싱한다. 입력 문맥이 필요한 검사는 parsers 에 있다.
item_id·source_review_id·reviewer_slot·is_new_in_version 은 출력에 없고 parsers 가 붙인다.
"""

from typing import Literal, Self

from pydantic import BaseModel, Field, field_validator

from app.agents.personas import PersonaProfile
from app.models.enums import ArbitrationOutcome, IssueSeverity, ReviewLens, ReviewVerdict
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


class ProposeTopicOutput(BaseModel):
    proposals: list[TopicProposal] = Field(min_length=3, max_length=3)  # 1순위부터


# ── WRITE_DRAFT / REVISE_DRAFT ─────────────────────────────────────────


class WriteDraftInput(BaseModel):
    persona: PersonaProfile
    topic: TopicProposal
    structure_rules: StructureRules


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

    @classmethod
    def from_topic(cls, topic: TopicProposal) -> Self:
        return cls(title=topic.title, audience=topic.audience, keywords=topic.keywords, outline=topic.outline)


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
    author_rejection_reason: str
    reviewer_unresolved_note: str


class ArbitrateOutput(BaseModel):
    outcome: ArbitrationOutcome
    reasoning: str = Field(max_length=800)
