# 05. 에이전트 계약

프롬프트 문구는 이 문서에 두지 않는다(`app/agents/prompts/` 아래 버전 관리, 06 문서). 버전 디렉터리는 배포 후 고치지 않는다. 문구를 바꾸면 새 디렉터리를 만든다. 여기서는 페르소나 정의와 입출력 JSON 계약만 고정한다. 모든 출력은 Pydantic 모델로 파싱되며, 벤더의 structured output 기능을 쓸 수 있으면 스키마를 그대로 넘긴다. `item_id`, `source_review_id`, `reviewer_slot`, `is_new_in_version`은 오케스트레이터가 부모 리뷰 문맥을 사용해 파싱 뒤에 붙인다.

## 1. 페르소나 프로필

`PersonaProfile`은 설정 파일(YAML 또는 Python 상수)로 두고 `persona_id`로 참조한다. 아래는 초기 구성이며, 어떤 모델이 어떤 페르소나를 맡는지는 `ModelBinding`이 정한다.

| persona_id | 표시 이름 | 작성자 성향 (`writer_bias`) | 주 렌즈 |
|---|---|---|---|
| `architect` | 구조적 아키텍트 | 클린 아키텍처, 리팩토링, 디자인 패턴. 단계적 전개, 가독성 높은 서사 | `LOGIC_READABILITY` |
| `systems_engineer` | 시스템 엔지니어 | DB 튜닝, 인프라, 대용량 처리. 표·다이어그램·팩트 기반 정리, 레퍼런스 종합 | `FACT_STRUCTURE` |
| `troubleshooter` | 실용주의 트러블슈터 | 공개된 장애 사례 분석, 트렌드 비평, 공개된 측정 결과 해석. 직설적·위트 있는 톤, 관행에 의문 제기. 근거 없이 겪은 일처럼 쓰지 않는다 | `CRITICAL_PRACTICAL` |

```python
class PersonaProfile(BaseModel):
    persona_id: str
    display_name: str
    writer_bias: str                 # 작성자 프롬프트에 들어가는 성향 서술
    primary_lens: ReviewLens
    tone_markers: list[str]          # 톤 보존 규칙에서 "건드리지 말 것"으로 리뷰어에게 전달할 특징
```

### 렌즈 정의 (리뷰어 프롬프트의 평가 기준)

문구의 원본은 `prompts/v3/review_full.md` 다. 범위 제한: CRITICAL·MAJOR 는 `core_mechanism` 안의 결함에만 쓰고, 범위 밖 운영 문제를 구현하라는 요구는 MINOR 이하다. 본문에 쓴 내용이 틀리면 범위와 상관없이 CRITICAL 이고, 약속한 메커니즘을 보여 주지 않으면 렌즈와 상관없이 MAJOR 다.

| 렌즈 | 본다 | 보지 않는다 |
|---|---|---|
| `LOGIC_READABILITY` | 문맥 비약, 전제 없이 등장하는 개념, 예제 코드의 버그·컨벤션 위반, 이해하기 어려운 문장 구조 | 기술 스펙 사실 여부(FACT 렌즈 몫), 어조 |
| `FACT_STRUCTURE` | 잘못된 스펙·버전·API 시그니처(할루시네이션), 핵심 메커니즘이 동작하는 데 필요한 배경·엣지 케이스의 누락, 표·목차 구조 완결성 | 문체, 실무 효용 판단 |
| `CRITICAL_PRACTICAL` | "이론만 그렇다"는 반론, 가정과 한계를 밝혔는지, 상투적 완충 문구·AI 클리셰, 과장된 결론 | 문법·어조·유머. 클리셰 제거는 의미 없는 문구에 한정 |

## 2. 호출 종류별 입출력

### 2.1 `PROPOSE_TOPIC`

입력:

```python
class ProposeTopicInput(BaseModel):
    persona: PersonaProfile
    theme_keyword: str | None
    avoid_topics: list[AvoidTopic]       # 레지스트리 최근 TOPIC_AVOID_LOOKBACK_DAYS일 항목 + (재제안 시) 이번 라운드 확정 주제
    attempt_number: int
    conflict_feedback: str | None        # 재제안 시 "이전 제안이 X와 겹쳤다"
    categories: list[CategoryOption]     # 라운드 스냅샷(02 문서). 제안마다 이 중 한 쌍을 고른다

class AvoidTopic(BaseModel):
    title: str
    keywords: list[str]
```

출력:

```python
class ProposeTopicOutput(BaseModel):
    proposals: list[TopicProposal] = Field(min_length=3, max_length=3)   # 1순위부터
```

파서는 제안마다 `(main_category_id, sub_category_id)`가 입력 `categories`에 있는지 검사한다. 없으면 `LLMOutputError`로 호출을 재시도한다.

### 2.2 `WRITE_DRAFT`

입력:

```python
class WriteDraftInput(BaseModel):
    persona: PersonaProfile
    topic: TopicProposal
    structure_rules: StructureRules      # 필수 섹션, 앵커 규칙, 길이 범위, 코드 블록 규칙
```

출력:

```python
class DraftOutput(BaseModel):
    title: str = Field(min_length=1, max_length=100)     # Post BE CreatePostDto.title 상한
    content_markdown: str                # 앵커 포함. 04 문서 8절 변환표의 문법만 쓴다
    summary: str = Field(min_length=1, max_length=150)   # 메타 설명. 발행 때 Post BE description
```

### 2.3 `REVISE_DRAFT`

입력:

```python
class ReviseDraftInput(BaseModel):
    persona: PersonaProfile
    topic: TopicProposal
    structure_rules: StructureRules
    previous: DraftOutput                # 직전 제출 버전. 게이트에 떨어진 본문도 여기 들어간다
    reviews: list[ReviewForAuthor]       # 마지막 리뷰 완료 버전의 open_blocking_items 와, 그 버전이 FULL 리뷰였으면 비차단 항목. 리뷰 완료 버전이 없으면(v1 게이트 재작성) 빈 목록
    arbitration_results: list[ArbitrationForAuthor]   # UPHOLD_REVIEWER 항목은 must_accept=True
    validation_errors: list[str]         # 게이트 실패로 돌아온 경우
    human_instruction: str | None = None # 이번 수정본의 origin_cause 가 HUMAN 일 때(그 게이트 재작성 포함). 이 지시가 최우선이며 resolutions 는 비워도 된다
    iteration: int
    max_iterations: int

class ReviewForAuthor(BaseModel):
    reviewer_label: str                  # "리뷰어 1" (블라인드는 리뷰어→작성자 방향에는 적용하지 않지만 모델명은 숨긴다)
    lens: ReviewLens
    items: list[ReviewItem]              # 차단 항목과, FULL 리뷰였으면 덧붙여 처리할 수 있는 비차단 항목. severity 로 구분한다
    unresolved_notes: dict[str, str]     # 이월 항목 item_id → SPOT_CHECK 리뷰어가 아직 미해소로 본 이유(ItemCheck.note)
    overall_feedback: str

class ArbitrationForAuthor(BaseModel):
    item_id: str
    outcome: ArbitrationOutcome
    reasoning: str
    must_accept: bool
```

출력:

```python
class RevisionOutput(BaseModel):
    title: str = Field(min_length=1, max_length=100)
    content_markdown: str
    summary: str = Field(min_length=1, max_length=150)
    resolutions: list[ItemResolution]    # 차단 항목은 open_blocking_items와 ID가 정확히 같아야 한다. MINOR·NITPICK은 생략하거나 ACCEPTED·DEFERRED로 덧붙일 수 있다
    author_note: str                     # 사람이 읽는 수정 요약. 요약 노트에 그대로 쓰일 수 있다
```

### 2.4 `REVIEW` (FULL)

입력:

```python
class TopicForReviewer(BaseModel):
    """리뷰어에게 주는 주제. rationale(왜 이 성향의 작성자가 쓰는지)은 페르소나 서술이라 뺀다 (04 문서 4절 블라인드)."""
    title: str
    audience: str
    keywords: list[str]
    outline: list[str]

class ReviewItemForReviewer(BaseModel):
    """SPOT_CHECK·중재에 주는 지적. reviewer_slot·source_review_id·is_new_in_version 은 뺀다. item_id 는 SPOT_CHECK 가 그대로 돌려줘야 해 유지한다."""
    item_id: str
    section_id: str
    lens: ReviewLens
    severity: IssueSeverity
    comment: str
    suggested_fix: str | None

class ReviewInput(BaseModel):
    scope: Literal["FULL"]
    lens: ReviewLens
    secondary_lens: ReviewLens | None
    topic: TopicForReviewer              # 독자층·목차 (저자 정보 없음, rationale 없음)
    content_markdown: str
    tone_preservation: TonePreservationRule   # 지적 가능/불가 목록 + author persona 의 tone_markers (익명화)
    # author_slot, model, persona display name 은 넣지 않는다
```

출력:

```python
class ReviewOutput(BaseModel):
    verdict: ReviewVerdict                           # reported_verdict 로 저장. 판정에는 쓰지 않는다
    items: list[ReviewItemOutput]
    checklist: VerificationChecklist                 # 비어 있으면 스키마 위반
    overall_feedback: str = Field(max_length=1000)

class ReviewItemOutput(BaseModel):
    section_id: str                                  # 본문 앵커 중 하나. 없는 앵커면 스키마 위반
    lens: ReviewLens                                 # 본인에게 배정된 lens 또는 secondary_lens 중 하나여야 한다
    severity: IssueSeverity
    comment: str
    suggested_fix: str | None
```

### 2.5 `REVIEW` (SPOT_CHECK)

입력:

```python
class SpotCheckInput(BaseModel):
    scope: Literal["SPOT_CHECK"]
    lens: ReviewLens
    secondary_lens: ReviewLens | None
    previous_content: str
    current_content: str
    my_previous_items: list[ReviewItemForReviewer]   # 직전 리뷰 완료 버전의 open_blocking_items 중 본인이 낸 항목만 (각 항목에 lens 가 태그되어 있다). 슬롯 필드 없음
    resolutions: list[ItemResolution]                # 작성자의 결정 (기각 사유 포함)
    tone_preservation: TonePreservationRule
```

출력:

```python
class SpotCheckOutput(BaseModel):
    item_checks: list[ItemCheck]                     # my_previous_items 전부에 대해 하나씩
    new_critical_items: list[ReviewItemOutput]       # CRITICAL 만 남긴다. 다른 심각도는 파서가 버리고 로그를 남긴다
    checklist: VerificationChecklist

class ItemCheck(BaseModel):
    item_id: str
    resolved: bool
    note: str                                        # resolved=False 면 비어 있으면 안 된다
```

### 2.6 `ARBITRATE`

입력:

```python
class ArbitrateInput(BaseModel):
    item: ReviewItemForReviewer                      # 평가 렌즈는 item.lens 하나만 사용한다. 슬롯 필드 없음(블라인드)
    section_before: str                              # 해당 섹션만 (전후 버전)
    section_after: str
    author_rejection_reason: str
    reviewer_unresolved_note: str
```

출력:

```python
class ArbitrateOutput(BaseModel):
    outcome: ArbitrationOutcome
    reasoning: str = Field(max_length=800)
```

### 2.7 v3 에서 더한 호출과 필드

입출력 모델의 원본은 `app/agents/contracts.py` 다.

| 호출 | 입력 | 출력 |
|---|---|---|
| `PROPOSE_TOPIC` | `level_criteria`, `evidence?` 추가 | 제안에 `core_mechanism`, `out_of_scope[]`, `target_level`, `prerequisites` 추가. 빈 값, 근거 없는 400, 근거 없는 트러블슈터의 200 초과는 계약 위반 |
| `WRITE_DRAFT`·`REVISE_DRAFT` | `evidence?` 추가. `required_sections` 는 글 수준으로 정한다 | 수정 해소에 `OUT_OF_SCOPE` 추가 |
| `REVIEW` | 주제에 범위·수준 필드 추가 | 그대로 |
| `ARBITRATE` | `dispute_kind`, `core_mechanism`, `out_of_scope`, `scope_declaration`, `search_evidence?` 추가 | 그대로 |
| `FACT_CHECK` | `FactCheckInput`(`arbitrate_search` 는 `ArbitrateSearchInput`) | 글과 출처 |
| `FACT_TRANSFORM` | `FactTransformInput` | `FactCheckOutput`·`FactSpotCheckOutput`(리뷰 출력 + `claims[]`) |
| `FINAL_READ` | `FinalReadInput`(제목, 주제, 본문) | `findings[]{kind, section_id, quote, comment}`, `overall` |
| `ASSESS_LEVEL` | `AssessLevelInput`(제목, 본문, 기준표. 목표 수준 없음) | `level`, `evidence[]{criterion, quote}`, `reasoning` |
| `POLISH_FORMAT`·`POLISH_TONE` | 번호 붙은 문단(문체는 `tone_markers` 도) | `patches[]{index, text}` |

수준 기준표는 `models/level.py` 의 기본값이고 `agent_routes` 의 `LEVEL_CRITERIA` 문서로 바꿀 수 있다. 항목 수가 아니라 한 결정의 인과를 얼마나 깊게 설명하는지로 본다.

## 3. 구조 규칙 (`StructureRules`)

```python
class StructureRules(BaseModel):
    required_sections: list[str] = ["intro", "body-1", "tradeoff", "conclusion"]
    anchor_format: str = "<!-- sec:{id} -->"
    min_chars: int
    max_chars: int
    min_code_blocks: int = 1
    language: str = "ko"
    forbidden_syntax: list[str] = ["HTML 태그(섹션 앵커 제외)", "이미지", "각주", "mermaid 코드 블록"]
```

`required_sections` 는 글 수준으로 정한다. 입문(100)은 `tradeoff` 를 요구하지 않고 범위 선언을 `conclusion` 에 쓴다. 수준이 없는 옛 글은 원래 규칙이다. 작성자 프롬프트에는 앵커를 넣고 수정 시 ID를 유지하라는 지시가 들어간다. 허용 문법은 04 문서 8절 변환표뿐이고, `forbidden_syntax`는 게이트에서 실패한다. 완전한 Python 모듈은 펜스 언어를 `python` 으로, 잘린 조각은 `python-snippet` 으로 적는다. `python` 은 `ast.parse`, `json` 은 `json.loads` 대상이고, 모든 코드 블록은 비어 있지 않고 펜스 짝이 맞아야 한다.

## 4. 톤 보존 규칙 (`TonePreservationRule`)

```python
class TonePreservationRule(BaseModel):
    may_flag: list[str] = [
        "사실 오류", "논리 결함", "코드 버그·컨벤션 위반",
        "이해를 방해하는 문장 구조", "의미 없는 완충 문구·상투적 도입/마무리",
    ]
    must_not_flag: list[str] = [
        "유머·위트", "1인칭 화법", "문장 리듬·길이 선호", "어조(직설·시니컬 포함)", "불릿 대신 서술을 택한 것 자체",
    ]
    author_tone_markers: list[str]       # 페르소나의 tone_markers (페르소나 이름은 제거)
```

## 5. 피어 리뷰 요약 노트 템플릿

LLM 호출 없이 템플릿으로 만든다. 발행 본문 하단에 붙는다.

```markdown
---

## 이 글은 어떻게 만들어졌나

이 글은 **{author_display}**가 작성하고 {lens_labels} 관점의 교차 리뷰를 거쳐 완성되었습니다.

- 버전: v1 → v{final_version}
- 발행을 막은 사항: CRITICAL {n_critical}, MAJOR {n_major}
- 기록만 남긴 사항: MINOR {n_minor}, NITPICK {n_nitpick} (fast-track 이면 수정 없이 발행)
- 작성자가 수용한 사항: {n_accepted}건 / 근거를 들어 유지한 사항: {n_rejected}건
- 모델: {author_model} 작성, {model}({lens})… 리뷰{, 수정 모델이 다르면 수정}{, 중재가 있었으면 중재}{, 패치가 적용됐으면 다듬기}
{if arbitrations}
- 의견이 갈려 제3자 판정을 받은 사항: {n_arbitrations}건
{endif}

{for r in rejected_items}
> **{r.lens_label} 리뷰의 지적**: {r.comment}
> **작성자의 입장**: {r.reason}
{if r.arbitration}> **판정**: {r.outcome_text} — {r.reasoning}{endif}
{endfor}
```

기각 항목은 최대 3건까지만 노출하고(CRITICAL > MAJOR 순), 나머지는 건수만 적는다. 노트는 본문 뒤에 붙여 함께 Tiptap으로 변환하므로(04 문서 8절) 변환표의 문법만 쓴다. 노트에 넣는 LLM 텍스트(`comment`, `reason`, `reasoning`)는 줄바꿈을 공백으로 바꾸고 마크다운·HTML 특수문자를 이스케이프해 문단 텍스트로만 들어가게 한다. 그래서 노트는 항상 변환된다.
