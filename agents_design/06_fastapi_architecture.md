# 06. FastAPI 아키텍처

이 저장소 [CONVENTIONS.md](../CONVENTIONS.md)의 계층형 구조(routers → services → repositories → clients, 엔티티는 models, DTO는 schemas)를 그대로 따른다. 이 문서는 파이프라인이 그 구조 안에서 어디에 놓이는지, 기존 서비스와 다른 점(비동기 Mongo, 장기 실행 러너, 프롬프트 자산)을 어떻게 수용하는지 정한다.

## 1. 배치: 검색 서비스와 같은 저장소·같은 앱

파이프라인은 검색·동기화·KIS 서빙 서비스인 `vans_devblog_fastapi`의 같은 FastAPI 앱에 들어간다(07 문서 D-01). 성격은 다르다.

| 항목 | 검색 서비스 | 에이전트 파이프라인 |
|---|---|---|
| 요청 모델 | 짧은 동기 요청, 캐시 | 분 단위 장기 실행 백그라운드 태스크 |
| DB 드라이버 | 동기 pymongo, 동기 elasticsearch | 비동기(`AsyncMongoClient`) |
| 장애 영향 | 블로그 검색 다운 | 글 생성 지연 (사용자 영향 없음) |
| 비밀 | ES·Mongo·KIS 키 | LLM 벤더 키 3종 + 벤더별 발행 관리자 계정 비밀번호 3종 |
| 배포 주기 | 안정 | 프롬프트·페르소나 튜닝으로 잦음 |

차이는 같은 앱 안에서 이렇게 수용한다.

- 비동기 Mongo 클라이언트는 기존 동기 `MongoClient`와 별도로 lifespan이 만든다(5절). 파이프라인 경로만 `async def`다(CONVENTIONS 3절).
- 러너는 lifespan이 소유한 `asyncio` 태스크다. uvicorn 워커 1개 전제는 KIS 매니저와 같다. 코루틴의 예외는 러너 경계에서 잡으므로 검색 요청으로 번지지 않지만, 메모리와 이벤트 루프는 공유한다.
- 비밀과 상수는 같은 `Settings`에 더한다(4절). 프롬프트 튜닝도 이 서비스 배포로 나간다.
- 기존 파일(`main.py`, `dependencies.py`, `core/`, `clients/mongo.py`, `cli/__main__.py`)에는 파이프라인 항목을 덧붙인다. 아래 구조도의 그 파일 설명은 덧붙일 내용이다.

발행은 Post BE(`vans_story_be_post`)의 기존 글 생성 API를 `clients/publisher/`로 호출한다. Post BE는 바꾸지 않고, 사람이 쓴 글과 같은 데이터를 보낸다(07 문서 D-27).

## 2. 패키지 구조

```
app/
├─ main.py                     lifespan: Mongo(async) 연결, 인덱스 보장, PipelineRunner 시작·종료(DB 이름이 없으면 러너를 돌리지 않는다)
├─ dependencies.py             build_pipeline_runner·build_llm_registry·build_publisher·build_round_service·build_draft_decision_service·build_pipeline_view_service·build_agent_call_service(lifespan·CLI 공용 조립), open_async_database(CLI 연결), get_async_database·get_round_service·get_draft_decision_service·get_pipeline_view_service(라우터), verify_agents_key(AGENTS_INTERNAL_API_KEY, 07 문서 D-44). 라우터·CLI 에 리포지토리를 직접 주지 않는다
├─ core/
│  ├─ config.py                Settings: 벤더 키, 슬롯 바인딩(원문 dict. 검증은 RoundService.create_round. core 는 models 를 import 하지 않는다), 반복·예산 상수, 승인 토글, 벤더별 발행 계정
│  └─ exceptions.py            AppError + InvalidTransitionError(409), ConcurrentUpdateError(409), BudgetExceededError, LLMTransportError, LLMOutputError, LLMRetriesExhaustedError, PublishFailedError, PublishUncertainError
├─ routers/
│  └─ agent_rounds.py          내부 API (X-Internal-Key)
├─ schemas/
│  └─ agent_pipeline.py        RoundCreate, RoundPublic, DraftPublic, DraftHistory, EscalationDecision, DraftApproval, DraftRetry, DraftDiscard, RoundProgress(CLI), … 필드 타입으로만 models 를 쓴다. 변환은 services/pipeline_views.py
├─ services/
│  ├─ round.py                 RoundService: 라운드 생성(create_round: 바인딩·페르소나·렌즈 매트릭스 검증, round_id 는 uuid, round_seq 는 기존 라운드 수 + 1)·글 문서 생성(build_draft)·종결(close_if_complete)·중단(abort_round)·정리(reconcile)
│  ├─ topic_selection.py       TopicSelectionService.drive(round_id): 제안 수집, 중복 판별, 배정, 확정 (Phase 1). 한 걸음(제안·배정·확정)마다 라운드를 저장한다
│  ├─ draft_pipeline.py        DraftPipelineService.drive(draft_id): 글 하나를 사람 대기나 종결까지 진행. 발행은 DraftPublisher 계약(PublishingService 가 구현)
│  ├─ draft_decision.py        DraftDecisionService: 사람 결정(승인, 격리 해소, 재개, 폐기). 충돌이면 다시 읽어 재적용, 종결이면 레지스트리·close_if_complete 따라 쓰기
│  ├─ validation.py            ValidationGate(min_chars, max_chars, min_code_blocks).validate(draft) -> list[str]: 결정론적 검사 (LLM 없음). Tiptap 변환 가능 여부 포함. 전이는 호출자가 한다
│  ├─ evaluation.py            derive_verdict, evaluate 규칙 (순수 함수)
│  ├─ publishing.py            PublishingService(DraftPublisher 구현): 결과 불명 판정, 백오프, build_peer_review_note(요약 노트, 순수 함수), build_payload(Tiptap 변환·CreatePostDto 매핑), 의도 저장, Publisher 호출, 결과 반영과 레지스트리 PUBLISHED
│  ├─ runner.py                PipelineRunner: 코루틴 표(running), 주기 스캔, 기동·종료, 코루틴 경계의 INTERNAL_ERROR 처리
│  ├─ metrics.py               compute_metrics(drafts, calls) -> PipelineMetrics: 07 문서 4절 지표 (순수 함수)
│  ├─ pipeline_views.py        PipelineViewService: 읽기 전용(라운드 목록·상세·진행 상태, 글 이력, 격리 요약, 영수증, 지표)과 엔티티 → 응답 변환 함수(build_round_public·build_draft_public·build_escalation_summary·…). 라우터·CLI 는 리포지토리 대신 이것을 부른다
│  └─ agent_calls.py           AgentCallService.call -> CallResult(output, call_id): 예산 검사, 호출, 재시도, 영수증. replay(call, binding?) -> ReplayResult: 영수증 메시지 재전송(영수증·재시도 없음)
├─ agents/                     [프롬프트 자산 계층] 도메인 → 프롬프트 입력, LLM 출력 → 도메인
│  ├─ personas.py              PersonaProfile 상수 3종, 렌즈 정의
│  ├─ contracts.py             05 문서의 입출력 Pydantic 모델
│  ├─ builders.py              build_*_input(draft, round, …) 가 입력 모델을 만들고 render_messages(model, prompt_version) 가 [system=템플릿, user=입력 JSON] 을 만든다. 블라인드 처리 포함
│  ├─ parsers.py               문맥 검사(*_check 클로저 → AgentCallService.call 의 check 인자, 위반은 LLMOutputError 로 재시도)와 출력 → ReviewItem 변환(review_items, spot_check_items)
│  └─ prompts/
│     ├─ v1/
│     │  ├─ propose_topic.md
│     │  ├─ write_draft.md
│     │  ├─ revise_draft.md
│     │  ├─ review_full.md
│     │  ├─ review_spot_check.md
│     │  └─ arbitrate.md
│     └─ registry.py           PROMPT_VERSION = "v1", 템플릿 로더(load_template). 템플릿은 string.Template($display_name, $writer_bias)이고 .dockerignore 의 *.md 에서 예외다
├─ repositories/
│  ├─ optimistic_lock.py       replace_with_revision: revision 조건 저장. agent_round·post_draft 가 같이 쓴다
│  ├─ agent_round.py           agent_rounds 컬렉션, 주제 선정 라운드 부분 unique 인덱스
│  ├─ post_draft.py            agent_drafts 컬렉션, revision 기반 낙관적 잠금
│  ├─ agent_call.py            agent_calls 컬렉션, 글별 usage 합계
│  └─ topic_registry.py        agent_topics 컬렉션. list_active(중복 판별용 전체)·list_active_since(회피 목록용)
├─ models/
│  ├─ enums.py                 02 문서 열거형
│  ├─ publishing_round.py      PublishingRound, TopicAttempt
│  ├─ post_draft.py            PostDraft + 도메인 메서드, DraftVersion, PeerReview, ReviewItem, ItemResolution, Arbitration, PublicationIntent, Publication, FailureRecord, EvaluationOutcome
│  ├─ agent_call.py            AgentCall, TokenUsage
│  ├─ topic.py                 TopicProposal, CategoryOption, TopicRegistryEntry
│  ├─ sections.py              섹션 앵커 찾기·지우기·자르기(find_anchors, strip_anchors, section_text). 빌더·게이트·변환이 같이 쓴다
│  ├─ tiptap.py                markdown_to_tiptap: 04 문서 8절 변환표. markdown-it-py(commonmark + table·strikethrough)로 파싱한다. 순수 함수. 변환 불가는 ValueError
│  └─ model_binding.py         ModelBinding
└─ clients/
   ├─ mongo.py                 create_async_mongo_client
   ├─ llm/                     외부 시스템 하나 = 하위 패키지 하나
   │  ├─ base.py               LLMClient Protocol: complete(messages, schema, params, timeout) -> LLMResponse(text, usage). LLMParams 는 model_id·temperature·max_output_tokens(레지스트리는 provider 만 알므로 모델은 호출마다 받는다). 네트워크·429·5xx·타임아웃은 LLMTransportError(429 는 rate_limited=True), 그 밖의 4xx 는 ValueError(설정 오류, 재시도 없음). LLMMessage·LLMUsage 도 여기(clients 는 models 를 import 하지 않는다)
   │  ├─ anthropic.py          공식 SDK(anthropic 2.x, httpx2 기반) AsyncAnthropic + output_config.format=json_schema(transform_schema). SDK 재시도 off. 현행 API(2026-10-21)가 sampling 을 거부해 temperature 는 보내지 않는다
   │  ├─ google.py             REST generateContent + responseMimeType=application/json·responseJsonSchema. thoughtsTokenCount 도 completion 에 더한다
   │  ├─ xai.py                REST chat/completions(OpenAI 호환) + response_format=json_schema
   │  ├─ http.py               google·xai 가 같이 쓰는 post_json: httpx2 호출과 실패 분류
   │  └─ registry.py           provider 문자열 → 클라이언트와 ProviderGate(세마포어 + 429 뒤 cooldown 동안 1건씩). dependencies.build_llm_registry 가 키 있는 벤더만 넣는다
   └─ publisher/
      ├─ base.py               Publisher Protocol: list_categories() -> list[PostCategory], publish(PostCreatePayload) -> PublishResult. PostCreatePayload 는 CreatePostDto(camelCase alias)
      └─ post_be.py            PostBEPublisher: GET /api/v1/categories?activeOnly=true, 발행마다 vans_story_be POST /api/v1/auth/login(토큰은 응답 Authorization 헤더) → POST /api/v1/posts(Bearer). 실패 분류는 3절
cli/
└─ __main__.py                 typer `agents` 하위 명령: start-round·status·abort·run·show·resolve·approve·retry·discard·replay·metrics
```

### `agents/`의 위치

파이프라인으로 CONVENTIONS 1절에 더한 패키지다. 프롬프트 템플릿과 입출력 변환은 서비스 로직도, 외부 연결도 아니다. 의존 규칙에 다음 행이 있다.

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
class DraftPublisher(Protocol):
    """READY_TO_PUBLISH 글의 발행. 의도 저장·외부 호출·결과 반영과 그 저장을 모두 맡는다. PublishingService 가 구현한다."""
    async def publish(self, draft: PostDraft, publishing_round: PublishingRound) -> None: ...


class DraftPipelineService:
    def __init__(self, *, draft_repository: PostDraftRepository, round_service: RoundService,
                 agent_call_service: AgentCallService, gate: ValidationGate, publisher: DraftPublisher,
                 settings: Settings) -> None: ...

    async def drive(self, draft_id: str) -> None:
        """글 하나를 사람 대기나 종결까지 진행한다. 러너가 글마다 asyncio 태스크 하나로 돌린다."""
        while True:
            draft = await self._drafts.get(draft_id)
            if draft is None:
                return
            publishing_round = await self._rounds.get(draft.round_id)   # 바인딩 스냅샷과 라운드 상태
            if publishing_round is None or not self._should_run(draft, publishing_round):
                return
            try:
                draft = await self._step(draft, publishing_round)
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
            case PostStatus.READY_TO_PUBLISH:                    await self._publisher.publish(draft, round); return draft
```

- `POST_BE_URL`이 없으면 `dependencies.build_pipeline_runner`가 `PublishingService` 대신 `UnconfiguredDraftPublisher`를 꽂아 발행 단계에 닿은 글을 `ValueError` → `FAILED(INTERNAL_ERROR)`로 멈춘다. 글을 쓴 슬롯의 벤더 계정이나 `USER_SERVICE_URL`이 없으면 `PublishingService`가 같은 `ValueError`로 멈춘다(`Publisher.has_account`). 발행 의도를 저장하기 전에 멈추므로 설정을 고치고 재개하면 바로 발행한다(의도 뒤에 멈추면 `PUBLISH_UNCERTAIN`으로 격리된다).

- `_should_run`은 라운드가 `IN_PROGRESS`이고, 글이 `is_active`이며, 승인 대기(`READY_TO_PUBLISH`, 승인 토글 켜짐, `approved_by` 없음)가 아닐 때만 참이다. 거짓이면 루프를 끝낸다. 사람 결정이 상태를 바꾸면 다음 스캔이 다시 띄운다.
- `_write`는 `revision_cause`(없으면 v1 작성)로 호출 종류(`WRITE_DRAFT`·`REVISE_DRAFT`)와 입력을 정한다. 새 버전·리뷰·중재의 `call_id`는 `CallResult.call_id`다. 새 버전의 `origin_cause`가 `HUMAN`이면 게이트 재작성이어도 `human_instruction`을 넣는다. `DRAFTING`에서 재시작해도 같은 호출을 만든다.
- `_review`·`_arbitrate`는 응답이 없는 슬롯·항목만 `asyncio.gather(..., return_exceptions=True)`로 병렬 호출한다. 하나가 `LLMRetriesExhaustedError`나 `BudgetExceededError`로 끝나도 나머지를 기다리고, 성공한 결과를 넣은 뒤 `mark_failed`와 함께 한 번 저장한다(04 문서 6절). 사유가 여럿이면 `BUDGET_EXCEEDED`를 남긴다. 예상하지 못한 예외가 섞여도 성공분을 저장하고 `FAILED(INTERNAL_ERROR)`로 멈춘다(로그가 신호). 단일 호출 단계에서 같은 예외가 나면 바로 `mark_failed`로 저장한다.
- 저장은 `drafts.save(draft)`가 `revision`을 검사한다. 충돌(라운드 중단)이면 `ConcurrentUpdateError`이고, 루프가 다시 읽는다. 버린 호출의 비용은 `AgentCall`에 남는다.
- 단일 호출 단계의 그 밖의 예외는 `drive`를 띄운 러너가 받아 `FAILED(INTERNAL_ERROR)`로 저장한다(아래 runner).

### `services/publishing.py`

- `PublishingService.publish(draft, round)`가 04 문서 8절을 그대로 따른다. 글·라운드 재확인(3단계)과 승인 대기(1단계)는 글 코루틴이 매 걸음 다시 읽어 검사하므로 되풀이하지 않는다. 순서: `has_uncertain_publication`이면 호출 없이 `ESCALATED(PUBLISH_UNCERTAIN)` → 작성 슬롯 벤더의 계정이 없으면 `ValueError`(D-45) → 직전 의도가 결과 확정 실패면 지수 백오프(`2 × 2^(attempt-1)`초, `sleep` 주입) → `build_peer_review_note`(05 문서 5절 템플릿, 순수 함수) → `build_payload`(발행본 + 노트를 `markdown_to_tiptap`, `CreatePostDto` 매핑) → `begin_publish` 저장 → `Publisher.publish(payload, provider)` → 성공이면 `publish` 저장과 레지스트리 `PUBLISHED`, `PublishFailedError`면 `fail_publish`, `PublishUncertainError`면 `escalate(PUBLISH_UNCERTAIN)`.
- `begin_publish` 저장 뒤의 호출과 결과 반영은 별도 태스크로 띄워 `asyncio.shield`로 기다린다. 글 코루틴이 cancel 돼도(`runner.stop`) 결과를 반영해야 재배포마다 `PUBLISH_UNCERTAIN`이 생기지 않는다. `drain()`이 진행 중인 발행을 기다리고, `DraftPublisher` 계약에도 `drain`이 있다.
- 성공 반영 저장이 충돌하면(라운드 중단) 글을 다시 읽어 상태와 무관하게 `publication_intent.external_post_id`만 남기고 `ConcurrentUpdateError`를 다시 던진다. 운영자가 그 id로 블로그 글을 회수한다(8절 9단계). 요약 응답(`DraftPublic`)과 `agents status`의 `external_post_id`가 `Publication`이 없으면 이 값을 보여 준다.
- 노트의 LLM 텍스트(`comment`, `reason`, `reasoning`)는 줄바꿈을 공백으로 바꾸고 ASCII 구두점을 백슬래시로 이스케이프해 문단 텍스트로만 들어간다. 기각 항목은 CRITICAL > MAJOR 순으로 3건까지 인용문으로 넣고, 인용문 안의 줄은 줄 끝 `\`(hardBreak)로 나눈다.

### `services/draft_decision.py`

- 사람 결정 API·CLI(승인, 격리 해소, 재개, 폐기)를 맡는다. 글을 읽어 도메인 메서드를 부르고 `revision` 조건으로 저장한다. 충돌이면 다시 읽어 전제 상태가 여전히 맞을 때만 재적용하고, 아니면 409 `InvalidTransitionError`다. 도메인의 `ValueError`(지시문 없음 등)와 사용량 이하의 `token_limit`은 400 `InvalidRequestError`다.
- `approve`는 승인 토글이 꺼져 있거나 이미 승인된 글이면 409다. 그때는 발행 코루틴이 돌 수 있어 승인 쓰기가 결과 반영과 겹친다(04 문서 8절 9단계).
- 글을 종결로 보낸 결정(`REJECT`, `discard`, `CONFIRM_PUBLISHED`)은 이어서 레지스트리를 따라 쓰고 `RoundService.close_if_complete`를 부른다(04 문서 1절 표). `CONFIRM_PUBLISHED`의 `Publication`은 의도의 `final_version`과 다시 만든 요약 노트로 채운다.
- 라운드 중단의 레지스트리 `RELEASED`는 `TopicRegistryRepository.release_unless_published`(조건 `status != PUBLISHED`를 DB에 건 갱신)로 쓴다. 목록을 읽은 뒤 발행이 끼어들어 `PUBLISHED`가 된 항목을 덮어쓰지 않기 위해서다.
- 진행은 하지 않는다. 다음 스캔이 글 코루틴을 띄운다.

### `services/agent_calls.py`

- `call(kind, round_id, slot, binding, messages, schema, check=None, draft=None, target_version=None, topic_attempt=None, item_id=None) -> CallResult(output, call_id)`: 재시도 루프를 이 메서드 안에서 끝낸다. 성공하면 파싱된 계약 모델과 그 시도의 영수증 `call_id`를 돌려주고(버전·리뷰·중재가 영수증을 가리킨다), 소진하면 `LLMRetriesExhaustedError`를 던진다. `binding`은 호출자가 라운드 스냅샷에서 꺼내 넘긴다(영수증의 `model_binding`). `schema`는 Pydantic 계약 모델 클래스이고 파싱은 `model_validate_json`이다. 입력 문맥이 필요한 의미 검사(카테고리가 목록에 있는지 등)는 3단계 `agents/parsers.py`가 더한다.
- 시도마다: ① 글 호출이면 소프트 예산 검사. 그 글의 `AgentCall.usage` 합계 + 추정치(메시지 문자 수 + `max_output_tokens`) > `draft.token_limit`이면 호출하지 않고 `BudgetExceededError`. ② 벤더 세마포어 안에서 타임아웃(`review_timeout_s` 또는 `draft_timeout_s`)을 걸고 호출. ③ 성공·실패와 무관하게 `AgentCall`을 insert. ④ `agents/parsers.py`로 파싱.
- 실패 처리: 네트워크·5xx·429·타임아웃(클라이언트가 `LLMTransportError`로 던진다)은 지수 백오프 뒤 같은 메시지로, `LLMOutputError`(스키마 위반)는 첫 시도 메시지 뒤에 직전 실패 출력과 오류 안내를 붙여 다시 부른다. `check`가 `ValidationError`를 던져도 같은 실패다. 429(`LLMTransportError.rate_limited`)는 그 벤더의 게이트를 `_RATE_LIMIT_COOLDOWN_S`(30초) 동안 동시 1건으로 줄인다(04 문서 9절). 시도 수는 이 메서드의 지역 값이라 재개하면 새로 센다.
- 주제 제안 호출(`draft=None`)은 예산을 검사하지 않는다(02 문서 3절 예산).
- 벤더별 게이트(`ProviderGate`: 세마포어 + cooldown)는 `clients/llm/registry.py`가 가진다.

### `services/runner.py`

- lifespan이 `runner.start()`로 시작하고, 종료 시 코루틴을 전부 cancel한다. 끊긴 호출은 다음 기동 때 다시 한다. 발행 호출만은 끊지 않고 `DraftPipelineService.drain()`으로 결과 반영까지 기다린다(04 문서 8절 4단계).
- `start()`: `scan()`을 `SCAN_INTERVAL_S` 주기로 돌린다. 첫 스캔은 기동 직후다.
- `scan()`: 먼저 `RoundService.reconcile()`로 정리한다. 순서는 `ABORTED` 라운드에 남은 진행 중 글을 `REJECTED`로 → `RESERVED` 레지스트리를 글의 종결 상태(`PUBLISHED`·`REJECTED`)에 맞춤 → `IN_PROGRESS` 라운드의 `close_if_complete` 재확인이다(04 문서 1절 표). 그다음 코루틴이 없는 진행 대상만 띄운다. 러너는 `id → asyncio.Task` 표로 같은 대상에 코루틴이 둘 생기지 않게 한다.
- 코루틴의 예상하지 못한 예외는 `logger.exception`을 남기고 글을 `FAILED(INTERNAL_ERROR)`로, 주제 선정이면 라운드를 `ABORTED(INTERNAL_ERROR)`로 저장한다. 다음 스캔이 같은 오류로 호출을 되풀이하지 않게 하기 위해서다. 경계이므로 `except Exception`을 허용한다(CONVENTIONS 5절).
- uvicorn 워커 1개 전제. 주석으로 남긴다. `agents run`을 서버와 동시에 띄우지 않는다. 다중 인스턴스가 필요해지면 글 단위 실행 소유권(lease)과 트랜잭션을 다시 들여야 한다(07 문서 Q-04).

### `clients/publisher/`

- `PostCreatePayload`·`PostCategory`는 `base.py`에 둔다(`clients`는 `models`를 import 하지 않는다). JSON 필드 이름은 `CreatePostDto` 그대로(camelCase)다. 평평한 카테고리 목록을 메인·서브 쌍(`CategoryOption`)으로 묶는 일은 서비스가 한다(`services/round.py` 의 `category_options`: 활성 메인 아래 활성 서브만, 응답 순서 유지. `RoundService.start_round` 가 조회→묶기→`create_round` 를 잇는다).
- 실패 계약: 로그인 단계의 모든 실패와 글 생성 호출의 2xx 아닌 500 미만 응답(4xx·리다이렉트)·연결 거부는 `PublishFailedError`, 글 생성 호출의 응답 전 타임아웃·연결 끊김·5xx는 `PublishUncertainError`를 던진다. 로그인에서는 글이 생기지 않기 때문이다. 분류 근거는 04 문서 8절.
- 로그인 응답은 본문이 비어 있고 토큰을 `HttpServletResponse`로 내려준다(`vans_story_be`의 `AuthController.login`). 쿠키인지 헤더인지는 구현 때 확인한다. 토큰은 저장하지 않고 발행마다 받는다. 모든 호출에 `publish_timeout_s`를 건다.

### `routers/agent_rounds.py`

모두 `Depends(verify_agents_key)`(`X-Internal-Key` = `AGENTS_INTERNAL_API_KEY`, 검색 내부 키와 별개. 07 문서 D-44). 응답은 `schemas/agent_pipeline.py`(`RoundPublic`·`RoundDetail`·`DraftPublic`·`DraftHistory`·`EscalationSummary`·`CallPublic`)이고 엔티티 → 응답 변환은 `services/pipeline_views.py`가 한다. 읽기 엔드포인트(라운드 목록·상세, 글 이력, 격리 요약, 영수증, 지표)는 `PipelineViewService`만, 사람 결정은 `DraftDecisionService`, 생성·중단은 `RoundService`를 부른다. 라우터는 리포지토리를 받지 않는다(CONVENTIONS 1절). 상태를 바꾸는 요청은 DB만 바꾸고, 진행은 다음 스캔이 이어 간다(최대 `SCAN_INTERVAL_S` 지연). 라운드 생성 입력 오류(바인딩·렌즈 매트릭스·발행 설정 없음)와 결정 값 오류는 400 `InvalidRequestError`, 없는 id는 404.

| 메서드 | 경로 | 동작 |
|---|---|---|
| `POST` | `/api/v1/agents/rounds/` | 라운드 생성·시작. 먼저 Post BE 활성 카테고리(`activeOnly=true`)를 읽어 `categories` 스냅샷을 만들고(실패하면 라운드를 만들지 않는다), 라운드를 `TOPIC_SELECTION`으로 만든다. 다른 `TOPIC_SELECTION` 라운드가 있으면 409. 바디 `RoundCreate(theme_keyword?, bindings?)` |
| `GET` | `/api/v1/agents/rounds/` | 목록(상태 필터) |
| `GET` | `/api/v1/agents/rounds/{round_id}/` | 라운드 + 글 3편 요약. `external_post_id`는 `PUBLISHED`가 아니면 의도에 남은 회수 대상 id다 |
| `POST` | `/api/v1/agents/rounds/{round_id}/abort/` | 중단. 라운드 `ABORTED`를 먼저 쓰고 글·레지스트리를 정리한다(04 문서 1절) |
| `GET` | `/api/v1/agents/drafts/{draft_id}/` | 글 전체 이력(버전, 리뷰, 결정, 중재) |
| `GET` | `/api/v1/agents/drafts/{draft_id}/escalation/` | 사람용 요약: 발행본 본문과 직전 버전과의 unified diff, `last_reviewed_version`의 미해소 차단 항목, 기각 항목과 사유·중재 판정, 발행 의도. `ESCALATED`가 아니어도 읽을 수 있다 |
| `POST` | `/api/v1/agents/drafts/{draft_id}/escalation/` | `EscalationDecision(decision: PUBLISH_AS_IS \| REVISE \| REJECT \| CONFIRM_PUBLISHED, instruction?, external_post_id?, decided_by)`. `CONFIRM_PUBLISHED`는 `PUBLISH_UNCERTAIN`에서만 |
| `POST` | `/api/v1/agents/drafts/{draft_id}/approve/` | 승인 토글이 켜진 경우 발행 승인. 바디 `DraftApproval(approved_by)`. 승인은 그 버전에 대한 것이다. 토글이 꺼져 있거나 이미 승인됐으면 409 |
| `POST` | `/api/v1/agents/drafts/{draft_id}/retry/` | `FAILED` 재개. 바디 `DraftRetry(token_limit?)`. `token_limit`은 어느 사유든 받을 수 있고, 현재 사용량보다 커야 한다 |
| `POST` | `/api/v1/agents/drafts/{draft_id}/discard/` | `FAILED` 글 하나만 폐기. 바디 `DraftDiscard(decided_by)` |
| `GET` | `/api/v1/agents/calls/` | `draft_id` 또는 `round_id` 필터(둘 다 없으면 400), 토큰·지연 조회. 메시지 본문은 뺀다 |
| `GET` | `/api/v1/agents/metrics/` | 07 문서 4절 지표(`PipelineMetrics`). `round_id` 가 없으면 모든 라운드 |

경로는 trailing slash 포함(CONVENTIONS 2절). 상태 전이 위반은 409 `InvalidTransitionError`.

### `cli/`

| 명령 | 동작 |
|---|---|
| `agents start-round [--theme] [--wait]` | Post BE 카테고리를 읽어 라운드 생성 후 종료(진행은 러너). `--wait`면 러너가 더 할 일이 없을 때(라운드 종결, 또는 글이 모두 승인 대기·사람 결정 대기·종결. 승인됐거나 토글이 꺼진 `READY_TO_PUBLISH`는 발행 중이라 기다린다)까지 `SCAN_INTERVAL_S` 마다 상태를 찍는다 |
| `agents run` | 서버 없이 이 프로세스에서 러너를 돌린다. 디버깅용. 서버와 동시에 쓰지 않는다 |
| `agents status <round_id>` | 글 3편 상태·승인 대기 여부·iteration·토큰·`external_post_id`(`PipelineViewService.get_round_progress`, `RoundProgress`) |
| `agents show <draft_id> [--version N]` | 본문·리뷰·결정 출력 |
| `agents resolve <draft_id> --decision … [--instruction] [--external-post-id] [--decided-by]` | 격리 해소. `decided_by` 기본은 `cli` |
| `agents approve <draft_id> [--approved-by]` | 발행 승인 |
| `agents retry <draft_id> [--token-limit N]` | `FAILED` 재개 |
| `agents discard <draft_id> [--decided-by]` | `FAILED` 글 폐기 |
| `agents abort <round_id>` | 중단 |
| `agents replay <call_id> [--provider] [--model]` | 영수증의 메시지를 그대로 다시 보내 응답·파싱 결과·토큰을 찍는다(프롬프트 튜닝용, 영수증·상태 변경 없음). 다른 모델로 보낼 수 있다. 파싱은 스키마까지만 본다 |
| `agents metrics [--round-id]` | 07 문서 4절 지표를 JSON 으로 찍는다 |

## 4. 설정 (`core/config.py`)

```python
class Settings(BaseSettings):
    # 벤더 키 (로그 금지)
    anthropic_api_key: SecretStr | None = None
    google_api_key: SecretStr | None = None
    xai_api_key: SecretStr | None = None

    # 슬롯 바인딩. JSON 문자열. 예: {"A": {"provider": "anthropic", "model_id": "...", "persona_id": "architect"}, ...}
    agent_slot_bindings: dict[AgentSlot, ModelBinding] = {}   # 비어 있으면 start-round 가 거절한다

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
    draft_max_chars: int = 20000
    review_temperature: float = 0.2       # REVIEW, ARBITRATE. 작성은 ModelBinding.temperature
    review_timeout_s: int = 300
    draft_timeout_s: int = 600           # PROPOSE_TOPIC, WRITE_DRAFT, REVISE_DRAFT
    scan_interval_s: int = 15

    # 발행 (07 문서 D-27, D-40)
    post_be_url: str = ""                 # 비어 있으면 publisher 가 None 이라 start-round 가 거절한다
    user_service_url: str = ""            # vans_story_be. 발행 계정 로그인. 이것이나 작성 벤더 계정이 비면 발행이 ValueError(INTERNAL_ERROR)
    anthropic_publisher_email: str = ""   # 벤더별 관리자 계정. 글을 쓴 슬롯의 벤더 계정이 게시글 작성자다 (D-45)
    anthropic_publisher_password: SecretStr | None = None
    google_publisher_email: str = ""
    google_publisher_password: SecretStr | None = None
    xai_publisher_email: str = ""
    xai_publisher_password: SecretStr | None = None
    agents_internal_api_key: str = ""     # /api/v1/agents/ 의 X-Internal-Key. 비어 있으면 모두 403 (07 문서 D-44)
    publish_timeout_s: int = 30

    # 운영
    publish_requires_approval: bool = True
    prompt_version: str = "v2"
    llm_concurrency_per_provider: int = 2
```

새 변수는 `.env.example`에 같이 추가한다. 바인딩을 라운드 생성 요청으로 덮어쓸 수 있게 해 A/B 테스트를 지원한다(라운드 문서에 스냅샷 저장).

## 5. 비동기 모델

- 이 기능의 라우터·서비스·리포지토리는 전부 `async def`. 동기 I/O 호출 금지.
- MongoDB는 `pymongo.AsyncMongoClient`(pymongo ≥ 4.13). 검색 서비스와 같은 `MONGODB_URI`·`mongodb_db_name`을 쓰고(07 문서 D-43), 연결만 동기 `MongoClient`와 별도로 만든다(`create_async_mongo_client`). 새 환경 변수는 없다.
- 다중 문서 트랜잭션을 쓰지 않으므로 standalone MongoDB여도 된다. 여러 문서를 고치는 동작의 순서와 정리는 04 문서 1절.
- LLM 클라이언트는 벤더 공식 SDK의 async 인터페이스(Anthropic) 또는 `httpx2.AsyncClient`(Google·xAI). 타임아웃 필수. 재시도는 호출 서비스가 하므로 SDK 재시도는 끈다.
- 코드 블록 문법 검사 등 CPU 작업은 짧으므로 인라인. 외부 언어 체커를 subprocess 로 돌린다면 `asyncio.create_subprocess_exec`.

## 6. 테스트 전략

| 범위 | 방법 |
|---|---|
| `models/post_draft.py` | 전이표(03 문서) 행마다 테스트 1개. 허용되지 않는 전이가 `InvalidTransitionError`를 던지는지. DB 없음 |
| `services/evaluation.py` | 순수 함수. fast-track, 차단 1건, SPOT_CHECK 해소/미해소/교착, 중재 결과별 케이스 |
| `models/tiptap.py` | 변환표 행마다 1건, 앵커 제거, soft break → 공백, `python-snippet` → `python`, 금지 문법 각각 `ValueError`. 변환 결과의 노드·마크 이름이 에디터 스키마(vans_story EditorExtensions: StarterKit·Link·CodeBlockLowlight·Table) 안에 있는지 비교. 사람 글 golden 은 없어 `tests/golden/tiptap_sample.md → .json` 을 golden 으로 둔다 |
| `services/validation.py` | 앵커 누락, 코드 문법 오류, 길이, 차단 항목 누락·중복·리뷰에 없는 ID, `MINOR`의 `DEFERRED`는 통과, 차단 항목의 `DEFERRED`는 실패, 변환 불가 문법(HTML·이미지·각주·`mermaid`), 앵커 소실 각 1건 |
| `services/topic_selection.py` | 충돌 없음, 1쌍 충돌 후 순위 조합, `LockedTopic`의 이전 시도 스냅샷 유지, 상한 미달 시 `ABORTED`, 제안 호출 재시도 소진은 다른 호출을 기다린 뒤 `ABORTED(LLM_FAILED)`, 두 번째 `TOPIC_SELECTION` 라운드 생성은 409이고 문서가 남지 않음, 확정 쓰기 도중 끊고 다시 돌리면 같은 ID로 글 3편·레지스트리 3건만 남고 자기 레지스트리와 충돌하지 않음, 중단과 겹친 확정이 정리된 문서를 되살리지 않음 |
| `services/draft_pipeline.py` | `PipelineHarness`(`FakeAsyncDatabase` 위의 실제 리포지토리·서비스 + `ScriptedLLMClient` + 실제 `PublishingService` 위의 `FakePostBE`)로 ① fast-track ② 수정 1회 후 승인 ③ 교착 2건 동시 발생 → `item_id` 별 `ARBITRATE` 2건 호출 및 `UPHOLD_AUTHOR` ④ `UPHOLD_REVIEWER` 판정된 v1 항목이 v3 `open_blocking_items` 에 유지되고 기각 시 게이트 실패 ⑤ 게이트 실패는 `iteration` 유지, v1 재작성 뒤에도 FULL 리뷰, 통과하면 `validation_retries=0` ⑥ 사람 `REVISE(instruction)` 이 게이트 통과 후 `IN_SPOT_CHECK` 없이 `READY_TO_PUBLISH` 직행, 이전 승인은 지워져 다시 승인을 기다림 ⑦ `save` 충돌이면 결과를 버리고 다시 읽음 ⑧ 단계 중간에 끊고 다시 `drive`하면 마지막 저장 상태부터 이어 가고 끊긴 단계의 호출만 다시 함. `DRAFTING`에서 끊겨도 `revision_cause`로 같은 호출 종류·입력을 만듦 ⑨ `abort_round` 가 `ESCALATED`·`FAILED` 글까지 `REJECTED` 로 바꾸고 `PUBLISHED` 는 유지 ⑩ 중단과 `close_if_complete` 가 겹치면 라운드는 `ABORTED` 이고 `COMPLETED` 가 되지 않음 ⑪ 리뷰 1건이 재시도를 소진하면 다른 리뷰는 저장되고, 재개하면 빠진 리뷰만 호출 ⑫ 예상하지 못한 예외는 `FAILED(INTERNAL_ERROR)` 이고 스캔이 다시 띄우지 않음 ⑬ 병렬 단계의 예상하지 못한 예외는 성공한 형제를 저장하고 `FAILED(INTERNAL_ERROR)`, 재개하면 빠진 호출만 |
| `services/agent_calls.py` | 시도마다 `AgentCall` 1건과 비용 합산, 스키마 위반 재시도의 메시지 구성(첫 메시지 + 직전 실패 출력 + 오류 안내), 5xx·429·타임아웃 백오프 재시도, 429 는 벤더 게이트 축소(`ProviderGate`), `check` 의 `ValidationError` 도 재시도, 소진 시 `LLMRetriesExhaustedError`, 글 사용량 + 추정치가 상한을 넘으면 호출 전에 `BudgetExceededError`, 주제 제안은 예산을 검사하지 않음 |
| `services/runner.py` | 같은 글에 코루틴이 둘 생기지 않음, 승인 대기·`ESCALATED`·`FAILED` 글은 띄우지 않음, 스캔마다 정리가 `ABORTED` 라운드의 남은 글·레지스트리와 `PUBLISHED`·`REJECTED` 글의 레지스트리를 맞춤, 종료가 발행 호출 중인 글을 끊지 않고 `PUBLISHED` 까지 기다림 |
| `services/draft_decision.py` | 결정마다 전제 상태 검사와 409, 재승인·토글 꺼짐 승인은 409, 충돌 시 다시 읽어 재적용, 종결 결정 뒤 레지스트리와 `close_if_complete`, 재개의 `token_limit` 검사 |
| `services/metrics.py` | 순수 함수. fast-track(v1 이 발행본인 글만)·iteration·격리·실패 사유·중재·판정 불일치(슬롯별)·게이트 실패 종류(게이트를 거친 버전만)·`REJECTED_WITH_REASON`·종류별·글별 토큰. 빈 입력은 비율이 `None` |
| `services/pipeline_views.py` | 라운드 진행 상태(승인 대기·글별 토큰), 격리 요약(diff·미해소·기각·중재), 영수증 필터(둘 다 없으면 400), 없는 id 는 404. 응답 형태는 라우터 golden 이 본다 |
| `AgentCallService.replay` | 영수증 메시지를 그대로 보내고 새 영수증을 남기지 않음, 바인딩 덮어쓰기, `REVIEW` 영수증의 scope 로 출력 계약 선택 |
| `services/publishing.py` | `CreatePostDto` 매핑. 외부 호출 후 로컬 반영 전 장애를 재현하면 다시 `drive`해도 호출하지 않고 `PUBLISH_UNCERTAIN`. `PublishFailedError`는 재시도 후 상한에서 `PUBLISH_FAILED`, `PublishUncertainError`는 즉시 `PUBLISH_UNCERTAIN`. `CONFIRM_PUBLISHED`는 호출 없이 `PUBLISHED`, `PUBLISH_AS_IS`·`REVISE`는 카운터·의도를 지움. 로그인 단계 실패는 결과 확정. 요약 노트에 HTML·여러 줄이 든 지적이 있어도 변환 성공. 응답 반영 전에 `REJECTED`이면 `PUBLISHED`로 쓰지 않고 `external_post_id`만 의도에 남긴다 |
| `agents/parsers.py` | 없는 앵커·빈 체크리스트는 `LLMOutputError`. SPOT_CHECK의 MAJOR는 버리고 로그(그 앵커·렌즈는 검사하지 않음). `item_checks` 누락·중복·입력 외 ID·미해소 빈 note는 오류. 제안 카테고리가 입력 목록에 없음, 제목 100자 초과, 빈 `summary`는 오류 |
| `clients/publisher/post_be.py` | `httpx2.MockTransport`. 로그인 요청·토큰 추출, `CreatePostDto` 전송, 로그인 단계의 어떤 실패(401·5xx·토큰 없음·타임아웃)든 `PublishFailedError`, 글 생성의 4xx·3xx·연결 거부·연결 타임아웃은 `PublishFailedError`, 5xx·읽기 타임아웃·연결 끊김·`_id` 없는 응답은 `PublishUncertainError`, 벤더 계정 설정 없음은 `ValueError`, 주어진 벤더의 계정으로 로그인 |
| `cli/` | `_is_waiting_on_human`: 승인 대기·사람 결정 대기·종결만 멈춘 것으로 보고, 승인됐거나 토글이 꺼진 `READY_TO_PUBLISH` 는 계속 폴링 |
| `routers/` | `dependency_overrides`로 서비스 교체, `tests/golden/`에 응답 계약. 에이전트 API는 `get_async_database`를 `FakeAsyncDatabase`로 바꿔 실제 리포지토리·서비스가 돌고, 시각을 고정한 시드로 `agents_*.json` golden 과 값까지 비교한다 |
| e2e | `@pytest.mark.e2e`. `tests/e2e/test_llm_vendors.py` 가 키 있는 벤더마다 계약 출력 스키마 6종(`$defs`·`anyOf`·길이·개수 제약 포함)을 보내(6 × 벤더 수 호출) 구조화 출력 호환을 확인하고, `tests/e2e/test_post_be.py` 가 `POST_BE_URL` 의 활성 카테고리가 메인·서브 쌍을 이루는지와 벤더별 발행 계정 로그인(글은 만들지 않는다)을 본다. 키·URL 이 없으면 건너뛴다. 기본 실행 제외 |

`tests/fakes.py`에 `FakeLLMClient`를 두고 응답(본문·`LLMResponse`·예외)을 순서대로 스크립트한다. `FakePostBE`는 `delay_s`로 발행 HTTP 호출 시간을 흉내 낸다. `LLMClient` 계약에는 호출 종류가 없으므로 `ScriptedLLMClient`(4단계)가 시스템 프롬프트의 템플릿 문구로 종류(`PROPOSE_TOPIC`·`WRITE_DRAFT`·`REVISE_DRAFT`·`REVIEW_FULL`·`SPOT_CHECK`·`ARBITRATE`)를 가려 종류별 스크립트를 소비한다. 값이 callable 이면 입력 JSON 을 받아 응답을 만든다(SPOT_CHECK 의 `item_checks` 처럼 입력에 따라 달라지는 응답). 슬롯은 입력의 페르소나로 가린다. 파이프라인 테스트는 `FakePostDraftRepository` 대신 `FakeAsyncDatabase` 위의 실제 리포지토리를 써서 저장 조건(revision)까지 같이 검사한다. 토큰 사용량도 가짜로 돌려 소프트 예산을 테스트한다. Mongo 리포지토리는 `FakeAsyncCollection`(같음·`$in`·`$gte` 조건, `$group`/`$sum` 집계만)으로 질의·저장 조건을 검사한다.
