"""파이프라인 버전 문서 (설계 09 문서 2절). 프롬프트·라우트·페르소나·조절 값을 한 문서에 담는다.

문서는 append-only 다. 활성 버전은 순번이 가장 큰 버전이고(D-72), 라운드는 생성 때 버전 id 를 고정한다(D-73).
schema_version 1 은 단계 목록(draft_stages·round_stages)이 없다(이행 1a). 흐름은 아직 코드(services/draft_pipeline.py)다.
조절 값(Parameters)의 기본값은 1a 이전 코드의 Settings·상수와 같다. 기준 버전이 지금 동작을 그대로 재현해야 하기 때문이다.
"""

import hashlib
import json
from datetime import UTC, datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from app.models.enums import AgentSlot, ClaimStatus, FinalReadKind, IssueSeverity
from app.models.level import DEFAULT_LEVEL_CRITERIA, LevelCriteria
from app.models.model_binding import CallModel
from app.models.persona import PersonaProfile
from app.models.routing import LEVEL_CRITERIA_DOCUMENT_ID, SLOTS_DOCUMENT_ID

PROMPT_FAMILIES = ("claude", "gemini", "grok", "haiku")
COMMON_FAMILY = "common"
PipelineVersionSource = Literal["bootstrap", "import", "edit-prompt", "edit-param", "edit-routes"]
PromptSet = dict[str, dict[str, str]]  # 가족 → 템플릿 이름 → 본문. 평면 배치(v1~v3)는 common 에 둔다


def _now() -> datetime:
    return datetime.now(UTC)


# ---------- 조절 값 ----------


class StructureParameters(BaseModel):
    """글 구조 규칙 (05 문서 3절 StructureRules 의 기본값과 models/sections.REQUIRED_SECTIONS)."""

    required_sections: list[str] = Field(default_factory=lambda: ["intro", "body-1", "tradeoff"])
    # 수준별로 요구하지 않는 섹션. 입문(100)은 트레이드오프를 강요하지 않는다 (D-54)
    sections_optional_by_level: dict[str, list[str]] = Field(default_factory=lambda: {"100": ["tradeoff"]})
    anchor_format: str = "<!-- sec:{id} -->"
    forbidden_syntax: list[str] = Field(
        default_factory=lambda: [
            "HTML tags (except section anchors)",
            "Images (![alt](url))",
            "Footnotes",
            "mermaid code blocks",
        ]
    )
    language: str = "ko"
    # 기대 문체 (D-86). polite = 합쇼체(~습니다), plain = 해라체(~다), None = 검사하지 않는다. 운영 설정 개정은 polite 다:
    # 블로그의 사람 글 36편이 전부 합쇼체고 "~다"는 독자에게 반말로 읽힌다(2026-11-08). 프롬프트에 박지 않고
    # structure_rules 데이터로 알리며, 게이트가 다수 어미를 잰다. 코드 기본값이 None 인 이유는 테스트 픽스처가 해라체라서다
    prose_register: str | None = None

    def required_sections_for(self, level: int | None) -> list[str]:
        """글 수준별 필수 섹션. 수준이 없는 옛 글은 원래 규칙 그대로다."""
        optional = set(self.sections_optional_by_level.get(str(level), [])) if level is not None else set()
        return [section for section in self.required_sections if section not in optional]


class TonePreservationParameters(BaseModel):
    """리뷰어의 톤 보존 규칙 (05 문서 4절). v4 문구는 영어다."""

    may_flag: list[str] = Field(
        default_factory=lambda: [
            "Factual errors or specification mismatches",
            "Logical leaps or broken causal links",
            "Code bugs, syntax errors, or body-code contradictions",
            "Sentence structures that obstruct comprehension",
            "Hollow filler phrases or cliched AI introductions/summaries",
        ]
    )
    must_not_flag: list[str] = Field(
        default_factory=lambda: [
            "First-person narrative choice",
            "Sentence rhythm and length preferences",
            "Direct or assertive tone",
            "Choosing prose paragraphs over bullet lists",
        ]
    )


class ReviewParameters(BaseModel):
    """리뷰어 입력의 조절 값. 지금은 톤 보존 목록뿐이다."""

    tone_preservation: TonePreservationParameters = Field(default_factory=TonePreservationParameters)


class FactCheckParameters(BaseModel):
    """주장 판정 → 지적 심각도 (D-56). 표에 없는 판정(SUPPORTED)은 지적이 아니다. SPOT_CHECK 는 CRITICAL 만 남긴다."""

    severity: dict[ClaimStatus, IssueSeverity] = Field(
        default_factory=lambda: {
            ClaimStatus.REFUTED: IssueSeverity.CRITICAL,
            ClaimStatus.UNVERIFIABLE: IssueSeverity.MINOR,
        }
    )


class FinalReadParameters(BaseModel):
    """전체 읽기에서 격리(FINAL_READ_FLAGGED)로 보내는 지적 종류 (D-50)."""

    blocking_kinds: list[FinalReadKind] = Field(
        default_factory=lambda: [FinalReadKind.BODY_CODE_CONTRADICTION, FinalReadKind.CORE_MECHANISM_MISSING]
    )


class TellPattern(BaseModel):
    """다듬기 전에 코드가 찾는 AI 문체 패턴 하나 (D-90). id 는 epoko77-ai/im-not-ai(MIT) 분류를 따르고, T-1 만 이 블로그 글에서 뽑았다.

    min_count: 글 전체(문단 산문, 인라인 코드 제외)에서 이 수 이상 나와야 모델에 알린다. 기본 패턴은 모두 1 이다.
    """

    id: str
    label: str
    pattern: str
    min_count: int = 1


# 이 블로그의 사람 글(35편)보다 AI 글에 더 많이 나오는 패턴만 둔다. 사람 글에도 같은 만큼 나오는 패턴을 지우게 하면
# 다듬기가 블로그 주인의 문체를 지운다. 2026-11-09 측정(1000어절당 사람 대 AI)에서 부정 대구 C-8(2.69 대 3.07)은 비슷했고,
# A-7·A-8·A-9·D-1 은 사람 글이 더 많아 뺐다. D-8 은 비슷했지만 사용자가 어색하다고 확인한 문장이라 남긴다 (D-90 개정)
DEFAULT_TELL_PATTERNS = (
    TellPattern(
        id="D-14", label="무거운 비유", pattern=r"봉합|청사진|적신호|경고등|신호탄|(?:구조|설계)(?:의)? ?신호|붙잡"
    ),
    TellPattern(id="D-8", label="분열문(핵심은·결정은 ~)", pattern=r"(?:핵심|관건|요점|결정|필요한 것|중요한 것)은 "),
    TellPattern(id="D-10", label="도치 결산(~하는 이유입니다)", pattern=r"이유(?:입니다|이다|다)\."),
    TellPattern(id="T-1", label="영어식 서술어(따라갑니다)", pattern=r"따라갑니다|따라가 ?(?:봅니다|보겠습니다)"),
)


class PolishParameters(BaseModel):
    """다듬기 정규식과 허용폭 (04 문서 5절 안전장치). 1a 는 지금 값 그대로다. 정규식 삭제는 1b(D-81)."""

    phrase_fixes: list[tuple[str, str]] = Field(
        default_factory=lambda: [
            ("하십시오", "한다"),
            (r"(?<=\S)라고 볼 수 있다", "다"),
            (r"(?<=\S)인 상황이다", "이다"),
        ]
    )
    long_paragraph_chars: int = 300  # 정규식이 고칠 수 없어 사람에게 알린다
    length_slack_chars: int = 30  # 문체 패치의 길이 변화 허용폭
    length_slack_ratio: float = 0.25
    # AI 문체 패턴 탐지 (D-90). 고치지 않고 찾기만 해서 다듬기 입력의 findings 로 준다. id 는 polish_tone 프롬프트의 규칙 id 다
    tell_patterns: list[TellPattern] = Field(
        default_factory=lambda: [each.model_copy() for each in DEFAULT_TELL_PATTERNS]
    )


NOTE_LINE_FIELDS = ("writer", "reviewers", "revisers", "arbiters", "polishers")


class PublishNoteParameters(BaseModel):
    """발행 본문 끝 "AI Information" 표기의 문구 (09 문서 D-80·D-87). 어느 줄을 넣을지는 build_publish_note 코드다.

    줄마다 "역할: 모델" 한 줄이고 $models 를 string.Template 으로 치환한다. 버전 수·지적 건수·기각 인용은 넣지 않는다.
    그 이력은 제작 기록 블록(08 문서 D-67)이 보이므로 글 끝에서 되풀이하지 않는다(사용자 결정 2026-11-07·11-08).
    옛 버전 문서의 긴 머리말 필드(intro·versions_line 등)는 extra 로 무시된다.
    """

    heading: str = "## AI Information"
    writer: str = "- 작성: $models"
    reviewers: str = "- 리뷰: $models"  # 모델(렌즈, 렌즈) 꼴. 같은 모델이 두 렌즈를 맡으면 한 번만 적는다
    revisers: str = "- 수정: $models"  # 작성 모델과 다른 모델이 고쳤을 때만
    arbiters: str = "- 중재: $models"  # 중재가 있었을 때만
    polishers: str = "- 다듬기: $models"  # 작성 모델과 다른 모델의 다듬기 패치가 적용됐을 때만(D-91 전 글)
    polish_reviewers: str = "- 다듬기 검토: $models"  # 작성 모델이 다듬기 제안을 보고 고쳤을 때 제안한 모델 (D-91)
    lens_labels: dict[str, str] = Field(
        default_factory=lambda: {
            "LOGIC_READABILITY": "논리·가독성",
            "FACT_STRUCTURE": "사실·구조",
            "CRITICAL_PRACTICAL": "비판·실무",
        }
    )


class PublishParameters(BaseModel):
    """발행 조절 값: 승인 토글과 글 끝 AI Information 문구. 토글은 라운드가 고정한 버전에서 읽으므로 환경 변수로는 바꾸지 않는다(D-73)."""

    requires_approval: bool = True  # 켜져 있으면 READY_TO_PUBLISH 에서 사람 승인을 기다린다 (D-17)
    note: PublishNoteParameters = Field(default_factory=PublishNoteParameters)


class GlossaryParameters(BaseModel):
    """용어 추출 값 (08 문서 2.3절)."""

    full_list_max: int = 300  # 이 수까지는 표제어 전체를 추출 입력에 넣는다
    min_terms: int = 8
    max_terms: int = 12
    surface_max_chars: int = 40  # 본문 표면형 길이 상한. 넘으면 그 후보를 버린다
    terminology_max: int = 150  # 작성자 표기 표준 입력 상한 (아직 프롬프트에 넣지 않는다)


class StyleParameters(BaseModel):
    """문체 게이트의 임계값 (D-85, 2026-11-08).

    어미를 프롬프트로 고정하지 않는다. 생성된 본문의 서술문 어미 분포를 재서, 소수 어미(합쇼체·해요체)가 임계를 넘거나
    명령형·청유형이 있으면 예시 문장을 들어 수정 루프로 보낸다. 정규식으로 바꾸지 않고 모델이 고친다.
    """

    minority_register_ratio: float = 0.1  # 소수 어미 문장 수 / 어미를 가린 서술문 수. 이 비율 이상이면 혼용
    minority_register_min: int = 3  # 비율과 함께 최소 문장 수. 짧은 글에서 한두 문장으로 오탐하지 않는다
    forbid_imperatives: bool = True  # 설명글에 명령형·청유형(~하십시오, ~하세요, ~하라, ~하자)을 두지 않는다


class CodeCommentParameters(BaseModel):
    """코드 블록 주석 게이트 (D-85). 독자가 복사하는 부분이라 본문 설명과 별개로 왜·중요한 분기의 주석이 있어야 한다."""

    min_lines: int = 12  # 이 줄 수(공백 제외) 이상인 블록만 본다. 짧은 조각은 본문이 설명한다
    lines_per_comment: int = 25  # 이 줄 수마다 주석 1줄 이상. 파일 경로 머리줄은 주석으로 세지 않는다


class Parameters(BaseModel):
    """조절 값 (09 문서 2.4절). 기본값은 1a 이전 Settings 기본값과 같다."""

    # 반복 상한 (03 문서 5절)
    max_iterations: int = 2
    max_validation_retries: int = 2
    max_llm_retries: int = 3
    max_publish_attempts: int = 3
    max_topic_attempts: int = 2
    # 예산
    draft_token_limit: int = 300_000
    # 주제 (04 문서 2절)
    topic_keyword_overlap: float = 0.5
    topic_title_overlap: float = 0.5
    topic_avoid_lookback_days: int = 180
    # 구조·게이트
    draft_min_chars: int = 4000
    draft_max_chars: int = 20000
    min_code_blocks: int = 1
    structure: StructureParameters = Field(default_factory=StructureParameters)
    review: ReviewParameters = Field(default_factory=ReviewParameters)
    fact_check: FactCheckParameters = Field(default_factory=FactCheckParameters)
    final_read: FinalReadParameters = Field(default_factory=FinalReadParameters)
    polish: PolishParameters = Field(default_factory=PolishParameters)
    publish: PublishParameters = Field(default_factory=PublishParameters)
    glossary: GlossaryParameters = Field(default_factory=GlossaryParameters)
    style: StyleParameters = Field(default_factory=StyleParameters)  # 문체 게이트 (D-85)
    code_comments: CodeCommentParameters = Field(default_factory=CodeCommentParameters)  # 코드 주석 게이트 (D-85)
    # 템플릿 이름 → 있어야 하는 구절. 코드의 기본 목록(services/pipeline_validation.py)에 더해 합친다 (R-08, Q-38)
    prompt_lint: dict[str, list[str]] = Field(default_factory=dict)


# ---------- 버전 문서 ----------


class PipelineVersionBody(BaseModel):
    """해시에 들어가는 본문. 들여오기·검증은 이것으로 하고, 저장 때 메타(id·부모·해시·메모)를 더한다."""

    schema_version: int = 1
    slots: dict[AgentSlot, str]  # 슬롯 → persona_id (agent_routes 의 SLOTS)
    personas: dict[str, PersonaProfile]
    routes: dict[str, list[CallModel]]  # 호출별 후보. 키 없는 벤더 후보도 들어 있다(라운드 생성 때 뺀다)
    level_criteria: LevelCriteria = Field(default_factory=lambda: DEFAULT_LEVEL_CRITERIA.model_copy(deep=True))
    prompts: PromptSet
    parameters: Parameters = Field(default_factory=Parameters)

    def route_documents(self) -> list[dict[str, Any]]:
        """agent_routes 문서 모양. models/routing.build_snapshot 이 그대로 읽는다."""
        documents: list[dict[str, Any]] = [
            {"_id": SLOTS_DOCUMENT_ID, **{slot.value: persona_id for slot, persona_id in self.slots.items()}},
            {"_id": LEVEL_CRITERIA_DOCUMENT_ID, **self.level_criteria.model_dump(mode="json")},
        ]
        documents += [
            {"_id": route_id, "candidates": [each.model_dump(mode="json", exclude_none=True) for each in candidates]}
            for route_id, candidates in self.routes.items()
        ]
        return documents


BODY_FIELDS = frozenset(PipelineVersionBody.model_fields)


class PipelineVersion(PipelineVersionBody):
    """agent_pipeline_versions 문서. insert 만 한다(append-only). 활성은 seq 가 가장 큰 문서다."""

    model_config = ConfigDict(validate_by_name=True)

    version_id: str = Field(alias="_id")  # "v" + seq
    seq: int = Field(ge=1)
    parent_version_id: str | None = None
    content_hash: str
    label: str | None = None
    note: str = ""
    created_at: datetime = Field(default_factory=_now)
    created_by: str = ""
    source: PipelineVersionSource = "import"

    def body(self) -> PipelineVersionBody:
        """메타(id·부모·해시·메모)를 뺀 본문. edit-* 가 최신 버전을 복사해 자식 버전을 만들 때 쓴다."""
        return PipelineVersionBody.model_validate(self.model_dump(include=BODY_FIELDS))


def version_id_of(seq: int) -> str:
    """순번 → 버전 id. `v1`·`v2`… (Q-36). 프롬프트 디렉터리 이름과 겹치므로 영수증은 pipeline_version 필드를 따로 쓴다."""
    return f"v{seq}"


def content_hash_of(body: PipelineVersionBody) -> str:
    """본문의 정규화 JSON(키 정렬, 공백 없음) sha256. 메타는 들어가지 않는다."""
    payload = body.model_dump(mode="json", include=BODY_FIELDS)
    canonical = json.dumps(payload, sort_keys=True, ensure_ascii=False, separators=(",", ":"))
    return "sha256:" + hashlib.sha256(canonical.encode("utf-8")).hexdigest()
