# 02. 도메인 모델

Pydantic v2 모델로 `app/models/`에 둔다. 저장 형태(MongoDB 문서)와 도메인 모델은 같은 클래스를 쓰되, API 응답 스키마(`app/schemas/`)는 별도로 둔다(CONVENTIONS 2절).

## 1. 열거형

```python
class AgentSlot(StrEnum):
    A = "A"
    B = "B"
    C = "C"


class RoundStatus(StrEnum):
    TOPIC_SELECTION = "TOPIC_SELECTION" # 생성 즉시. 주제 제안·중복 판별 진행 중 (배리어). 한 번에 한 라운드만
    IN_PROGRESS = "IN_PROGRESS"         # 주제 확정, 글 3편이 독립적으로 진행
    COMPLETED = "COMPLETED"             # 3편 모두 PUBLISHED 또는 REJECTED 로 종결
    ABORTED = "ABORTED"                 # 관리자 중단 또는 주제 단계 실패


class PostStatus(StrEnum):
    TOPIC_LOCKED = "TOPIC_LOCKED"       # 글 문서가 생기는 첫 상태. 주제 제안 중에는 글 문서가 없다
    DRAFTING = "DRAFTING"               # 작성자 호출 진행 중 (v1 또는 수정본)
    VALIDATING = "VALIDATING"           # 결정론적 게이트 (LLM 호출 없음)
    IN_REVIEW = "IN_REVIEW"             # FULL 리뷰 진행
    IN_SPOT_CHECK = "IN_SPOT_CHECK"     # SPOT_CHECK 리뷰 진행
    IN_ARBITRATION = "IN_ARBITRATION"   # 작성·리뷰 벤더가 아닌 모델의 중재 진행
    REVISING = "REVISING"               # 작성자 호출 전 대기. revision_cause 가 리뷰·게이트·사람 중 무엇인지를 말한다
    POST_REVIEW = "POST_REVIEW"         # 리뷰 통과 뒤 발행 전 단계. post_review_stage 가 어디까지 했는지 말한다
    READY_TO_PUBLISH = "READY_TO_PUBLISH"
    PUBLISHED = "PUBLISHED"             # 종결
    ESCALATED = "ESCALATED"             # 사람 판단 대기
    REJECTED = "REJECTED"               # 종결(폐기). 사람 결정 또는 라운드 중단
    FAILED = "FAILED"                   # 재시도 소진·글 예산 초과·내부 오류. 사람이 재개하거나 폐기한다 (ESCALATED 와 구분: 품질 판단이 아니라 같은 자리에서 다시 돌리면 되는 문제)


class ReviewScope(StrEnum):
    FULL = "FULL"
    SPOT_CHECK = "SPOT_CHECK"
    # 중재는 PeerReview 가 아니다. Arbitration 엔티티로 남긴다.


class ReviewVerdict(StrEnum):
    APPROVE = "APPROVE"
    REQUEST_CHANGES = "REQUEST_CHANGES"


class IssueSeverity(StrEnum):
    CRITICAL = "CRITICAL"   # 기술적 오류, 코드 버그, 사실 오류. 발행 차단
    MAJOR = "MAJOR"         # 논리 비약, 구조 결함, 필수 배경 누락. 발행 차단
    MINOR = "MINOR"         # 표현, 문맥, 가독성 개선. 차단하지 않음
    NITPICK = "NITPICK"     # 취향. 차단하지 않음, 작성자 재량


class ResolutionStatus(StrEnum):
    ACCEPTED = "ACCEPTED"
    REJECTED_WITH_REASON = "REJECTED_WITH_REASON"
    DEFERRED = "DEFERRED"   # MINOR·NITPICK 에만 허용. "이번 버전에서 다루지 않음"
    OUT_OF_SCOPE = "OUT_OF_SCOPE"  # 차단 항목에만 허용. 글의 범위 밖이라 본문을 넓히지 않고 범위 선언에 남긴다. 사유 필수


class ArbitrationOutcome(StrEnum):
    UPHOLD_REVIEWER = "UPHOLD_REVIEWER"   # 리뷰어가 맞다. 작성자가 수정해야 한다
    UPHOLD_AUTHOR = "UPHOLD_AUTHOR"       # 작성자 기각이 타당하다. 해당 항목 종결


class ReviewLens(StrEnum):
    LOGIC_READABILITY = "LOGIC_READABILITY"   # 논리 전개, 코드 정합성, 가독성
    FACT_STRUCTURE = "FACT_STRUCTURE"         # 기술 스펙 사실 확인, 버전, 누락된 배경·엣지 케이스
    CRITICAL_PRACTICAL = "CRITICAL_PRACTICAL" # 반론, 실무 효용, 클리셰 제거


class RevisionCause(StrEnum):
    """REVISING 으로 들어온 이유. 나갈 때 iteration 을 올릴지 가른다."""

    REVIEW = "REVIEW"          # 리뷰 평가가 수정을 요구. 나갈 때 iteration += 1
    GATE = "GATE"              # 결정론적 게이트 실패. iteration 유지
    HUMAN = "HUMAN"            # 사람이 ESCALATED 에서 1회 재수정을 허가. iteration 유지


class EscalationReason(StrEnum):
    MAX_ITERATIONS_REACHED = "MAX_ITERATIONS_REACHED"
    PUBLISH_FAILED = "PUBLISH_FAILED"
    PUBLISH_UNCERTAIN = "PUBLISH_UNCERTAIN"  # 발행 호출 결과를 모름. 중복 게시를 막으려고 자동 재시도하지 않는다 (04 문서 8절)


class EscalationDecisionKind(StrEnum):
    """ESCALATED 글에 대한 사람의 결정 (03 문서 23~25행)."""

    PUBLISH_AS_IS = "PUBLISH_AS_IS"
    REVISE = "REVISE"
    REJECT = "REJECT"
    CONFIRM_PUBLISHED = "CONFIRM_PUBLISHED"  # PUBLISH_UNCERTAIN 에서만


class FailureCode(StrEnum):
    """FAILED 로 들어온 이유. 재개 방식이 갈린다 (03 문서 26행)."""

    LLM_RETRIES_EXHAUSTED = "LLM_RETRIES_EXHAUSTED"
    GATE_RETRIES_EXHAUSTED = "GATE_RETRIES_EXHAUSTED"
    BUDGET_EXCEEDED = "BUDGET_EXCEEDED"     # 호출 전 소프트 예산 검사 실패. 재개 때 상한을 올릴 수 있다
    INTERNAL_ERROR = "INTERNAL_ERROR"       # 코루틴 안의 예상하지 못한 예외. 같은 오류로 호출을 되풀이하지 않으려고 멈춘다


class RoundAbortReason(StrEnum):
    MANUAL = "MANUAL"
    TOPIC_ATTEMPTS_EXHAUSTED = "TOPIC_ATTEMPTS_EXHAUSTED"
    LLM_FAILED = "LLM_FAILED"
    INTERNAL_ERROR = "INTERNAL_ERROR"


class TopicRegistryStatus(StrEnum):
    RESERVED = "RESERVED"      # 세 주제가 모두 확정된 뒤에만 등록
    PUBLISHED = "PUBLISHED"
    RELEASED = "RELEASED"      # 폐기·라운드 중단·주제 단계 실패


class CallKind(StrEnum):
    PROPOSE_TOPIC = "PROPOSE_TOPIC"
    WRITE_DRAFT = "WRITE_DRAFT"
    REVISE_DRAFT = "REVISE_DRAFT"
    REVIEW = "REVIEW"
    ARBITRATE = "ARBITRATE"
    GLOSSARY = "GLOSSARY"               # 용어 추출 (08 문서 D-64). v3 에서 더한 FACT_CHECK·FACT_TRANSFORM·FINAL_READ·ASSESS_LEVEL·POLISH_TONE 은 models/enums.py


class PostReviewStage(StrEnum):
    POLISH = "POLISH"
    FINAL_READ = "FINAL_READ"
    ASSESS_LEVEL = "ASSESS_LEVEL"
    GLOSSARY = "GLOSSARY"               # 용어 추출. 실패해도 건너뛴다 (08 문서)
    DONE = "DONE"


class GlossaryStatus(StrEnum):          # 표제어·뜻의 승인 상태 (08 문서 2.1절). 표시는 APPROVED 만
    PROPOSED = "PROPOSED"
    APPROVED = "APPROVED"
    REJECTED = "REJECTED"
    MERGED = "MERGED"                   # 표제어만. merged_into 를 따라간다
```

`ProvenanceStageKind`(제작 기록의 단계)는 08 문서 3.2절이다.

## 2. 엔티티 관계

```
PublishingRound (Aggregate Root, 라운드 수준)
 ├─ round_id, round_seq, status, theme_keyword?, slot_bindings{slot → ModelBinding}
 ├─ routing?: RoutingSnapshot               호출별 모델 후보, 슬롯 페르소나, 수준 기준표. 생성 때 버전 문서의 routes 에서 만든 유효 스냅샷. 응답에 내보내지 않는다. 라우팅 전 라운드는 없다
 ├─ pipeline_version_id?                    생성 때 고정한 파이프라인 버전(프롬프트·조절 값·페르소나, 09 문서 D-73). 끝까지 이 버전으로 돈다. 1a 전 라운드는 없고 최신 버전으로 돈다
 ├─ evidence?                               실제 프로젝트 근거(git 이력, code-graph context, 측정 기록)
 ├─ categories: CategoryOption[]            라운드 생성 때 Post BE 에서 읽은 카테고리 스냅샷. `AGENT_MAIN_CATEGORIES` 에 든 메인 아래 서브만 넣는다(07 문서 D-60). 주제 제안 입력
 ├─ draft_ids                               길이 0 (주제 선정 중) 또는 3 (확정 후)
 ├─ topic_attempts: TopicAttempt[]          주제 제안·재제안 이력 (배리어 단계 전용)
 └─ abort_reason?: RoundAbortReason

PostDraft (Aggregate Root, 글 수준)        ◄── 리뷰 결과를 써도 라운드 루트에 락이 걸리지 않도록 분리
 ├─ draft_id, round_id, author_slot, status
 ├─ iteration                               0부터. 리뷰 반영으로 수정을 시작할 때만 +1 (03 문서 1절)
 ├─ validation_retries                      연속 게이트 실패 횟수. 0부터. 실패마다 1 증가 후 상한과 비교, 통과하면 0 (03 문서)
 ├─ revision_cause?                         REVISING·DRAFTING 에서 유지. REVIEW | GATE | HUMAN. DRAFTING 의 호출 종류와 새 버전의 origin_cause 를 정하고, submit_version 이 지운다
 ├─ human_instruction?                      사람이 ESCALATED → REVISE 할 때 남긴 지시문
 ├─ escalation_reason?, publish_attempts    발행 실패 횟수. 0부터. 같은 비교. 사람이 발행 대기로 되돌리면 0
 ├─ failure?: FailureRecord                  FAILED 재개에 필요한 직전 상태와 사유
 ├─ token_limit                             글 소프트 예산. 확정 때 DRAFT_TOKEN_LIMIT, 재개 때 사람이 올릴 수 있다
 ├─ approved_by?, approved_at?              발행 승인. 그 버전에 대한 것이라 새 버전이 제출되면 지운다
 ├─ topic: TopicProposal
 ├─ reviewer_assignments: ReviewAssignment[3]  렌즈마다 하나. 옛 글은 슬롯 리뷰어 둘(slot, secondary_lens)이고 같은 코드로 돈다
 ├─ versions: DraftVersion[]                게이트 실패본도 남는다. 개수 상한은 두지 않고 전이가 막는다
 │    ├─ version_number, title, summary, content_markdown, section_ids[]
 │    ├─ origin_cause?                      이 버전을 만든 시작 원인(REVIEW | HUMAN | None=초안). 게이트 재시도를 거쳐도 유지
 │    ├─ passed_validation
 │    ├─ validation_errors[]                게이트 실패 사유. 통과 버전이면 빈 목록
 │    ├─ resolutions: ItemResolution[]      차단 항목은 필수. MINOR·NITPICK 은 ACCEPTED·DEFERRED 로 덧붙일 수 있다
 │    ├─ author_note
 │    └─ call_id                            이 버전을 만든 AgentCall
 ├─ reviews: PeerReview[]
 │    ├─ review_id, target_version, lens, scope, model_id?, reviewer_slot?, secondary_lens?   리뷰어는 lens 로 가린다. 뒤의 둘은 옛 글만
 │    ├─ reported_verdict?                  FULL 만. SPOT_CHECK 는 없음
 │    ├─ derived_verdict                    오케스트레이터 계산
 │    ├─ items: ReviewItem[]                SPOT_CHECK 에서는 신규 CRITICAL 만
 │    ├─ item_checks: ItemCheck[]           SPOT_CHECK 전용. FULL 은 빈 목록
 │    ├─ checklist, overall_feedback, call_id
 │    └─ claims: FactClaim[]                사실 확인 렌즈의 주장과 판정
 ├─ arbitrations: Arbitration[]
 ├─ post_review_stage?                      POST_REVIEW 에서 다음에 할 일. POLISH → FINAL_READ → ASSESS_LEVEL → GLOSSARY → DONE
 ├─ polish?: PolishRecord                   적용·버린 패치 수와 사유, 건너뛴 호출, 긴 문단, 패치를 쓴 모델·제안한 모델(09 문서 D-91)
 ├─ glossary?: GlossaryRecord               용어 추출 결과(항목·보류·버림·건너뜀 사유). 발행 때 post_glossary 로 옮긴다 (08 문서 2.1절)
 ├─ final_read?: FinalRead                  findings(kind, section_id, quote, comment), overall
 ├─ level_assessment?: LevelAssessment      level, evidence(criterion, quote), reasoning, criteria_version
 ├─ user_level?                             사용자가 매긴 수준. 평가자와의 일치도 측정에 쓴다
 ├─ last_reviewed_version?                  평가(리뷰·중재)를 마친 마지막 버전. FULL·SPOT_CHECK 를 가르고 open_blocking_items 의 기준 (D-21)
 ├─ rejected_by?, abort_reason?             REJECTED 가 된 사람 결정 또는 라운드 중단 사유
 ├─ publication_intent?: PublicationIntent
 └─ publication?: Publication

AgentCall (독립 컬렉션, LLM 호출 1회의 영수증. insert 만 한다)
 ├─ call_id, round_id, draft_id?, kind, slot?, lens?, model_binding(CallModel 스냅샷), pipeline_version?(메시지를 렌더링한 버전 문서 id, 1a 부터), prompt_template_version?(옛 영수증의 프롬프트 디렉터리 이름)
 ├─ target_version?, topic_attempt?, item_id?   어느 단계의 호출인지
 ├─ attempt_number                          같은 단계 호출 안의 몇 번째 시도. 1부터
 ├─ messages                                이 시도에 실제로 보낸 메시지
 ├─ output_raw?, output_parsed_ok, error?, stop_reason, citations[]
 ├─ usage: TokenUsage, latency_ms
 └─ started_at, finished_at

TopicRegistryEntry (독립 컬렉션 agent_topics, 라운드 간 중복 방지)
 └─ title, keywords[], embedding?, draft_id, round_id, status, published_at?   _id 는 draft_id 와 같다(D-28)
```

## 3. 값 객체

### ModelBinding

```python
class ModelBinding(BaseModel):
    provider: str              # "anthropic" | "google" | "xai" | ...  (도메인은 문자열로만 안다)
    model_id: str              # "claude-…", "gemini-…", "grok-…"
    persona_id: str            # PersonaProfile 식별자. 슬롯당 하나
    temperature: float = 0.7   # 응답 계약 때문에 남겨 두고 읽지 않는다
    max_output_tokens: int


class CallModel(BaseModel):
    """호출 하나의 모델. 라우트 후보이자 영수증 스냅샷이다. 옛 영수증의 persona_id 는 읽을 때 버린다."""

    provider: str
    model_id: str
    effort: str | None = None        # None 이면 보내지 않아 벤더 기본값이다
    temperature: float | None = None # 지원표에 없는 조합은 라운드 생성에서 거절한다
    max_output_tokens: int


class RoutingSnapshot(BaseModel):
    """파이프라인 버전 문서의 routes 를 라운드 생성 때 뜬 것(09 문서. 1a 전에는 agent_routes). 키 없는 벤더 후보는 빠진다. 선택은 models/routing.py 의 순수 함수다."""

    routes: dict[str, list[CallModel]]  # PROPOSE_TOPIC:{persona}, WRITE_DRAFT:{persona}, REVISE_DRAFT:{persona}, REVIEW:{lens},
                                        # ARBITRATE, FINAL_READ, ASSESS_LEVEL, POLISH_TONE, FACT_TRANSFORM (POLISH_FORMAT 은 v4 에서 뺐다, D-62)
    slot_personas: dict[AgentSlot, str]
    level_criteria: LevelCriteria
```

`PersonaProfile`은 코드/설정으로 관리한다(05 문서). 도메인은 `persona_id`만 저장한다. 세 슬롯의 `primary_lens`는 서로 달라야 한다. 라운드 시작이 검사한다.

### TopicProposal

```python
class TopicProposal(BaseModel):
    title: str = Field(max_length=200)  # 발행 때 Post BE topic 으로도 보낸다
    audience: str                       # 핵심 독자층
    keywords: list[str] = Field(min_length=3, max_length=3)   # 발행 때 Post BE tags
    outline: list[str] = Field(min_length=3)   # 목차 초안
    rationale: str                      # 왜 이 주제를 이 페르소나가 쓰는지
    core_mechanism: str = ""            # 끝까지 설명할 핵심 메커니즘 하나. 옛 문서를 읽으려고 기본값을 둔다. 빈 값은 파서가 막는다
    out_of_scope: list[str] = []        # 다루지 않을 것
    target_level: PostLevel | None = None  # 작성자의 목표 수준. 평가자에게 주지 않는다. 없으면 옛 규칙(tradeoff 필수)
    prerequisites: str = ""             # 독자에게 전제하는 지식
    main_category_id: str               # 라운드 categories 의 한 쌍. 파서가 목록에 있는지 검사한다 (05 문서)
    sub_category_id: str


class CategoryOption(BaseModel):
    """Post BE 서브 카테고리 하나와 그 메인 카테고리. 라운드 생성 때 GET /api/v1/categories 로 만들고 메인은 AGENT_MAIN_CATEGORIES 로 거른다."""

    main_category_id: str               # parentId 가 null 인 Category
    main_label: str
    sub_category_id: str                # parentId 가 main_category_id 인 Category
    sub_label: str
```

### TopicAttempt (라운드 배리어 단계 이력)

```python
class TopicConflict(BaseModel):
    slot: AgentSlot
    proposal_index: int
    other_slot: AgentSlot | None        # 레지스트리와 겹치면 None
    other_index: int | None
    registry_title: str | None
    kind: Literal["keyword", "title", "registry"]
    score: float


class LockedTopic(BaseModel):
    slot: AgentSlot
    source_attempt: int
    proposal_index: int
    proposal: TopicProposal             # 원본 배열이 바뀌어도 선택 결과가 보존된다


class TopicAttempt(BaseModel):
    attempt_number: int                 # 1..MAX_TOPIC_ATTEMPTS
    proposals: dict[AgentSlot, list[TopicProposal]]  # 이번 시도에서 제안한 슬롯만
    locked: dict[AgentSlot, LockedTopic]  # 이전 시도의 선택도 스냅샷으로 유지
    conflicts: list[TopicConflict]
```

세 슬롯이 모두 `locked`에 들어올 때까지 글 문서를 만들지 않는다. 상한까지 못 채우면 라운드를 `ABORTED`로 두고, 레지스트리에는 아무것도 남기지 않는다.

### ReviewItem (불변)

```python
class ReviewAssignment(BaseModel):
    slot: AgentSlot
    primary_lens: ReviewLens
    secondary_lens: ReviewLens | None = None


class ReviewItem(BaseModel):
    model_config = ConfigDict(frozen=True)

    item_id: str                        # 오케스트레이터가 "{review_id}:{n}" 으로 부여. LLM 출력에는 없다
    source_review_id: str
    reviewer_slot: AgentSlot | None = None  # 옛 글만. 리뷰어는 source_review_id 의 리뷰 렌즈로 찾는다
    section_id: str                     # 본문 앵커 id. intro, body-1, code-1, tradeoff, conclusion
    lens: ReviewLens                    # 이 지적을 낸 관점 (리뷰어의 primary 또는 secondary 중 하나)
    severity: IssueSeverity
    comment: str
    suggested_fix: str | None
    is_new_in_version: bool = False     # SPOT_CHECK 신규 CRITICAL 만 True. 오케스트레이터가 설정
    from_fact_check: bool = False       # 사실 확인 주장에서 코드가 만든 지적. 분쟁이면 중재자도 검색한다
```

`resolution_status`는 여기 두지 않는다. 결정은 다음 `DraftVersion`의 `ItemResolution`에 있다.

### ItemResolution

```python
class ItemResolution(BaseModel):
    item_id: str
    status: ResolutionStatus
    reason: str | None                  # REJECTED_WITH_REASON·OUT_OF_SCOPE 이면 필수
    section_id_after: str | None        # 수정으로 섹션이 바뀌었으면 새 앵커
```

### VerificationChecklist

```python
class ItemCheck(BaseModel):
    """SPOT_CHECK 가 직전 차단 항목마다 남기는 해소 여부."""

    item_id: str
    resolved: bool
    note: str                           # resolved 가 False 면 왜 아직 아닌지


class VerificationChecklist(BaseModel):
    """무엇을 확인했는지 적게 해 동조 현상을 줄인다. 세 목록이 모두 비면 스키마 위반."""

    checked_claims: list[str]
    checked_code_blocks: list[str]      # 검토한 코드 블록 section_id
    unverifiable: list[str]

    @model_validator(mode="after")
    def at_least_one_entry(self) -> VerificationChecklist:
        if not (self.checked_claims or self.checked_code_blocks or self.unverifiable):
            raise ValueError("checklist is empty")
        return self
```

### Arbitration

```python
class Arbitration(BaseModel):
    arbitration_id: str
    target_version: int
    item_id: str                        # 교착된 ReviewItem
    arbiter_slot: AgentSlot | None = None  # 옛 글만
    model_id: str | None = None         # 작성·리뷰 벤더가 아닌 ARBITRATE 후보
    outcome: ArbitrationOutcome
    reasoning: str
    call_id: str
```

### 실패·토큰

```python
class FailureRecord(BaseModel):
    from_status: PostStatus
    code: FailureCode
    message: str
    occurred_at: datetime


class TokenUsage(BaseModel):
    prompt_tokens: int
    completion_tokens: int

    @property
    def total_tokens(self) -> int:
        return self.prompt_tokens + self.completion_tokens
```

`AgentCall`은 시도마다 한 건을 insert 만 한다. 재시도가 이전 호출을 덮어쓰지 않는다. 토큰 비용은 성공 여부와 무관하게 모든 `AgentCall.usage`를 합산한다.

### Publication

```python
class PublicationIntent(BaseModel):
    final_version: int
    requested_at: datetime
    attempt: int                        # 이번 호출 번호. 1부터. 저장 시점은 publish_attempts + 1
    failed_at: datetime | None = None   # 결과가 확정된 실패를 반영한 시각. READY_TO_PUBLISH 인데 None 이면 직전 호출 결과를 모른다
    external_post_id: str | None = None # Post BE 게시글 id. 로컬이 PUBLISHED가 되기 전에 중단되면 여기만 남는다


class Publication(BaseModel):
    published_at: datetime
    final_version: int
    external_post_id: str               # Post BE 게시글 id. 발행 응답 또는 CONFIRM_PUBLISHED 결정에서 온다
    peer_review_note: str               # 하단 부록 마크다운 (05 문서 템플릿으로 생성). 발행 때 본문과 함께 Tiptap 으로 변환한다
    approved_by: str | None             # 승인 토글이 켜져 있을 때 사람 식별자
    derived_at: datetime | None = None  # 글 단위 파생 문서(post_glossary·post_provenance)를 다 쓴 시각. 없으면 정리가 마저 쓴다 (08 문서 2.2절)
```

### 예산

글마다 `PostDraft.token_limit` 하나만 둔다. 사용량은 그 글의 `AgentCall.usage` 합계이고, 별도 계정 문서는 없다. 주제 제안 호출은 글이 없고 호출 수가 상한(`MAX_TOPIC_ATTEMPTS × 3 × MAX_LLM_RETRIES`)으로 묶여 예산을 검사하지 않는다. 라운드 상한은 두지 않는다. 검사 방식은 04 문서 6절.

## 4. 불변식

`PostDraft`에서 코드로 강제한다(메서드 안에서 `ValueError` 또는 도메인 예외).

| 불변식 | 설명 |
|---|---|
| `author_slot` 은 `reviewer_assignments` 의 슬롯이 아니다 | 옛 글에만 슬롯이 있다. 지금은 라우트가 작성 벤더를 피한다 |
| 배정의 주 렌즈는 서로 다르고, 주 렌즈·보조 렌즈의 합이 세 렌즈 전부다 | 리뷰어를 주 렌즈로 가리는 근거다 |
| 세 슬롯의 페르소나는 서로 다르다 | 라운드 시작 시 |
| `versions[-1].version_number == len(versions)` | 버전 번호는 1부터 연속. 게이트 실패본도 번호를 받는다 |
| 리뷰를 마친 버전(`passed_validation` 이고 리뷰 완료)은 `1 + iteration` 개 이하, 사람 재수정 버전은 그 위에 별도 | 개수 상한으로 게이트 재시도를 자르지 않는다 |
| `origin_cause == REVIEW` 인 버전의 차단 항목 해소는 직전 `open_blocking_items` 와 ID가 정확히 같고, `UPHOLD_REVIEWER` 는 `ACCEPTED` 다. 추가 해소는 그 리뷰의 `MINOR`·`NITPICK` 에 대한 `ACCEPTED` 또는 `DEFERRED` 뿐이다 | 첫 초안(`origin_cause=None`)과 사람 재수정(`origin_cause=HUMAN`), 그리고 그들의 게이트 재작성은 제외 |
| `REJECTED_WITH_REASON`·`OUT_OF_SCOPE` 는 `reason` 필수 | 근거 없는 기각 금지 |
| `OUT_OF_SCOPE` 는 차단 항목에만 | 게이트가 비차단 항목의 사용을 거절한다 |
| `DEFERRED` 는 MINOR·NITPICK 에만 | 차단 등급을 미룰 수 없다 |
| 버전 하나에 같은 `lens` 의 같은 `scope` 리뷰는 하나 | 중복 리뷰 방지(멱등) |
| `ReviewItem.lens` 는 해당 리뷰어의 `primary_lens` 또는 `secondary_lens` 중 하나다 | 중재·SPOT_CHECK 에 정확한 렌즈를 전달하기 위함 |
| `ReviewItem.source_review_id`는 실제 부모 리뷰다 | 문자열 `item_id`를 파싱하지 않고 원본과 소유자(`reviewer_of`)를 찾는다 |
| `SPOT_CHECK` 리뷰어는 직전 리뷰 완료 버전에서 미결 차단 항목(`open_blocking_items` 중 본인 몫)을 가진 렌즈이고, 사실 확인 렌즈는 항목이 없어도 받는다 | 게이트만 실패한 버전은 직전이 아니다. 수정으로 들어온 새 주장을 확인하는 곳이 여기뿐이다 |
| 중재 모델은 작성 벤더도 그 항목의 리뷰 벤더도 아니다 | 라우트 선택이 보장한다(`select_arbiter`) |
| `content_markdown` 은 모든 필수 섹션 앵커를 포함한다 | VALIDATING 게이트가 검사(04 문서) |
| `REVISING` 이면 `revision_cause` 가 있다. `DRAFTING` 에서는 수정본을 쓸 때 있다 | DRAFTING 의 호출 종류(`WRITE_DRAFT`·`REVISE_DRAFT`)와 새 버전의 `origin_cause` 를 정한다 |

## 5. `PostDraft` 도메인 메서드

상태 전이는 메서드 안에서 일어난다. 메서드는 I/O를 하지 않는다. LLM 호출과 저장은 서비스가 한다(04·06 문서). `apply_evaluation` 은 `services/evaluation.py` 가 만든 `EvaluationOutcome` 을 받아 전이만 한다. 모델이 서비스를 import 하지 않도록 `EvaluationOutcome` 타입은 `models/` 에 둔다.

| 메서드 | 전제 상태 | 결과 상태 | 설명 |
|---|---|---|---|
| `start_drafting(max_iterations)` | `TOPIC_LOCKED`, `REVISING` | `DRAFTING` (14행의 방어 경우 `ESCALATED`) | `revision_cause` 에 따라 iteration 증가 여부가 갈린다 (03 문서 13a~14행). `revision_cause` 는 지우지 않는다 |
| `submit_version(title, summary, content_markdown, section_ids, resolutions, author_note, call_id)` | `DRAFTING` | `VALIDATING` | 버전 추가. `passed_validation=False`. 새 버전의 `origin_cause` 는 `revision_cause` 로 정한다(`GATE` 면 직전 버전 값, `REVIEW`·`HUMAN` 이면 그 값, 없으면 `None`). `revision_cause`, `approved_by`·`approved_at` 을 지운다 |
| `finish_polish(record)` | `POST_REVIEW`(`POLISH`) | 변화 없음(`FINAL_READ`) | 패치 없이 다듬기를 끝낸다. 새 버전을 만들지 않는다 |
| `attach_polished_version(content_markdown, section_ids, record, call_id)` | `POST_REVIEW`(`POLISH`) | 변화 없음(`FINAL_READ`) | 게이트를 통과한 다듬은 본문을 `origin_cause=POLISH`, `passed_validation=True` 인 새 버전으로 붙인다. 승인을 지운다 |
| `record_final_read(final_read)` | `POST_REVIEW`(`FINAL_READ`) | 변화 없음(`ASSESS_LEVEL`) / `ESCALATED` | 차단 종류가 있으면 `FINAL_READ_FLAGGED` |
| `record_level_assessment(assessment)` | `POST_REVIEW`(`ASSESS_LEVEL`) | 변화 없음(`GLOSSARY`) | 수준을 라벨로 남긴다. 막지 않는다 |
| `finish_glossary(record)` | `POST_REVIEW`(`GLOSSARY`) | `READY_TO_PUBLISH` | 용어 추출 결과를 남긴다. 호출 실패·라우트 없음으로 건너뛴 기록도 같은 전이다 (08 문서 D-64) |
| `pass_validation()` | `VALIDATING` | `READY_TO_PUBLISH` / `IN_REVIEW` / `IN_SPOT_CHECK` | `validation_retries=0`. `origin_cause == HUMAN` 이면 바로 `READY_TO_PUBLISH`. 그 외에는 리뷰 완료 이전 버전(`last_reviewed_version`)이 없으면 `IN_REVIEW`, 있으면 `IN_SPOT_CHECK`. `IN_SPOT_CHECK` 인데 미결 항목을 가진 리뷰어가 없으면 `ValueError`(정상 흐름에서는 생기지 않는 방어) |
| `fail_validation(errors, max_validation_retries)` | `VALIDATING` | `REVISING` (`cause=GATE`) 또는 `FAILED` | 실패 횟수를 1 올린 값이 상한 미만이면 전자, 같으면 `FailureRecord(GATE_RETRIES_EXHAUSTED)`. `iteration` 과 `origin_cause` 는 유지 |
| `add_review(review)` | `IN_REVIEW`, `IN_SPOT_CHECK` | 변화 없음 | `derived_verdict` 를 저장. 중복이면 무시 |
| `open_blocking_items(version)` | – | – | 해당 버전의 미결 차단 항목 집합 (04 문서 5절). 현재 이력으로 다시 계산하므로 나중 버전의 `UPHOLD_AUTHOR` 중재가 이전 버전의 집합도 줄인다. 흐름은 `last_reviewed_version` 의 집합만 쓴다 |
| `required_reviewers()` | – | – | 렌즈 목록. FULL 은 배정된 렌즈 전부. SPOT_CHECK 는 직전 리뷰 완료 버전의 `open_blocking_items` 에 본인 항목이 남은 렌즈와 사실 확인 렌즈 |
| `pending_reviewers()` | – | – | `required_reviewers()` 중 아직 응답하지 않은 렌즈. 서비스는 이 렌즈만 호출한다 |
| `reviewer_of(item)` | – | – | 지적을 낸 리뷰어의 주 렌즈. 옛 글의 지적은 보조 렌즈일 수 있어 `item.lens` 로 가리지 않는다 |
| `checks_facts(lens)` | – | – | 웹 검색으로 사실을 확인하는 리뷰어인지. 옛 글의 슬롯 리뷰어는 아니다 |
| `authored_publishable_version` | – | – | 다듬기 전 발행본. 요약 노트의 버전 수와 fast-track 이 쓴다 |
| `is_review_complete()` | – | – | `pending_reviewers()` 가 비었다 |
| `apply_evaluation(outcome)` | 리뷰 완료 또는 중재 완료 | outcome 이 정한 상태 | `REVISING` 이면 `cause=REVIEW`. `IN_ARBITRATION` 이 아니면 `last_reviewed_version` 을 현재 버전으로 둔다 |
| `add_arbitration(arb)` | `IN_ARBITRATION` | 변화 없음 | 항목 하나만 도착해도 전이하지 않는다 |
| `deadlocked_items()` | – | – | 현재 버전의 교착 항목(04 문서 5절). 이전 버전에서 중재받은 항목은 빠진다 |
| `pending_arbitration_items()` | – | – | 교착 항목 중 이번 버전 중재가 없는 것. 서비스는 이 항목만 `ARBITRATE` 를 호출한다 |
| `arbitration_complete()` | – | – | `pending_arbitration_items()` 가 비었다 |
| `escalate(reason)` | 종결·`ESCALATED`·`FAILED` 가 아닌 상태 | `ESCALATED` | `escalation_reason`만 기록한다 |
| `resolve_escalation(decision, decided_by, instruction?, publication?)` | `ESCALATED` | `READY_TO_PUBLISH` / `REVISING` (`cause=HUMAN`) / `REJECTED` / `PUBLISHED` | 사람 결정. `REVISE` 는 iteration 을 바꾸지 않는다. `PUBLISH_AS_IS` 의 발행본은 마지막 `passed_validation` 버전이고, 그런 버전이 없으면 거절한다. `PUBLISH_AS_IS`·`REVISE` 는 `publish_attempts=0` 으로 두고 `publication_intent` 를 지운다. `CONFIRM_PUBLISHED(external_post_id)` 는 `PUBLISH_UNCERTAIN` 에서만 허용하고 `Publication` 을 기록한다. `PUBLISH_UNCERTAIN` 에서는 어느 결정이든 사람이 블로그를 먼저 확인한다 |
| `reject_by_abort(reason)` | `PUBLISHED`·`REJECTED` 외 전부 (`ESCALATED`, `FAILED` 포함) | `REJECTED` | 라운드 중단. `publication_intent` 는 유지한다. `is_active` 가 거짓인 글도 대상이므로 글 코루틴이 아니라 `abort_round` 가 직접 호출한다 |
| `mark_failed(code, message)` | 종결·`ESCALATED`·`FAILED` 가 아닌 상태 | `FAILED` | 현재 상태로 `FailureRecord(from_status, code)`를 만들어 기록한다. 병렬 단계는 서비스가 다른 호출이 끝나기를 기다려 성공한 리뷰·중재를 먼저 넣는다 |
| `retry_from_failed(token_limit=None)` | `FAILED` | `failure.from_status`. `GATE_RETRIES_EXHAUSTED` 면 `REVISING` (`cause=GATE`) | 게이트 실패면 `validation_retries=0`. 어느 사유든 새 `token_limit` 을 받을 수 있다(병렬 단계는 사유가 여럿일 수 있다). 저장된 리뷰·중재는 그대로라 서비스는 빠진 호출만 한다 |
| `discard(by)` | `FAILED` | `REJECTED` | 사람이 그 글만 폐기한다. 레지스트리 `RELEASED` 는 서비스가 따라 쓴다 |
| `record_approval(by, requires_approval)` | `READY_TO_PUBLISH` | 변화 없음 | 승인 토글이 켜져 있고 아직 승인이 없을 때만. 토글이 꺼져 있거나 재승인이면 `InvalidTransitionError`. 그때는 발행 코루틴이 돌 수 있어 이 쓰기가 결과 반영과 겹친다(04 문서 8절 9단계) |
| `begin_publish(requires_approval)` | `READY_TO_PUBLISH` | 변화 없음 | 발행 호출 직전 `PublicationIntent(attempt = publish_attempts + 1)` 를 남긴다. 승인 대기이거나 결과를 모르는 의도가 있으면 거절한다 (04 문서 8절 4·6단계) |
| `fail_publish(max_publish_attempts)` | `READY_TO_PUBLISH` | 변화 없음 / `ESCALATED` | 결과가 확정된 실패. 의도에 `failed_at` 을 쓰고 `publish_attempts` 를 그 `attempt` 로 맞춘다. 상한이면 `PUBLISH_FAILED` (03 문서 28a·28행) |
| `publish(publication, requires_approval)` | `READY_TO_PUBLISH` | `PUBLISHED` | 승인 토글이 켜져 있으면 `approved_by` 가 있어야 한다. `begin_publish` 가 남긴 의도가 있어야 하고 `publication.final_version` 은 그 의도의 버전과 같아야 한다 |
| `has_uncertain_publication` | – | – | `failed_at` 없는 의도가 있다. 서비스는 이때 호출하지 않고 `escalate(PUBLISH_UNCERTAIN)` (03 문서 28b행) |
| `awaits_approval(requires_approval)` | – | – | `READY_TO_PUBLISH`, 토글 켜짐, `approved_by` 없음. 러너가 이 글을 띄우지 않는다 |
| `external_post_id` | – | – | 블로그에 올라간 게시글 id. `Publication` 이 없으면 의도에 남은 것(중단과 겹쳐 `PUBLISHED` 로 쓰지 못한 글, 회수 대상). 요약 응답·CLI 상태가 이 값을 보여 준다 |
| `is_active` | – | – | `PUBLISHED`, `REJECTED`, `ESCALATED`, `FAILED` 가 아니면 참. 러너가 이 글만 코루틴으로 돌린다 |

## 6. 저장 (MongoDB)

| 컬렉션 | 문서 | 인덱스 |
|---|---|---|
| `agent_rounds` | `PublishingRound` | `(status, created_at)`. `status` 부분 unique(`status == "TOPIC_SELECTION"` 인 문서만): 주제 선정 라운드는 하나 |
| `agent_drafts` | `PostDraft` (versions·reviews 내장) | `round_id`, `status`, `(status, updated_at)` |
| `agent_calls` | `AgentCall` | `draft_id`, `(round_id, kind)` |
| `agent_pipeline_versions` | `PipelineVersion` (09 문서 2.2절: 슬롯·페르소나·라우트·수준 기준표·프롬프트·조절 값). insert 만 한다. 활성은 `seq` 가 가장 큰 문서 | `seq` **unique**, `content_hash`, `created_at` |
| `agent_pipeline_counters` | `{_id: "version", seq}` | `_id` 만. `find_one_and_update($inc)` 로 버전 순번 |
| `agent_routes` | 옛 호출별 후보, `SLOTS`, `LEVEL_CRITERIA` 문서. 1a 부터 라운드가 읽지 않는다. `agents pipeline bootstrap --routes mongo` 의 입력으로만 남는다(3단계 삭제) | `_id` 만 쓴다 |
| `agent_topics` | `TopicRegistryEntry` | `(status, keywords)`, `round_id`, `published_at` |
| `glossary_headwords` | `GlossaryHeadword` (08 문서 2.1절) | `lookup_keys` **unique**(다중키: 한 조회 키는 표제어 하나에만), `status` |
| `glossary_senses` | `GlossarySense` | `headword_key`, `(status, headword_key)`, `post_ids` |
| `post_glossary` | `PostGlossary` (글→뜻·표면형) | `_id`(게시글 id), `draft_id` unique, `items.sense_key` |
| `glossary_counters` | `{_id: "sense", seq}` | `_id` 만. `find_one_and_update($inc)` 로 뜻 키 순번 |
| `post_provenance` | `PostProvenance` (08 문서 3.2절) | `_id`(게시글 id), `draft_id` unique, `round_id` |

- `PostDraft`에 버전·리뷰를 내장하는 이유: 글 한 편의 전체 이력을 한 번에 읽고, 한 문서 쓰기는 원자적이다. 게이트 실패본이 더해져도 버전은 한 자릿수다.
- 낙관적 잠금: `PostDraft.revision`과 `PublishingRound.revision`. 조건은 `{"_id": id, "revision": n}`, 갱신은 `{"$set": {...}, "$inc": {"revision": 1}}`. 글 하나는 코루틴 하나만 진행하고 사람 결정은 코루틴이 돌지 않는 상태(승인 대기, `ESCALATED`, `FAILED`)에만 쓰므로, 글 코루틴과의 충돌은 라운드 중단에서만 난다. 코루틴은 결과를 버리고 다시 읽는다. 주제 코루틴의 라운드 저장도 같다. 사람·관리자 쓰기는 충돌하면 다시 읽고, 전제 상태가 여전히 맞으면 재적용하고 아니면 409다.
- 결정론적 ID: 글은 `"{round_id}:{slot}"`, 레지스트리 항목은 그 글 ID를 쓴다. 주제 확정은 없을 때만 만든다(`$setOnInsert`). 다시 해도 이미 있는 문서(중단으로 정리된 것 포함)를 되살리지 않는다.
- 네 컬렉션은 검색 서비스와 같은 Mongo, 같은 DB(`MONGODB_URI`)에 둔다(07 문서 D-43). 다중 문서 트랜잭션을 쓰지 않으므로 standalone이어도 된다. 여러 문서를 고치는 동작은 커밋 지점이 되는 쓰기 하나를 정하고, 나머지를 결정론적 ID로 멱등하게 쓴다. 순서와 정리는 04 문서 1절.
- `AgentCall.messages`는 그 시도에 실제로 보낸 메시지다. 본문 20,000자와 리뷰를 합쳐도 수십 KB라 16MB 문서 한도에 여유가 있다. 스키마 위반 재시도는 첫 시도 메시지 뒤에 직전 실패 출력과 오류 안내를 붙인다.
- `PublicationIntent`는 외부 호출 전에 `PostDraft`에 저장한다. Post BE에는 멱등 키가 없으므로, `READY_TO_PUBLISH`인데 `failed_at`이 없는 의도가 남아 있으면 직전 호출 결과를 모르는 것으로 보고 다시 호출하지 않는다(04 문서 8절).
