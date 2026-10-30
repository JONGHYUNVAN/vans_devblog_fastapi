# 06. FastAPI 아키텍처

자매 저장소 `vans_devblog_fastapi`의 [CONVENTIONS.md](../../vans_devblog_fastapi/CONVENTIONS.md)의 계층형 구조(routers → services → repositories → clients, 엔티티는 models, DTO는 schemas)를 그대로 따른다. 이 문서는 파이프라인이 그 구조 안에서 어디에 놓이는지, 기존 서비스와 다른 점(비동기 Mongo, 장기 실행 러너, 프롬프트 자산)을 어떻게 수용하는지 정한다.

## 1. 배치: 별도 서비스 (권장)

기존 `vans_devblog_fastapi`는 검색·동기화·KIS 서빙 서비스다. 파이프라인은 성격이 다르다.

| 항목 | 검색 서비스 | 에이전트 파이프라인 |
|---|---|---|
| 요청 모델 | 짧은 동기 요청, 캐시 | 분 단위 장기 실행 백그라운드 태스크 |
| DB 드라이버 | 동기 pymongo, 동기 elasticsearch | 비동기(`AsyncMongoClient`) |
| 장애 영향 | 블로그 검색 다운 | 글 생성 지연 (사용자 영향 없음) |
| 비밀 | ES·Mongo·KIS 키 | LLM 벤더 키 3종 + 발행용 에이전트 관리자 계정 비밀번호 |
| 배포 주기 | 안정 | 프롬프트·페르소나 튜닝으로 잦음 |

따라서 **같은 컨벤션을 따르는 별도 저장소/서비스**(`vans_devblog_agents`, 이 저장소)로 만들고, 발행은 Post BE(`vans_story_be_post`)의 기존 글 생성 API를 `clients/publisher/`로 호출한다. Post BE는 바꾸지 않고, 사람이 쓴 글과 같은 데이터를 보낸다(07 문서 D-01, D-27). 컨벤션 문서는 구현 시작 시 `vans_devblog_fastapi`에서 복사해 와 이 저장소 루트에 둔다.

나중에 검색 서비스와 합치기로 결정하더라도 아래 구조를 그쪽 `app/` 아래 그대로 옮길 수 있다. 모듈 이름 충돌은 없다.

## 2. 패키지 구조

```
app/
├─ main.py                     lifespan: Mongo(async) 연결, LLM 클라이언트, PipelineRunner 시작·종료
├─ dependencies.py             get_round_service, get_draft_decision_service, verify_internal_key
├─ core/
│  ├─ config.py                Settings: 벤더 키, 슬롯 바인딩, 반복·예산 상수, 승인 토글, 발행 계정
│  └─ exceptions.py            AppError + InvalidTransitionError(409), ConcurrentUpdateError, BudgetExceededError, LLMOutputError, LLMRetriesExhaustedError, PublishFailedError, PublishUncertainError
├─ routers/
│  └─ agent_rounds.py          내부 API (X-Internal-Key)
├─ schemas/
│  └─ agent_pipeline.py        RoundCreate, RoundPublic, DraftPublic, DraftHistory, EscalationDecision, DraftApproval, DraftRetry, DraftDiscard, …
├─ services/
│  ├─ round.py                 RoundService: 라운드 생성·종결·중단, 렌즈 매트릭스 검증, 정리(reconcile)
│  ├─ topic_selection.py       TopicSelectionService.drive(round_id): 제안 수집, 중복 판별, 배정, 확정 (Phase 1)
│  ├─ draft_pipeline.py        DraftPipelineService.drive(draft_id): 글 하나를 사람 대기나 종결까지 진행
│  ├─ draft_decision.py        DraftDecisionService: 사람 결정(승인, 격리 해소, 재개, 폐기)
│  ├─ validation.py            ValidationGate: 결정론적 검사 (LLM 없음). Tiptap 변환 가능 여부 포함
│  ├─ evaluation.py            EvaluationEngine: derived_verdict, evaluate 규칙 (순수 함수)
│  ├─ publishing.py            PublishingService: 승인 확인, 요약 노트 생성, Tiptap 변환·CreatePostDto 매핑, 결과 불명 판정, Publisher 호출
│  ├─ runner.py                PipelineRunner: 코루틴 표, 주기 스캔, 기동·종료
│  └─ agent_calls.py           AgentCallService.call: 예산 검사, 호출, 재시도, 영수증
├─ agents/                     [프롬프트 자산 계층] 도메인 → 프롬프트 입력, LLM 출력 → 도메인
│  ├─ personas.py              PersonaProfile 상수 3종, 렌즈 정의
│  ├─ contracts.py             05 문서의 입출력 Pydantic 모델
│  ├─ builders.py              build_review_input(draft, slot) 등. 블라인드 처리와 결정론적 메시지 렌더링
│  ├─ parsers.py               LLM 원문 → 계약 모델. 실패는 LLMOutputError
│  └─ prompts/
│     ├─ v1/
│     │  ├─ propose_topic.md
│     │  ├─ write_draft.md
│     │  ├─ revise_draft.md
│     │  ├─ review_full.md
│     │  ├─ review_spot_check.md
│     │  └─ arbitrate.md
│     └─ registry.py           PROMPT_VERSION = "v1", 템플릿 로더
├─ repositories/
│  ├─ agent_round.py           agent_rounds 컬렉션, 주제 선정 라운드 부분 unique 인덱스
│  ├─ post_draft.py            agent_drafts 컬렉션, revision 기반 낙관적 잠금
│  ├─ agent_call.py            agent_calls 컬렉션, 글별 usage 합계
│  └─ topic_registry.py        agent_topics 컬렉션
├─ models/
│  ├─ enums.py                 02 문서 열거형
│  ├─ publishing_round.py      PublishingRound, TopicAttempt
│  ├─ post_draft.py            PostDraft + 도메인 메서드, DraftVersion, PeerReview, ReviewItem, ItemResolution, Arbitration, PublicationIntent, Publication, FailureRecord, EvaluationOutcome
│  ├─ agent_call.py            AgentCall, TokenUsage
│  ├─ topic.py                 TopicProposal, CategoryOption, TopicRegistryEntry
│  ├─ tiptap.py                markdown_to_tiptap: 04 문서 8절 변환표. 순수 함수. 변환 불가는 ValueError
│  └─ model_binding.py         ModelBinding
└─ clients/
   ├─ mongo.py                 create_async_mongo_client
   ├─ llm/                     외부 시스템 하나 = 하위 패키지 하나
   │  ├─ base.py               LLMClient Protocol: complete(messages, schema, params, timeout) -> LLMResponse(text, usage)
   │  ├─ anthropic.py
   │  ├─ google.py
   │  ├─ xai.py
   │  └─ registry.py           provider 문자열 → 클라이언트. 세마포어 보관
   └─ publisher/
      ├─ base.py               Publisher Protocol: list_categories() -> list[PostCategory], publish(PostCreatePayload) -> PublishResult
      └─ post_be.py            vans_story_be 로그인(봇 계정, 발행마다) + vans_story_be_post GET /api/v1/categories, POST /api/v1/posts
cli/
└─ __main__.py                 typer: start-round, status, show, run, resolve, approve, retry, discard, abort, replay
```

### `agents/`의 위치

CONVENTIONS 1절에 없는 패키지다. 프롬프트 템플릿과 입출력 변환은 서비스 로직도, 외부 연결도 아니다. 의존 규칙에 다음 행을 추가한다.

| 계층 | import 해도 되는 것 | import 하면 안 되는 것 |
|---|---|---|
| `agents/` | `models`, `core` | `services`, `repositories`, `clients`, `schemas`, `fastapi` |

`services/`는 `agents/`를 import 할 수 있다. 방향: `services → agents → models`.

### 의존 방향

```
routers, cli ─→ services ─→ repositories ─→ clients(mongo)
                  │  │  │
                  │  │  └─→ clients/llm, clients/publisher   (KIS 와 같은 예외: 외부 API 가 데이터 원천)
                  │  └─→ agents ─→ models
                  └─→ schemas
```

## 3. 계층별 책임 (이 기능에 한정)

### `models/post_draft.py`

- 상태 전이는 전부 `PostDraft` 메서드 안에 있다. 잘못된 전이는 `InvalidTransitionError`.
- I/O 없음. 테스트는 DB 없이 돈다.
- 판정 규칙은 `services/evaluation.py` 의 순수 함수다. `PostDraft.apply_evaluation(outcome)` 은 그 결과로 전이만 한다. 모델이 서비스를 import 하지 않기 위해서다. `EvaluationOutcome` 타입은 `models/post_draft.py`에 둔다.

### `services/draft_pipeline.py`

```python
class DraftPipelineService:
    def __init__(self, drafts: PostDraftRepository, rounds: RoundService, calls: AgentCallService,
                 gate: ValidationGate, publishing: PublishingService, settings: Settings) -> None: ...

    async def drive(self, draft_id: str) -> None:
        """글 하나를 사람 대기나 종결까지 진행한다. 러너가 글마다 asyncio 태스크 하나로 돌린다."""
        while True:
            draft = await self._drafts.get(draft_id)
            if draft is None or not await self._should_run(draft):
                return
            try:
                draft = await self._step(draft)
            except ConcurrentUpdateError:
                continue  # 라운드 중단과 겹쳤다. 결과를 버리고 다시 읽는다
            if draft.status in (PostStatus.PUBLISHED, PostStatus.REJECTED):
                await self._rounds.close_if_complete(draft.round_id)

    async def _step(self, draft: PostDraft) -> PostDraft:
        """현재 상태에서 한 걸음. LLM 호출을 직접 await 하고, 결과를 반영해 한 번 저장한 글을 돌려준다."""
        match draft.status:
            case PostStatus.TOPIC_LOCKED | PostStatus.REVISING: return await self._start_drafting(draft)
            case PostStatus.DRAFTING:                            return await self._write(draft)
            case PostStatus.VALIDATING:                          return await self._validate(draft)
            case PostStatus.IN_REVIEW | PostStatus.IN_SPOT_CHECK: return await self._review(draft)
            case PostStatus.IN_ARBITRATION:                      return await self._arbitrate(draft)
            case PostStatus.READY_TO_PUBLISH:                    return await self._publish(draft)
```

- `_should_run`은 라운드가 `IN_PROGRESS`이고, 글이 `is_active`이며, 승인 대기(`READY_TO_PUBLISH`, 승인 토글 켜짐, `approved_by` 없음)가 아닐 때만 참이다. 거짓이면 루프를 끝낸다. 사람 결정이 상태를 바꾸면 다음 스캔이 다시 띄운다.
- `_write`는 `revision_cause`(없으면 v1 작성)로 호출 종류(`WRITE_DRAFT`·`REVISE_DRAFT`)와 입력을 정한다. 새 버전의 `origin_cause`가 `HUMAN`이면 게이트 재작성이어도 `human_instruction`을 넣는다. `DRAFTING`에서 재시작해도 같은 호출을 만든다.
- `_review`·`_arbitrate`는 응답이 없는 슬롯·항목만 `asyncio.gather(..., return_exceptions=True)`로 병렬 호출한다. 하나가 `LLMRetriesExhaustedError`나 `BudgetExceededError`로 끝나도 나머지를 기다리고, 성공한 결과를 넣은 뒤 `mark_failed`와 함께 한 번 저장한다(04 문서 6절). 사유가 여럿이면 `BUDGET_EXCEEDED`를 남긴다. 단일 호출 단계에서 같은 예외가 나면 바로 `mark_failed`로 저장한다.
- 저장은 `drafts.save(draft)`가 `revision`을 검사한다. 충돌(라운드 중단)이면 `ConcurrentUpdateError`이고, 루프가 다시 읽는다. 버린 호출의 비용은 `AgentCall`에 남는다.
- 그 밖의 예외는 `drive`를 띄운 러너가 받아 `FAILED(INTERNAL_ERROR)`로 저장한다(아래 runner).

### `services/draft_decision.py`

- 사람 결정 API·CLI(승인, 격리 해소, 재개, 폐기)를 맡는다. 글을 읽어 도메인 메서드를 부르고 `revision` 조건으로 저장한다. 충돌이면 다시 읽어 전제 상태가 여전히 맞을 때만 재적용하고, 아니면 409 `InvalidTransitionError`다.
- 글을 종결로 보낸 결정(`REJECT`, `discard`, `CONFIRM_PUBLISHED`)은 이어서 레지스트리를 따라 쓰고 `RoundService.close_if_complete`를 부른다(04 문서 1절 표).
- 진행은 하지 않는다. 다음 스캔이 글 코루틴을 띄운다.

### `services/agent_calls.py`

- `call(kind, round_id, draft, slot, messages, schema, target_version=None, topic_attempt=None, item_id=None) -> 계약 모델`: 재시도 루프를 이 메서드 안에서 끝낸다. 성공하면 파싱된 계약 모델을 돌려주고, 소진하면 `LLMRetriesExhaustedError`를 던진다.
- 시도마다: ① 글 호출이면 소프트 예산 검사. 그 글의 `AgentCall.usage` 합계 + 추정치(메시지 문자 수 + `max_output_tokens`) > `draft.token_limit`이면 호출하지 않고 `BudgetExceededError`. ② 벤더 세마포어 안에서 타임아웃(`review_timeout_s` 또는 `draft_timeout_s`)을 걸고 호출. ③ 성공·실패와 무관하게 `AgentCall`을 insert. ④ `agents/parsers.py`로 파싱.
- 실패 처리: 네트워크·5xx·429·타임아웃은 지수 백오프 뒤 같은 메시지로, `LLMOutputError`(스키마 위반)는 첫 시도 메시지 뒤에 직전 실패 출력과 오류 안내를 붙여 다시 부른다. 시도 수는 이 메서드의 지역 값이라 재개하면 새로 센다.
- 주제 제안 호출(`draft=None`)은 예산을 검사하지 않는다(02 문서 3절 예산).
- 벤더별 `asyncio.Semaphore`는 `clients/llm/registry.py`가 가진다.

### `services/runner.py`

- lifespan이 `runner.start()`로 시작하고, 종료 시 코루틴을 전부 cancel한다. 끊긴 호출은 다음 기동 때 다시 한다.
- `start()`: `scan()`을 `SCAN_INTERVAL_S` 주기로 돌린다. 첫 스캔은 기동 직후다.
- `scan()`: 먼저 `RoundService.reconcile()`로 정리한다. `ABORTED` 라운드의 남은 글·레지스트리를 정리하고, `PUBLISHED`·`REJECTED` 글의 레지스트리를 맞추고, `close_if_complete`를 다시 확인한다(04 문서 1절 표). 그다음 코루틴이 없는 진행 대상만 띄운다. 러너는 `id → asyncio.Task` 표로 같은 대상에 코루틴이 둘 생기지 않게 한다.
- 코루틴의 예상하지 못한 예외는 `logger.exception`을 남기고 글을 `FAILED(INTERNAL_ERROR)`로, 주제 선정이면 라운드를 `ABORTED(INTERNAL_ERROR)`로 저장한다. 다음 스캔이 같은 오류로 호출을 되풀이하지 않게 하기 위해서다. 경계이므로 `except Exception`을 허용한다(CONVENTIONS 5절).
- uvicorn 워커 1개 전제. 주석으로 남긴다. `agents run`을 서버와 동시에 띄우지 않는다. 다중 인스턴스가 필요해지면 글 단위 실행 소유권(lease)과 트랜잭션을 다시 들여야 한다(07 문서 Q-04).

### `clients/publisher/`

- `PostCreatePayload`·`PostCategory`는 `base.py`에 둔다(`clients`는 `models`를 import 하지 않는다). JSON 필드 이름은 `CreatePostDto` 그대로(camelCase)다. 평평한 카테고리 목록을 메인·서브 쌍(`CategoryOption`)으로 묶는 일은 서비스가 한다.
- 실패 계약: 로그인 단계의 모든 실패와 글 생성 호출의 4xx 응답·연결 거부는 `PublishFailedError`, 글 생성 호출의 응답 전 타임아웃·연결 끊김·5xx는 `PublishUncertainError`를 던진다. 로그인에서는 글이 생기지 않기 때문이다. 분류 근거는 04 문서 8절.
- 로그인 응답은 본문이 비어 있고 토큰을 `HttpServletResponse`로 내려준다(`vans_story_be`의 `AuthController.login`). 쿠키인지 헤더인지는 구현 때 확인한다. 토큰은 저장하지 않고 발행마다 받는다. 모든 호출에 `publish_timeout_s`를 건다.

### `routers/agent_rounds.py`

모두 `Depends(verify_internal_key)`. 응답은 `schemas/agent_pipeline.py`. 사람 결정은 `DraftDecisionService`가 맡는다. 상태를 바꾸는 요청은 DB만 바꾸고, 진행은 다음 스캔이 이어 간다(최대 `SCAN_INTERVAL_S` 지연).

| 메서드 | 경로 | 동작 |
|---|---|---|
| `POST` | `/api/v1/agents/rounds/` | 라운드 생성·시작. 먼저 Post BE 활성 카테고리(`activeOnly=true`)를 읽어 `categories` 스냅샷을 만들고(실패하면 라운드를 만들지 않는다), 라운드를 `TOPIC_SELECTION`으로 만든다. 다른 `TOPIC_SELECTION` 라운드가 있으면 409. 바디 `RoundCreate(theme_keyword?, bindings?)` |
| `GET` | `/api/v1/agents/rounds/` | 목록(상태 필터) |
| `GET` | `/api/v1/agents/rounds/{round_id}/` | 라운드 + 글 3편 요약 |
| `POST` | `/api/v1/agents/rounds/{round_id}/abort/` | 중단. 라운드 `ABORTED`를 먼저 쓰고 글·레지스트리를 정리한다(04 문서 1절) |
| `GET` | `/api/v1/agents/drafts/{draft_id}/` | 글 전체 이력(버전, 리뷰, 결정, 중재) |
| `GET` | `/api/v1/agents/drafts/{draft_id}/escalation/` | 사람용 요약: 최종 diff, 미해소 항목, 기각 사유 |
| `POST` | `/api/v1/agents/drafts/{draft_id}/escalation/` | `EscalationDecision(decision: PUBLISH_AS_IS \| REVISE \| REJECT \| CONFIRM_PUBLISHED, instruction?, external_post_id?, decided_by)`. `CONFIRM_PUBLISHED`는 `PUBLISH_UNCERTAIN`에서만 |
| `POST` | `/api/v1/agents/drafts/{draft_id}/approve/` | 승인 토글이 켜진 경우 발행 승인. 바디 `DraftApproval(approved_by)`. 승인은 그 버전에 대한 것이다 |
| `POST` | `/api/v1/agents/drafts/{draft_id}/retry/` | `FAILED` 재개. 바디 `DraftRetry(token_limit?)`. `token_limit`은 어느 사유든 받을 수 있고, 현재 사용량보다 커야 한다 |
| `POST` | `/api/v1/agents/drafts/{draft_id}/discard/` | `FAILED` 글 하나만 폐기. 바디 `DraftDiscard(decided_by)` |
| `GET` | `/api/v1/agents/calls/` | `draft_id`·`round_id` 필터, 토큰·지연 조회 |

경로는 trailing slash 포함(CONVENTIONS 2절). 상태 전이 위반은 409 `InvalidTransitionError`.

### `cli/`

| 명령 | 동작 |
|---|---|
| `agents start-round [--theme]` | 라운드 생성 후 종료(진행은 러너). `--wait`면 완료까지 폴링 |
| `agents run` | 서버 없이 이 프로세스에서 러너를 돌린다. 디버깅용. 서버와 동시에 쓰지 않는다 |
| `agents status <round_id>` | 글 3편 상태·iteration·토큰 |
| `agents show <draft_id> [--version N]` | 본문·리뷰·결정 출력 |
| `agents resolve <draft_id> --decision …` | 격리 해소 |
| `agents approve <draft_id>` | 발행 승인 |
| `agents retry <draft_id> [--token-limit N]` | `FAILED` 재개 |
| `agents discard <draft_id>` | `FAILED` 글 폐기 |
| `agents abort <round_id>` | 중단 |
| `agents replay <call_id>` | 저장된 메시지로 호출을 다시 실행(프롬프트 튜닝용, 상태 변경 없음) |

## 4. 설정 (`core/config.py`)

```python
class Settings(BaseSettings):
    # 벤더 키 (로그 금지)
    anthropic_api_key: SecretStr | None = None
    google_api_key: SecretStr | None = None
    xai_api_key: SecretStr | None = None

    # 슬롯 바인딩. JSON 문자열. 예: {"A": {"provider": "anthropic", "model_id": "...", "persona_id": "architect"}, ...}
    agent_slot_bindings: dict[AgentSlot, ModelBinding]

    # 파이프라인 상수 (03 문서 5절)
    max_iterations: int = 2
    max_topic_attempts: int = 2
    max_llm_retries: int = 3
    max_validation_retries: int = 2
    max_publish_attempts: int = 3
    draft_token_limit: int                # 글 소프트 상한. 라운드 상한은 없다
    topic_keyword_overlap: float = 0.5
    topic_title_overlap: float = 0.5
    topic_avoid_lookback_days: int = 180
    draft_min_chars: int = 4000
    draft_max_chars: int = 12000
    review_temperature: float = 0.2       # REVIEW, ARBITRATE. 작성은 ModelBinding.temperature
    review_timeout_s: int = 300
    draft_timeout_s: int = 600           # PROPOSE_TOPIC, WRITE_DRAFT, REVISE_DRAFT
    scan_interval_s: int = 15

    # 발행 (07 문서 D-27, D-40)
    post_be_url: str
    user_service_url: str                 # vans_story_be. 봇 계정 로그인
    publisher_email: str                  # 에이전트 전용 관리자 계정
    publisher_password: SecretStr
    publish_timeout_s: int = 30

    # 운영
    publish_requires_approval: bool = True
    prompt_version: str = "v1"
    llm_concurrency_per_provider: int = 2
```

새 변수는 `.env.example`에 같이 추가한다. 바인딩을 라운드 생성 요청으로 덮어쓸 수 있게 해 A/B 테스트를 지원한다(라운드 문서에 스냅샷 저장).

## 5. 비동기 모델

- 이 기능의 라우터·서비스·리포지토리는 전부 `async def`. 동기 I/O 호출 금지.
- MongoDB는 `pymongo.AsyncMongoClient`(pymongo ≥ 4.13). 기존 검색 서비스의 동기 `MongoClient`와 같은 프로세스에 두게 되면 연결을 별도로 만든다(`create_async_mongo_client`).
- 다중 문서 트랜잭션을 쓰지 않으므로 standalone MongoDB(현재 `vans_devblog_mongodb`)에서 돈다. 여러 문서를 고치는 동작의 순서와 정리는 04 문서 1절.
- LLM 클라이언트는 벤더 공식 SDK의 async 인터페이스 또는 `httpx2.AsyncClient`. 타임아웃 필수.
- 코드 블록 문법 검사 등 CPU 작업은 짧으므로 인라인. 외부 언어 체커를 subprocess 로 돌린다면 `asyncio.create_subprocess_exec`.

## 6. 테스트 전략

| 범위 | 방법 |
|---|---|
| `models/post_draft.py` | 전이표(03 문서) 행마다 테스트 1개. 허용되지 않는 전이가 `InvalidTransitionError`를 던지는지. DB 없음 |
| `services/evaluation.py` | 순수 함수. fast-track, 차단 1건, SPOT_CHECK 해소/미해소/교착, 중재 결과별 케이스 |
| `models/tiptap.py` | 변환표 행마다 1건, 앵커 제거, soft break → 공백, `python-snippet` → `python`, 금지 문법 각각 `ValueError`. 변환 결과의 노드·마크 이름과 attrs 키가 기존 사람 글 `content`(`tests/golden/`)에 나오는 것 안에 있는지 비교 |
| `services/validation.py` | 앵커 누락, 코드 문법 오류, 길이, 차단 항목 누락·중복·리뷰에 없는 ID, `MINOR`의 `DEFERRED`는 통과, 차단 항목의 `DEFERRED`는 실패, 변환 불가 문법(HTML·이미지·각주·`mermaid`), 앵커 소실 각 1건 |
| `services/topic_selection.py` | 충돌 없음, 1쌍 충돌 후 순위 조합, `LockedTopic`의 이전 시도 스냅샷 유지, 상한 미달 시 `ABORTED`, 제안 호출 재시도 소진은 다른 호출을 기다린 뒤 `ABORTED(LLM_FAILED)`, 두 번째 `TOPIC_SELECTION` 라운드 생성은 409이고 문서가 남지 않음, 확정 쓰기 도중 끊고 다시 돌리면 같은 ID로 글 3편·레지스트리 3건만 남고 자기 레지스트리와 충돌하지 않음, 중단과 겹친 확정이 정리된 문서를 되살리지 않음 |
| `services/draft_pipeline.py` | `FakeLLMClient` 와 `FakePostDraftRepository` 로 ① fast-track ② 수정 1회 후 승인 ③ 교착 2건 동시 발생 → `item_id` 별 `ARBITRATE` 2건 호출 및 `UPHOLD_AUTHOR` ④ `UPHOLD_REVIEWER` 판정된 v1 항목이 v3 `open_blocking_items` 에 유지되고 기각 시 게이트 실패 ⑤ 게이트 실패는 `iteration` 유지, v1 재작성 뒤에도 FULL 리뷰, 통과하면 `validation_retries=0` ⑥ 사람 `REVISE(instruction)` 이 게이트 통과 후 `IN_SPOT_CHECK` 없이 `READY_TO_PUBLISH` 직행, 이전 승인은 지워져 다시 승인을 기다림 ⑦ `save` 충돌이면 결과를 버리고 다시 읽음 ⑧ 단계 중간에 끊고 다시 `drive`하면 마지막 저장 상태부터 이어 가고 끊긴 단계의 호출만 다시 함. `DRAFTING`에서 끊겨도 `revision_cause`로 같은 호출 종류·입력을 만듦 ⑨ `abort_round` 가 `ESCALATED`·`FAILED` 글까지 `REJECTED` 로 바꾸고 `PUBLISHED` 는 유지 ⑩ 중단과 `close_if_complete` 가 겹치면 라운드는 `ABORTED` 이고 `COMPLETED` 가 되지 않음 ⑪ 리뷰 1건이 재시도를 소진하면 다른 리뷰는 저장되고, 재개하면 빠진 리뷰만 호출 ⑫ 예상하지 못한 예외는 `FAILED(INTERNAL_ERROR)` 이고 스캔이 다시 띄우지 않음 |
| `services/agent_calls.py` | 시도마다 `AgentCall` 1건과 비용 합산, 스키마 위반 재시도의 메시지 구성(첫 메시지 + 직전 실패 출력 + 오류 안내), 5xx·429·타임아웃 백오프 재시도, 소진 시 `LLMRetriesExhaustedError`, 글 사용량 + 추정치가 상한을 넘으면 호출 전에 `BudgetExceededError`, 주제 제안은 예산을 검사하지 않음 |
| `services/runner.py` | 같은 글에 코루틴이 둘 생기지 않음, 승인 대기·`ESCALATED`·`FAILED` 글은 띄우지 않음, 스캔마다 정리가 `ABORTED` 라운드의 남은 글·레지스트리와 `PUBLISHED`·`REJECTED` 글의 레지스트리를 맞춤 |
| `services/draft_decision.py` | 결정마다 전제 상태 검사와 409, 충돌 시 다시 읽어 재적용, 종결 결정 뒤 레지스트리와 `close_if_complete`, 재개의 `token_limit` 검사 |
| `services/publishing.py` | `CreatePostDto` 매핑. 외부 호출 후 로컬 반영 전 장애를 재현하면 다시 `drive`해도 호출하지 않고 `PUBLISH_UNCERTAIN`. `PublishFailedError`는 재시도 후 상한에서 `PUBLISH_FAILED`, `PublishUncertainError`는 즉시 `PUBLISH_UNCERTAIN`. `CONFIRM_PUBLISHED`는 호출 없이 `PUBLISHED`, `PUBLISH_AS_IS`·`REVISE`는 카운터·의도를 지움. 로그인 단계 실패는 결과 확정. 요약 노트에 HTML·여러 줄이 든 지적이 있어도 변환 성공. 응답 반영 전에 `REJECTED`이면 `PUBLISHED`로 쓰지 않고 `external_post_id`만 의도에 남긴다 |
| `agents/parsers.py` | 없는 앵커·빈 체크리스트는 `LLMOutputError`. SPOT_CHECK의 MAJOR는 버리고 로그. `item_checks` 누락·중복·입력 외 ID·미해소 빈 note는 오류. 제안 카테고리가 입력 목록에 없음, 제목 100자 초과, 빈 `summary`는 오류 |
| `routers/` | `dependency_overrides`로 서비스 교체, `tests/golden/`에 응답 계약 |
| e2e | `@pytest.mark.e2e`. 실제 벤더 1회 호출로 스키마 호환 확인. 기본 실행 제외 |

`tests/fakes.py`에 `FakeLLMClient`를 두고 `(kind, slot, target_version, item_id | None) → 출력`을 딕셔너리로 스크립트한다. 토큰 사용량도 가짜로 돌려 소프트 예산을 테스트한다.
