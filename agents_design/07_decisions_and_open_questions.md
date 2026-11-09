# 07. 결정 기록과 미결 사항

## 1. 확정한 결정

| ID | 결정 | 이유 | 대안 |
|---|---|---|---|
| D-01 | 검색 서비스 `vans_devblog_fastapi`와 같은 저장소·같은 FastAPI 앱에 구현한다(2026-10-30 변경) | 사용자 결정. 컨벤션·설정·배포를 한 곳에서 관리하고, 서비스를 하나 더 띄우지 않는다. 비동기 러너·벤더 키·잦은 배포는 같은 앱 안에서 수용한다(06 문서 1절) | 별도 저장소·서비스 `vans_devblog_agents`(처음 결정. 로드맵 0·1단계를 거기서 시작했다가 같은 날 이 저장소로 옮겼다) |
| D-02 | `AgentSlot`(A/B/C) + `PersonaProfile` + `ModelBinding` 3분리 | 벤더를 도메인에 박지 않는다. 모델 교체·A/B가 설정 변경으로 끝난다 | `AgentType = CLAUDE\|GEMINI\|GROK` |
| D-03 | 판정은 `severity` 집계로 결정론적으로 도출. LLM `verdict`는 기록만 | verdict/severity 이중 기준 충돌 제거. 규칙이 한 줄 | verdict 우선, severity 보조 |
| D-04 | 부분 발행 허용. 한 편의 `ESCALATED`가 나머지 발행을 막지 않는다 | 글 단위 독립 파이프라인 원칙. 라운드는 묶음일 뿐 | 라운드 전체 보류 |
| D-05 | 배리어는 주제 확정 단계에만 둔다 | Post A 리뷰는 Post A v1만 있으면 시작 가능. B·C를 기다릴 이유가 없다 | 초안 3편 완료 후 리뷰 시작 |
| D-06 | 리뷰 1건으로는 판정하지 않는다. 리뷰 호출 재시도 소진 시 글은 `FAILED` | 교차 리뷰의 의미 유지. 품질 판단(`ESCALATED`)과 같은 자리에서 다시 돌리면 되는 중단(`FAILED`)을 구분 | 1건으로 진행 |
| D-07 | `ReviewItem`은 불변. 수용/기각은 다음 버전의 `ItemResolution` | VO 일관성. "v2 기각 → v3 수용" 이력이 남는다 | `ReviewItem.resolution_status` 가변 필드 |
| D-08 | 섹션 앵커(`<!-- sec:id -->`)를 본문에 주입하고 수정 시 유지 강제 | 버전 간 지적 사항 대조 가능. SPOT_CHECK 가 기계적으로 가능 | 문단 번호·줄 번호 |
| D-09 | SPOT_CHECK 범위: 이전 지적 해소 여부 + 신규 CRITICAL 만 | Over-editing 방지 | 전체 재리뷰 |
| D-10 | CRITICAL 교착은 제3 슬롯 중재 1회 | 사람 개입 대폭 감소. 호출 1건으로 교착 해소 | 바로 `ESCALATED` |
| D-11 | 게이트 실패는 `revision_cause=GATE` 로 `iteration` 을 올리지 않는다. 사람 재수정은 `cause=HUMAN` 으로 마찬가지로 올리지 않는다. `revision_cause`는 `DRAFTING`까지 유지해 호출 종류와 새 버전의 `origin_cause`를 정한다 | `REVISING` 출구가 원인을 봐야 상한과 재시도가 섞이지 않는다. 호출과 저장을 다른 단계로 나누므로 `DRAFTING`에서 재시작해도 무엇을 쓸지 알아야 한다 | 상태 자체를 `VALIDATION_RETRY` 로 분리 |
| D-12 | 렌즈는 (글, 리뷰어) 쌍에 배정. 모든 글이 세 렌즈를 받는다 | 작성자 본인 렌즈가 본인 글에 비는 문제 해결 | 모델 고정 렌즈 |
| D-13 | 주제 배정은 슬롯당 순위 3개에서 충돌 없는 최소 순위 합. 상한까지 못 채우면 라운드 `ABORTED` | 겹치는 주제를 억지 배정하면 Phase 1 목적이 사라진다 | 레지스트리와만 안 겹치면 라운드 내 중복 허용 |
| D-14 | 블라인드는 리뷰·중재 단계 한정. 편향 억제의 주 수단은 `VerificationChecklist` 강제 | 3개 슬롯에선 블라인드 실효성이 제한적. 체크리스트가 동조 현상에 더 효과적 | 블라인드만 |
| D-15 | 최소 지적 개수 강제 금지 | 억지 지적 생성 | "최소 3건" |
| D-16 | 요약 노트는 템플릿 생성(LLM 호출 없음) | 비용 0, 결정론적, 할루시네이션 없음 | LLM 요약 |
| D-17 | 운영 초기 `PUBLISH_REQUIRES_APPROVAL=true`. 승인은 그 버전에 대한 것이라 새 버전이 제출되면 지운다 | 신뢰 축적 전 사람 확인. 사람이 보지 않은 본문이 이전 승인으로 발행되면 안 된다 | 처음부터 자동 |
| D-18 | 상태는 전부 Mongo. LLM 호출은 시도마다 `AgentCall` 한 건을 insert 하는 영수증이고, 작업 상태(`PENDING`/`RUNNING`)를 두지 않는다 | 호출과 반영을 같은 코루틴이 하므로 작업 문서와 멱등 키가 막을 중복이 없다. 남는 비용은 재시작 때 끊긴 단계의 호출을 다시 하는 것뿐이다 | 멱등 키를 가진 Task 문서를 재시도마다 갱신 |
| D-19 | 렌즈 배정은 세 슬롯의 `primary_lens` 로 계산한다. 슬롯 문자에 고정하지 않는다 | 바인딩을 라운드마다 덮어써도 글마다 세 렌즈가 유지된다 | A/B/C 고정 표 |
| D-20 | 토큰 예산은 글마다 소프트 상한 하나(`token_limit`)이고, 사용량은 `AgentCall.usage` 합계다. 라운드 상한과 별도 계정 문서는 두지 않는다 | 병렬 호출이 동시에 검사를 통과해도 넘는 폭은 동시 호출 몇 건이다. 라운드 비용은 글 상한과 주제 선정 호출 수 상한으로 묶인다 | 라운드·글 `BudgetAccount`의 조건부 `$inc` 하드 예약 |
| D-21 | FULL 리뷰 진입은 "리뷰를 마친 이전 버전이 없음"이다. 버전 번호가 2 이상인지는 기준이 아니다 | 게이트 실패 재작성이 리뷰 없이 발행되지 않는다 | `version >= 2` 이면 SPOT_CHECK |
| D-22 | 사람 재수정(`origin_cause=HUMAN`)은 게이트 통과 시 `IN_SPOT_CHECK` 를 거치지 않고 `READY_TO_PUBLISH` 로 직행한다 | 사람이 개입한 결정을 에이전트가 다시 기각해 `ESCALATED` 루프에 빠지는 것을 막는다 | SPOT_CHECK 에 `human_instruction` 전달 |
| D-23 | 버전 `v` 의 미결 차단 항목을 `open_blocking_items(v)` (신규 차단 ∪ `resolved=False` 이월 항목 − `UPHOLD_AUTHOR`)로 정의해 수정 입력·게이트·다음 SPOT_CHECK 가 공유한다 | SPOT_CHECK 버전의 `items` 에 이월 항목이 없어 v3 에서 `UPHOLD_REVIEWER` 항목이 누락되는 구멍을 막는다 | 직전 버전 `reviews.items` 만 검사 |
| D-24 | `ReviewItem` 에 `lens` 를 태그하고, `SpotCheckInput` 에도 `secondary_lens` 를 유지한다 | 보조 렌즈로 제기된 이슈가 SPOT_CHECK·중재에서 주 렌즈 기준으로 잘못 평가되는 것을 막는다 | 리뷰어 단위 단일 렌즈 |
| D-25 | 단일 프로세스에서 글마다 코루틴 하나가 LLM 호출을 직접 `await`하고 결과를 반영해 저장한다. 러너는 기동 시와 주기 스캔으로 코루틴이 없는 진행 대상만 띄운다 | 워커 1개·수동 실행·라운드당 3편 규모에서는 작업 큐·dispatch·heartbeat·stale 복구·취소 요청이 막을 상황이 없다. 상태가 DB에 있으니 재시작은 마지막 저장 상태부터 이어 가면 된다 | `PENDING` Task를 DB에 쓰고 별도 dispatch가 실행, heartbeat로 stale 복구 |
| D-26 | `AgentCall`은 append-only다. 재시도는 새 문서를 insert 하고 이전 호출을 덮어쓰지 않는다 | 재시도 비용·출력·오류를 덮어쓰면 예산과 감사 이력이 틀어진다 | 호출 단위 단일 `usage`·`output_raw` |
| D-27 | 발행은 Post BE를 바꾸지 않고, 사람이 쓴 글과 같은 `CreatePostDto`로 기존 `POST /api/v1/posts`를 호출한다. 호출 전 `PublicationIntent`를 저장하고, 결과를 모르면(응답 전 타임아웃·연결 끊김·5xx, 결과를 기록하지 못한 의도) 다시 호출하지 않고 `ESCALATED(PUBLISH_UNCERTAIN)`로 사람이 블로그를 확인한다 | Post BE에 멱등 키가 없고, 글 목록 API는 카테고리·페이지로만 걸러 이미 게시됐는지 찾을 수 없다. 발행은 라운드당 몇 건이라 사람 확인 비용이 작다 | Post BE에 멱등 키 unique 제약 추가, 결과를 몰라도 자동 재시도 |
| D-28 | 주제 선정 중인 라운드는 한 번에 하나다(부분 unique 인덱스). 주제 확정은 전체 잠금을 먼저 저장하고, 글 3편·레지스트리 3건을 결정론적 ID로 없을 때만 만든 뒤 라운드 전환을 커밋 지점으로 쓴다 | 동시에 주제를 고르는 라운드가 없으면 레지스트리 비교만으로 라운드 사이 중복을 막는다. 커밋 지점 전에 죽으면 같은 ID로 다시 쓰면 되고, 없을 때만 만들어야 중단이 정리한 문서를 되살리지 않는다 | 전역 lease(TTL 갱신) 안에서 Mongo 트랜잭션으로 커밋 |
| D-29 | `FAILED`에는 `FailureRecord(from_status, code)`를 남긴다 | 재개 지점과 방식(게이트 실패는 `REVISING`, 예산 초과는 상한 조정)이 사유마다 다르다 | 상태만 `FAILED` |
| D-30 | 라운드 중단은 진행 중인 호출을 끊지 않는다. 늦게 끝난 호출의 결과는 저장 충돌이나 라운드 상태 확인에서 버리고, 비용만 영수증에 남긴다. 중단 쪽 쓰기는 충돌하면 다시 읽어 재적용하고, 남은 따라 쓰기는 스캔마다 도는 정리가 마저 한다 | 같은 프로세스라 결과를 반영할지는 저장 시점에 정하면 된다. 취소 요청·사후 정산 상태가 필요 없다 | Task에 `CANCELLED`·`cancel_requested_at`을 두고 정산 |
| D-31 | 라운드 중단은 `PostDraft.reject_by_abort(reason)`(종결 외 전부, `ESCALATED`·`FAILED` 포함)로 글을 `REJECTED`시키고 `abort_round`가 직접 호출한다 | `REJECTED` 진입이 `resolve_escalation` 뿐이면 비격리 상태의 글을 중단할 수 없고, `is_active`가 거짓인 글은 러너가 건드리지 않는다 | 중단 시 전부 `ESCALATED`로 보내 사람이 하나씩 `REJECT` |
| D-32 | 모든 LLM 호출에 타임아웃(`REVIEW_TIMEOUT_S`, `DRAFT_TIMEOUT_S`), 발행 HTTP에 `PUBLISH_TIMEOUT_S`를 둔다 | 모든 에이전트 단계가 반드시 끝나야 코루틴이 멈추지 않고, 라운드 시간 상한 없이도 진행이 보장된다 | 단일 `LLM_TIMEOUT_S`, 타임아웃 없는 호출 |
| D-33 | 글 호출 직전에 사용량 합계 + 추정치(메시지 문자 수 + `max_output_tokens`)로 소프트 예산을 검사하고, 넘으면 호출하지 않고 `FAILED(BUDGET_EXCEEDED)`다. 돈이 나가는 하드 상한은 벤더 콘솔 지출 한도로 둔다 | 예산은 폭주를 멈추는 장치라 추정이 정확할 필요가 없다. 정확한 하드 예약은 추정기·안전계수·브레이커·재예약 규칙을 끌고 온다 | tokenizer·UTF-8 상계 추정과 원자 예약, 스키마 위반 재시도의 재예약 |
| D-34 | 라운드 시간 상한을 두지 않는다. 중단은 라운드 `ABORTED`를 먼저 쓰고 글·레지스트리를 따라 정리하며, `close_if_complete`는 `IN_PROGRESS`에만 `COMPLETED`를 쓴다. 발행 반영은 `READY_TO_PUBLISH` 조건 갱신이고, 갱신이 실패하면 상태와 무관하게 `external_post_id`만 의도에 남긴다 | 모든 에이전트 단계는 타임아웃·재시도·반복 상한으로 끝나고, 남는 대기는 사람 대기뿐이다. 시간 상한은 사람의 결정을 매 주기 덮어썼다. 라운드를 먼저 `ABORTED`로 쓰면 완료 확인이 라운드를 `COMPLETED`로 닫지 못한다. 외부에 이미 생긴 게시글은 버릴 수 없다 | `ROUND_TIMEOUT_S` 초과 시 `ESCALATED(TIMEOUT)`·`ABORTED(TIMEOUT)`, 상태별 한 트랜잭션 |
| D-35 | 차단 항목 해소는 `open_blocking_items`와 ID가 정확히 같고, `MINOR`·`NITPICK`은 생략하거나 `ACCEPTED`·`DEFERRED`로만 덧붙인다 | 집합 일치를 전체 해소에 적용하면 `DEFERRED`가 게이트를 통과하지 못한다 | `DEFERRED` 삭제 |
| D-36 | 실패 카운터는 0에서 시작해 실패마다 1 증가한 뒤 상한과 비교한다. 상한은 모두 실패했을 때의 호출 수다. `MAX_VALIDATION_RETRIES=2`는 연속 재작성 1회, 두 번째 연속 실패에 `FAILED`. `validation_retries`는 게이트를 통과하면, `publish_attempts`는 사람이 `PUBLISH_AS_IS`·`REVISE`를 고르면 0이 된다 | `<` 비교 뒤에 증가시키면 게이트 제출이 3회로 읽힌다. 리셋하지 않으면 v1·v2의 한 번씩 실패가 합쳐져 재작성 없이 `FAILED`가 되고, 사람 결정 뒤 발행 카운터가 상한을 넘어 맞는 전이가 없어진다 | 상한 값을 3으로 올려 제출 3회를 유지, 글 전체 누적 |
| D-37 | 벤더별 예산 브레이커를 두지 않는다 | 브레이커는 하드 예약의 추정이 틀리는 것을 막는 장치다. 소프트 예산은 실제 사용량으로 판단하므로 추정이 틀려도 상한 검사가 한 호출 늦을 뿐이다 | 실제 입력 토큰이 추정치를 넘으면 그 벤더의 `BudgetBreaker`를 열고 관리자만 닫기 |
| D-38 | `FAILED`는 사람이 재개(`retry`, 어느 사유든 새 `token_limit`을 줄 수 있다)하거나 그 글만 폐기(`discard`)한다. 병렬 단계의 실패는 다른 호출을 기다려 성공분을 저장하고, 재개하면 빠진 호출만 한다. 코루틴의 예상하지 못한 예외는 `FAILED(INTERNAL_ERROR)`로 멈춘다 | 글 하나를 버리려고 라운드를 중단하면 승인 대기 중인 형제 글까지 버려진다. 형제를 취소하지 않으면 정산할 것이 없다. 버그로 같은 호출을 주기 스캔이 되풀이하면 비용이 샌다 | 라운드 중단으로만 폐기, 형제 즉시 취소와 사후 정산, 예외 시 재시도 |
| D-39 | LLM 출력과 파이프라인 안의 본문은 마크다운(04 문서 8절 변환표의 문법)이다. Tiptap JSON 변환은 이 서비스의 순수 함수(`models/tiptap.py`)가 발행 때 하고, 게이트가 같은 함수를 실행한다. HTML·이미지·각주·`mermaid`는 거부한다 | Tiptap JSON은 장황해 작성·리뷰·SPOT_CHECK·수정·중재 호출마다 입력 토큰이 늘고, 중첩 JSON 생성은 스키마 위반 재시도를 늘린다. 리뷰어는 읽을 수 있는 텍스트가 필요하고, 섹션 앵커를 담을 노드가 없으며, LLM은 에디터 스키마에 맞는 노드를 보장하지 못한다. 블로그 뷰어에는 Mermaid 렌더러가 없다 | LLM이 Tiptap JSON을 직접 출력 |
| D-40 | Post BE 인증은 에이전트 전용 관리자 계정(벤더별, D-45)으로 발행마다 `vans_story_be`에 로그인해 받은 토큰을 쓴다. 게시글 작성자는 이 계정이다 | Post BE 글 생성은 관리자 JWT(HS512, `sub`=이메일)를 요구한다. `JWT_SECRET`을 공유해 직접 서명하면 이 서비스가 어떤 사용자로든 위장할 수 있다 | `JWT_SECRET` 공유 후 직접 서명 |
| D-41 | 카테고리는 주제 제안이 라운드 스냅샷(`PublishingRound.categories`)에서 한 쌍을 고르고, 파서가 목록에 있는지 검사한다 | 승인 토글을 끈 뒤에도 사람 없이 정해져야 한다. 잘못 고른 카테고리는 발행 후 관리자가 블로그 에디터에서 고친다 | 승인 때 사람이 지정 |
| D-42 | 관리자 수동 격리(`escalate(MANUAL)`)를 두지 않는다. 라운드 상태에서도 `OPEN`을 없애 라운드는 `TOPIC_SELECTION`으로 생긴다 | 수동 격리는 거는 API·CLI가 없었고, 발행 호출과 겹치면 이미 올라간 게시글을 놓쳤으며, 원래 상태로 돌아갈 경로도 없었다. 글은 상한으로 반드시 멈추고, 승인 토글이 발행을 막으며, 버릴 때는 `ESCALATED`·`FAILED`의 폐기나 라운드 중단을 쓴다. `OPEN`은 생성 API가 거치지 않는 상태였다 | `escalate(MANUAL)` + API·CLI + 복귀 경로, `OPEN → start_round` |
| D-43 | 파이프라인 컬렉션 4개(`agent_rounds`·`agent_drafts`·`agent_calls`·`agent_topics`)는 검색 서비스와 같은 Mongo, 같은 DB(`MONGODB_URI`·`mongodb_db_name`)에 둔다. 비동기 클라이언트만 별도로 만든다 | 사용자 결정(2026-11-02). 개인 블로그라 Mongo 인스턴스를 더 띄울 비용을 늘리지 않는다. 컬렉션 이름의 `agent_` 접두로 게시글 컬렉션과 구분한다 | 별도 `AGENTS_MONGODB_URI`로 standalone `vans_devblog_mongodb`를 가리키기 |
| D-44 | 에이전트 내부 API(`/api/v1/agents/`)의 `X-Internal-Key`는 검색 내부 키(`INTERNAL_API_KEY`)와 별개인 `AGENTS_INTERNAL_API_KEY`다 | Q-16 해소(2026-11-02). 검색 키는 NestJS Post BE와 공유하는데, 같은 키면 Post BE 설정이 새면 라운드 생성·발행 승인까지 열린다. 에이전트 API는 사람(CLI·curl)만 부른다 | `INTERNAL_API_KEY` 공유 |
| D-45 | 발행 계정은 벤더마다 하나다(`{ANTHROPIC,GOOGLE,XAI}_PUBLISHER_EMAIL`·`_PASSWORD`). 글을 쓴 슬롯(`author_slot`)의 바인딩 `provider` 계정으로 로그인해 그 계정이 게시글 작성자가 된다. 계정이 없으면 발행 의도를 저장하기 전에 `FAILED(INTERNAL_ERROR)`로 멈춘다 | 계정 하나면 블로그에서 어느 AI가 쓴 글인지 알 수 없다(2026-11-04, 사용자 결정). 슬롯이 아니라 벤더에 묶어 바인딩이 바뀌어도 이름이 벤더를 따라간다 | 공용 계정 하나(`PUBLISHER_EMAIL`), 공용 계정을 대체값으로 두기(작성자가 다시 흐려진다) |
| D-46 | 리뷰 항목 `section_id`·수정 `section_id_after` 는 받을 때 `sec:` 접두사를 뗀다(`normalize_section_id`). 없는 앵커는 그대로 계약 위반이다 | 프롬프트의 `<!-- sec:id -->` 표기 때문에 Gemini 는 `sec:body-3` 처럼 답한다. 같은 메시지로 재시도해도 고치지 못해 있는 앵커를 가리킨 리뷰로 글이 FAILED 됐다(2026-11-05) | 프롬프트만 고치기(벤더 편차가 남는다) |
| D-48 | 차단 항목의 범위 밖 판단 `OUT_OF_SCOPE`. 사유 필수, 비차단 항목에는 거절한다. 리뷰어가 받아들이지 않으면 MAJOR 도 중재 1회를 받는다. `UPHOLD_AUTHOR` 는 종결, `UPHOLD_REVIEWER` 는 다음 버전 수용 필수. 근거를 든 MAJOR 기각(`REJECTED_WITH_REASON`)은 여전히 중재하지 않는다 | Q-14 일부 해소(2026-11-05). 프롬프트만으로 방어권을 주면 리뷰어가 받아들이지 않은 MAJOR 가 상한까지 돈 뒤 `ESCALATED` 다. 실제 발행 글에서 작성자는 지적 3건을 모두 수용했고 범위가 넓어졌다 | 프롬프트만 고치기, 모든 MAJOR 중재 |
| D-49 | 발행 전 단계 `POST_REVIEW`(다듬기 → 전체 읽기 → 수준 평가). `evaluate` 경로로만 들어가고, `post_review_stage` 와 결과를 호출과 한 번에 저장한다. D-09 는 그대로다. 전체 읽기는 수정 루프를 만들지 않는다 | FULL 은 한 번뿐이라 수정이 만든 모순을 아무도 보지 않았다(발행 글: 본문은 유실 불가, 코드는 그냥 return). 세 호출이 같은 자리에 있어 상태 하나로 묶고, 포인터로 재개 때 호출을 되풀이하지 않는다. 사람이 정한 본문은 거치지 않는다(D-17, D-22) | 호출마다 상태 추가, `READY_TO_PUBLISH` 안에서 실행 |
| D-50 | 전체 읽기의 차단 결함은 `ESCALATED(FINAL_READ_FLAGGED)` 다. 격리 여부는 모델의 판정이 아니라 지적 종류(`BODY_CODE_CONTRADICTION`, `CORE_MECHANISM_MISSING`)로 코드가 정한다. 지적마다 앵커와 본문 인용이 필수다 | `READY_TO_PUBLISH` 의 사람 결정은 승인뿐이라 결과를 보여 주기만 하면 승인이나 라운드 포기만 남는다. `ESCALATED` 에는 REVISE·PUBLISH_AS_IS·REJECT 가 이미 있다 | `READY_TO_PUBLISH` 에 결정 추가 |
| D-51 | (2026-11-07 09 문서 D-71·D-73 으로 대체: 라우트는 파이프라인 버전 문서의 `routes` 이고 라운드가 버전을 고정한다. 아래는 그 전의 결정) 호출별 모델은 Mongo `agent_routes`(같은 DB, D-43)의 후보 배열이다. 라운드 생성 때 스냅샷을 뜨고 키 없는 벤더 후보를 뺀다. 라우트가 빠졌거나 지원하지 않는 조합이면 라운드를 만들지 않는다. `AGENT_SLOT_BINDINGS`·`REVIEW_TEMPERATURE` 를 없앴다. 후보는 failover 가 아니다. 응답의 `slot_bindings` 는 슬롯의 페르소나·작성 모델로 남는다. 요청의 `bindings` A/B 는 작성 모델만 바꾼다. 라우팅 전 라운드는 슬롯 바인딩으로 같은 스냅샷을 만들어 끝까지 돈다 | 2026-11-05 사용자 제안. 호출마다 읽으면 진행 중인 글의 모델이 바뀐다. 대체값을 두면 빠진 설정이 숨는다 | 설정 파일, 호출마다 조회, 후보로 장애 대비 |
| D-52 | 벤더 충돌 회피. 수정은 작성 벤더 안에서, 리뷰·전체 읽기·평가·문체 다듬기 제안은 작성 벤더 밖에서(제안을 적용하는 것은 수정 모델, 09 문서 D-91), 중재는 작성·리뷰 벤더 밖에서 고른다. SPOT_CHECK 라우트는 없고 FULL 모델을 다시 쓴다. 선택은 스냅샷과 글만 읽는 순수 함수다. 같은 회사의 다른 모델은 독립 검증으로 치지 않는다 | Q-15 해소(2026-11-05): 중재자가 그 글을 이미 FULL 리뷰한 리뷰어일 수 있는 점은 벤더 회피로 줄인다. 렌즈 셋을 벤더 둘이 맡으면 리뷰 벤더는 겹칠 수 있다 | 모델 단위 회피 |
| D-53 | `temperature`·`effort` 는 후보에 있고 None 이면 보내지 않는다. 벤더·모델 지원표에 없는 조합은 라운드 생성에서 거절한다. 잘린 응답은 계약 위반과 구분해 같은 한도로 재시도하지 않는다. 벤더 상한까지 올려 부르고 더 못 올리면 `FAILED(OUTPUT_TRUNCATED)` 다. xAI 는 Responses API 로 옮겼다 | 전에는 쓰기 0.7, 리뷰 0.2 를 보냈지만 Claude 는 400 이라 보내지 않았고 Argon 은 무시했다. 영수증과 실제 요청이 달랐다. 생각 토큰이 한도를 공유하면 같은 한도의 재시도는 또 잘린다 | 클라이언트가 조용히 버리기 |
| D-54 | 글 수준 100~400. 작성자는 `target_level`·`prerequisites` 를 내고, 작성 벤더가 아닌 모델이 목표를 모른 채 본문 인용으로 평가해 라벨로 남긴다. 처음에는 막지 않는다. 입문(100)은 `tradeoff` 를 요구하지 않는다. 400 은 근거(`evidence`)가 있어야 하고 트러블슈터는 근거 없이 200 까지다. `CreatePostDto.level` 은 `PUBLISH_POST_LEVEL` 을 켰을 때만 보낸다 | 지어낸 경험담은 검색으로 잡기 어려워 입력 단계에서 막는다. 300 까지는 표준 시나리오로 쓸 수 있다. Post BE 는 DTO 에 없는 필드를 400 으로 거부한다(`forbidNonWhitelisted`) | 300 이상에 근거 필수, 항목 수로 채점, 자기 평가 |
| D-55 | 글마다 렌즈 하나에 모델 하나(`build_lens_assignments`). 리뷰어는 주 렌즈로 가린다. `reviewer_slot`·`secondary_lens`·`arbiter_slot` 은 옛 글에만 있다. `ModelBinding.persona_id` 는 나누지 않는다. 호출·영수증은 이미 `CallModel` 이고 `slot_bindings` 는 응답 계약이다 | 작성자 렌즈가 다른 리뷰어의 보조 렌즈로 붙어 FACT 페르소나의 글은 FACT 전담 리뷰를 받지 못했다. 옛 글도 두 리뷰어의 주 렌즈가 달라 같은 코드로 돈다 | 렌즈 구성 변경(측정 뒤 결정) |
| D-56 | 사실 확인은 FACT 렌즈의 검색 호출 + 변환 호출이다. 주장은 외부 문서로 가릴 수 있는 것만 뽑는다. 심각도는 코드가 정한다(`REFUTED` CRITICAL, `UNVERIFIABLE` MINOR, URL 이 출처에 없으면 `UNVERIFIABLE`). 수정으로 바뀐 섹션은 SPOT_CHECK 에서 다시 확인한다(D-09 의 예외). 사실 지적의 분쟁은 중재자도 검색한다. 링크 응답은 확인 입력으로만 쓴다 | 리뷰어가 기억으로만 판정했다. 설계 판단까지 뽑으면 글의 논리가 지워진다. `UNVERIFIABLE` 을 처음부터 막으면 참인 주장이 삭제되거나 격리된다. 벤더 단일 호출로 조합을 확인할 수 없어 모든 벤더가 같은 두 호출을 쓴다 | 벤더별 한 호출, `UNVERIFIABLE` 차단 |
| D-57 | `typescript`·`ts`·`tsx` 코드 블록은 tree-sitter 로 문법을 검사한다(`has_error`). `*-snippet` 은 검사하지 않는다. 문법을 받지 못하면 그 검사만 건너뛴다 | Q-06 일부 해소. 코드 그래프가 이미 쓰는 의존성이라 subprocess 가 없다. 게이트를 네트워크에 묶지 않는다. 타입·논리 오류는 잡지 못하고 전체 읽기가 본문과의 어긋남을 본다 | tsc subprocess |
| D-58 | 다듬기는 문단 패치만 받고 안전장치를 통과한 것만 적용한다. 실패는 건너뛰고 수정 루프로 돌아가지 않는다. 다듬은 버전(`origin_cause=POLISH`)은 다시 리뷰하지 않으며 버전 수·fast-track 에 넣지 않는다 | 문체는 리뷰 렌즈가 될 수 없다(D-47). LLM 은 문법만 고치라 해도 뜻을 바꾼다는 연구가 있어 전체 재작성을 받지 않는다. 다듬기는 압축된 서사를 풀 수 없다 | 전체 재작성, 리뷰 렌즈 |
| D-59 | (버전 디렉터리 규칙은 2026-11-07 09 문서 D-71·D-77 로 대체: 문구는 버전 문서의 `prompts` 이고 `edit-prompt` 가 새 버전을 만든다) 프롬프트 v3. 범위(핵심 메커니즘, 다루지 않을 것), 문단 규칙(문단 하나에 개념 하나, 덧붙이지 말고 다시 쓰기), 범위 판단 먼저, 전달 확인, 수준, 근거 | 실제 발행 글은 제목이 약속한 모듈 구성이 없고 여러 주제를 한 문단에 담았다. 효과는 실제 호출로 재야 한다(`agents compare-prompts`) | v2 수정(버전 디렉터리는 배포 후 고치지 않는다) |
| D-60 | 라운드 스냅샷에는 `AGENT_MAIN_CATEGORIES`(쉼표로 나눈 메인 카테고리 값, 기본은 vans_story `POST_THEMES` 9개)에 든 메인 아래 활성 서브만 넣는다. 값이 비거나 남는 서브가 없으면 라운드를 만들지 않는다. 프런트에 화면을 더하면 이 값도 더한다 | Post BE 활성 메인 13개 중 `jest`·`cypress`·`junit5`·`git` 은 프런트에 화면이 없어 `/post/view/<main>/<id>` 가 404 다. 2026-11-06 라운드의 글이 발행되고도 블로그에서 안 보였다(글은 `nestjs`/`testing` 으로 옮겼다). 주제를 고르기 전에 막아야 글값을 버리지 않는다 | Post BE 에서 네 메인을 비활성화(사람이 쓰는 에디터 목록까지 바뀐다), 네 메인의 화면 추가(화면 없는 메인이 생기면 같은 문제가 되풀이된다), 발행 직전 검사(글을 다 쓴 뒤에야 막힌다) |
| D-62 | (2026-11-07 1a: v4 디렉터리는 기준 버전 `v1` 의 입력이 됐고 `Settings.prompt_version` 은 없어졌다, 09 문서) 프롬프트 v4. 가족별 디렉터리(`prompts/v4/{claude,gemini,grok,haiku,common}/`, 가족은 `prompt_family(CallModel)`), 영어 지시문·한국어 결과물, 작성·수정 입력에서 페르소나 제거(`writer_bias` 는 `propose_topic` 의 탐색 도메인으로만), `tone_markers`·`polish_format` 폐지, `conclusion` 필수 해제, 작성·리뷰 입력에 `level_criteria`, 작성 프롬프트에 길이 예산(`max_chars` 의 60~70%, 코드 40% 이하)·어미(~다)·불릿 억제·상투 도입/결론 금지(첫 비교에서 세 가족이 v3 보다 길어지고 Claude 가 "~합니다"로 간 뒤 보강), 주제 제안·작성 프롬프트에 용어 표기 규칙(통용 한국어 용어 우선, 없으면 영어 원어, 임의 음차 금지. 주제 제안이 만든 "스테이트리스"가 글 전체에 퍼진 뒤 추가), 수정 프롬프트에 길이 예산·어미 유지, 라운드 생성 때 `validate_prompts`. 구조 평가는 `fact_check` 에 남고(FACT 렌즈는 항상 검색 경로) 수준 미달은 MINOR(D-54·Q-19 유지). `Settings.prompt_version` 은 `agents compare-prompts --out-dir` 로 같은 영수증을 2차까지 비교해 세 가족이 게이트를 통과한 뒤 v4 로 올렸다(2026-11-07, 사용자 결정. 되돌리기는 `PROMPT_VERSION=v3`) | 2026-11-06 진단: 호출 84% 가 본문을 안 바꾸고 페르소나 말투가 글을 규정했다. 사용자가 준 v4 명세(저장소 밖, 2026-11-07 개정) | `review_full` 로 구조 평가 통합(`checks_facts` 때문에 호출되지 않는다), REFUTED 를 변환기가 `items` 에 복사(D-56 우회·중복), 수준 미달 MAJOR 차단(D-54 번복) |
| D-63 | 용어 사전은 공용 Mongo(D-43)의 `glossary_headwords`·`glossary_senses`·`post_glossary` 세 컬렉션이고 단위는 뜻이다. 표제어 키는 `normalize_key(term_en or term_ko)`, 뜻 키는 단어도 영역도 담지 않는 순번(`s00042`)이고 영역은 뜻의 속성 `domains`(고정 표 `DOMAINS`, 여럿 가능)다. 별칭은 `lookup_keys` unique 다중키 인덱스로 표제어 하나에만 속한다. Post BE 는 바꾸지 않고 FastAPI 가 읽기 API 를 키 없이 공개한다 | 같은 단어가 의미마다 다른 뜻("토큰")이라 단어 단위면 툴팁이 틀리고, 영역이 다르다고 뜻이 다른 것도 아니다("트랜잭션"은 DB 글에서나 백엔드 글에서나 같다. 2026-11-07 사용자 지적). 키에 단어나 영역이 들어가면 병합·영역 확장 뒤 어긋난다. 표제어 키를 코드가 만들어야 등록 여부를 AI 없이 먼저 가릴 수 있다. 발행 글의 부속이라 비밀이 없다 | MariaDB·ES 저장, 단어 단위 사전, `{headword}.{domain}` 뜻 키, `{headword}.{n}` 뜻 키, Post BE DTO 확장, BFF 가 내부 키를 붙여 호출 |
| D-64 | 추출은 `POST_REVIEW` 의 넷째 단계 `GLOSSARY`(저가 모델, `select_optional`)이고 실패해도 건너뛴다(다듬기 정책). 결과는 `PostDraft.glossary` 에 두고 발행 성공의 따라 쓰기가 `post_glossary` 로 옮긴다. `GLOSSARY` 는 **선택 라우트**다: `validate_snapshot` 은 있을 때만 확인하고 없어도 라운드를 만들며, 라우트가 없는 라운드(배포된 `agent_routes` 에 아직 없거나 옛 스냅샷)는 호출 없이 `skipped="no GLOSSARY route"` 로 지나간다. 사람 경로 글은 `agents glossary extract` 가 채운다 | 발행본이 확정된 뒤여야 표면형이 맞고, 발행 전이어야 기존 `_step`·포인터 저장으로 끝난다. 게시글 id 는 발행 뒤에야 있다. 용어가 없다고 글을 멈출 이유가 없고, 배포된 라우트 문서에 뒤늦게 더해지는 라우트가 라운드 생성을 막으면 안 된다(2026-11-07) | 발행 뒤 별도 스캔으로 추출, 리뷰 단계에 포함, D-51 처럼 필수 라우트로 두기 |
| D-65 | 등록 확인은 코드 정확 일치 → AI 의 `same_as`(목록 안만) → 코드 n-gram 후보(사람 참고) → 새 표제어 순이고, 코드 판정이 AI 판정보다 우선한다. 새 표제어·별칭·뜻은 `PROPOSED` 로 저장하고 `APPROVED` 뒤에만 표시한다. 발행 따라 쓰기(`DerivedRecordsWriter`)가 그 글이 제안한 표제어·뜻·별칭을 `approved_by="auto"` 로 승인한다(2026-11-09 사용자 결정. 그 전에는 사람 승인 뒤에만 표시했다). 불변식(사전체 정의 D-88, 구분 문구)에 걸린 뜻은 `PROPOSED` 로 남아 사람이 고쳐 승인한다. 승인은 전역이다. 후보 검사(표면형·앵커·전제 지식·중복)는 파서가 아니라 `GlossaryService` 가 항목 단위로 버린다. 사전이 `GLOSSARY_FULL_LIST_MAX` 를 넘으면 글이 많이 가리키는 표제어부터 남긴다(2단계 호출 전까지) | 지어낸 정의와 잘못된 동일시가 독자에게 보이면 사전이 신뢰를 잃는다. 정확 일치는 AI 가 틀릴 수 없는 영역이다. 등록 확인은 DB 조회를 포함해 입력 문맥만 보는 파서에 둘 수 없다 | 자동 표시 뒤 사후 수정, 매번 AI 판정, 임베딩 유사도 |
| D-66 | 첫 등장 표면형만 감싸고, 툴팁은 이 글에서 쓰인 뜻을 영역 라벨과 함께 맨 위에, 같은 표제어의 다른 승인된 뜻을 그 아래 보인다. 툴팁 컴포넌트는 뜻 키 기반이다: 글 페이지는 글 단위 응답으로 내용을 미리 받고, 없을 때만 표제어 키로 조회한다. vans_story 는 보일 때 TipTap JSON 에 인라인 노드 `glossaryTerm` 을 넣어 React 노드 뷰가 `TermTooltip`(탭 토글·`content` 추가)을 그린다. 주석 달린 JSON 은 저장하지 않는다 | 뜻 하나만 보일 거면 각주로 족하다. 툴팁의 값은 이 글의 뜻이 어느 영역의 것이고 다른 곳에서는 어떻게 쓰이는지를 함께 보이는 데 있다(2026-11-07 사용자 결정). 호버 때 조회하면 첫 호버가 늦다 | 글이 가리킨 뜻 하나만 표시, 호버 때마다 키로 조회, ProseMirror 장식 플러그인, 서버에서 HTML 생성 |
| D-67 | 제작 기록은 발행 때 영수증에서 만드는 자족적 글 단위 문서 `post_provenance` 다. 순수 함수로 만들고 정리가 `derived_at` 없는 `PUBLISHED` 글을 마저 만들며(배포 뒤 첫 스캔이 기존 글 전부를 채운다), `agents provenance build` 가 단건·`--rebuild` 를 맡는다. 머리말 노트는 그대로 두고 이 문서가 전체(실패한 시도, 전체 읽기·수준 평가·용어 추출 포함)를 보인다 | 영수증은 정리될 수 있고, 글마다 수십 건을 모으는 조회를 매번 하지 않는다. 결정론적이라 다시 만들 수 있다 | 영수증을 매번 조회해 응답, 머리말을 늘려 본문에 넣기 |
| D-68 | 작성자 표기 표준은 승인된 표제어에서 파생한 `terminology` 입력(라운드 스냅샷)을 주제 제안·작성·수정·다듬기 프롬프트에 넣는 것이다. 템플릿 문구는 새 프롬프트 버전에 넣는다. 아직 구현 전이다(08 문서 G-8) | 음차는 주제 제안에서 생겨 끝까지 퍼지므로 첫 호출부터 막아야 한다. 배포된 버전 디렉터리는 고치지 않는다 | 다듬기 정규식 치환만, 리뷰 지적 |
| D-69 | (2026-11-07 1a 로 소멸: 템플릿은 버전 문서 `prompts.haiku.glossary` 하나다, 09 문서) `glossary.md` 템플릿은 `prompts/v4/haiku/glossary.md` 와 `prompts/v3/glossary.md`(평면)에 같은 파일로 둔다. 로더는 활성 버전 디렉터리만 보므로 `PROMPT_VERSION=v3` 로 되돌려도 찾아야 하고, 가짜 벤더 테스트는 평면 디렉터리를 쓴다 | 배포된 버전 디렉터리에 새 이름을 더하는 것은 기존 호출의 문구를 바꾸지 않는 추가다(08 문서 Q-25 해소, 2026-11-07) | v4 전환 뒤 도입, GLOSSARY 만 고정 버전 |
| D-47 | 프롬프트 v2. 작성·수정 프롬프트에 한국어 문장 규칙(한 문장 한 개념, 분량은 설명에, 개발자·상황 주어, 명령형·완충 어미 금지)을 넣는다 | 발행 글에서 번역투와 과밀한 문장이 보였다. 리뷰는 어조·문장 리듬을 지적하지 않고(톤 보존 규칙) 표현은 MINOR 라 발행을 막지 못하므로 작성 단계에서 정한다. 수정 호출은 작성 프롬프트를 보지 않으므로 두 템플릿에 모두 둔다 | 리뷰에서 문체를 지적하게 하기(톤 보존 규칙과 충돌한다), v1 문구를 직접 고치기(버전 디렉터리는 배포 후 고치지 않는다) |

## 2. 미결 사항

| ID | 질문 | 영향 | 기본 가정 (결정 전까지) |
|---|---|---|---|
| Q-03 | 실제 슬롯 ↔ 모델 바인딩과 모델 ID | 설정값. 도메인 영향 없음 | A=architect, B=systems_engineer, C=troubleshooter 페르소나. 모델은 설정 |
| Q-04 | 단일 인스턴스 전제를 유지할지 | 다중 인스턴스면 글 단위 실행 소유권(lease)과 트랜잭션(replica set)이 다시 필요하다 | 단일 (CONVENTIONS 3절과 동일). 실행 모델 전체가 이 전제에 기댄다(D-25) |
| Q-05 | 주제 중복 판별에 임베딩을 쓸지 | 임베딩 API 의존 추가 | 1차는 키워드·제목 토큰 유사도만. 임베딩은 2단계 |
| Q-06 | 코드 블록 문법 검사에 외부 컴파일러(Go 등 subprocess)까지 붙일지 | Docker 이미지 크기, 게이트 신뢰도 | subprocess 없이 경량 검사(펜스 짝, 빈 블록 금지, `python` 은 `ast.parse`, `json` 은 `json.loads`, TS 는 tree-sitter, D-57)만 수행 |
| Q-07 | 글 토큰 예산 수치(`DRAFT_TOKEN_LIMIT`) | 소프트 상한 민감도. 추정치가 문자 수 기준이라 영어 위주 입력에서는 크게 잡힌다 | 첫 라운드 몇 번 돌려 글당 실사용을 본 뒤 결정. 초기엔 넉넉히 |
| Q-08 | 글 길이 범위(`DRAFT_MIN_CHARS`, `DRAFT_MAX_CHARS`) | 게이트, 프롬프트 | 4,000 ~ 20,000자 |
| Q-09 | 요약 노트의 모델명 문구를 운영 후에 뺄지 | 독자 신뢰 | 템플릿에 모델명을 넣는다 (05 문서). 빼는 것은 나중에 |
| Q-10 | `ESCALATED` 해소 UI: CLI 로 충분한가, 간단한 관리자 페이지가 필요한가 | 범위 | 1차는 CLI + `GET .../escalation/` JSON |
| Q-11 | 라운드 실행 트리거: 수동(CLI/API)만인지, 주기 스케줄(cron)도 두는지 | 러너 설계 | 1차 수동 |
| Q-13 | 발행본 본문을 이 서비스가 들고 자기 표절 검사를 할지 | 게이트, 저장 | 1차 게이트에서 제외. 레지스트리에는 제목·키워드만 있다 |
| Q-14 | 근거를 들어 기각한 MAJOR 도 중재할지 | 범위 밖 판단(`OUT_OF_SCOPE`)은 MAJOR 도 중재한다(D-48). 남은 경우는 작성자가 지적이 틀렸다고 기각한 MAJOR 를 리뷰어가 받아들이지 않을 때로, 반복 상한까지 돈 뒤 `ESCALATED`다 | 범위 밖 판단만 중재 |
| Q-16 | 에이전트 내부 API 키를 검색 내부 API 키(`INTERNAL_API_KEY`, NestJS Post BE와 공유)와 나눌지 | 같은 키면 Post BE가 라운드 생성·결정 API도 부를 수 있다 | 해소(2026-11-02, D-44): 별도 `AGENTS_INTERNAL_API_KEY` |
| Q-15 | 중재자 독립성 | 해소(2026-11-05, D-52): 작성·리뷰 벤더 밖의 `ARBITRATE` 라우트 후보다 | – |
| Q-18 | 수준 라벨을 블로그에 보일지 | `vans_story_be_post` DTO 와 `vans_story` 표시 수정이 필요하다. 배포 순서는 Post BE → 프런트 → 이 서비스 | `PUBLISH_POST_LEVEL=false`. 저장만 하고 보내지 않는다 |
| Q-19 | `UNVERIFIABLE` 을 MAJOR 로 올릴지, 낮은 수준 글을 막을지, 렌즈 구성을 바꿀지 | 오탐률·일치도·재현율 측정이 필요하다 | 막지 않는다. 렌즈 셋 유지 |
| Q-20 | 용어 추출 입력의 전체 목록 상한(`GLOSSARY_FULL_LIST_MAX`)과 2단계 호출(`GLOSSARY_MATCH`) 도입 시점 | 입력 토큰. 넘으면 지금은 글이 많이 가리키는 표제어부터 남긴다 | 300. 넘기 전까지 2차 호출을 만들지 않는다 (08 문서) |
| Q-21 | AI 의 `same_as` 판정으로 이미 승인된 뜻에 붙은 항목을 글 단위 확인 없이 바로 보일지 | 항목 단위 확인을 두려면 `PostGlossaryItem` 에 플래그가 필요하다 | 보인다. 표시 조건은 뜻·표제어의 `APPROVED` 뿐이고 틀린 동일시는 `drop` |
| Q-22 | 사람이 쓴 글의 용어 추출(TipTap JSON → 텍스트) | 변환기 하나, `draft_id` 없는 `post_glossary` | 범위 밖 |
| Q-23 | 다듬기 정규식 단계에 승인 별칭 → `term_ko` 치환을 넣을지 | 한국어 ↔ 한국어 치환만 가능 | 넣지 않는다. 표기 표준은 작성 프롬프트 입력(D-68) |
| Q-24 | 제작 기록 공개 범위와 `agent_calls` 정리 정책 | 독자에게 보이는 정보, 저장 비용 | 호출 오류 문자열만 빼고 전부 공개. 영수증 정리는 제작 기록이 있는 글만, 시점 미정 |
| Q-26 | ES 용어 색인 시점 | `services/indexing.py` 확장 | 전역 페이지에서 Mongo 정규식이 느려질 때 (08 문서 2.9절) |
| Q-27 | 영어 UI 에서 정의를 영어로도 보일지 | 뜻마다 `definition_en` | 한국어만 |
| Q-28 | `/glossary` 전역 페이지 범위 | 프런트 2단계 | 표제어 목록 + 뜻 번호 + 글 링크, 검색은 `q` 하나 |
| Q-29 | 글 끝 "용어"·"제작 기록" 블록의 위치와 접힘 | 독자 동선 | 둘 다 접힘. 태그 아래 |
| Q-30 | 영역 표 `DOMAINS` 를 코드 고정으로 둘지 Mongo 설정 문서로 둘지 | 표시 이름 i18n, 늘릴 때 배포 필요 | 코드 고정 12개 |
| Q-31 | 병합 제안(`agents glossary dedupe`)에 LLM 판정을 붙일지 | 템플릿·계약 하나 더. 지금은 코드 유사도 쌍만 찍는다 | 사전이 몇백 개가 된 뒤 |

## 3. 구현 로드맵

각 단계는 독립적으로 머지·테스트 가능해야 한다.

| 단계 | 내용 | 완료 기준 |
|---|---|---|
| 0 | CONVENTIONS에 `agents/` 계층·비동기 규칙 추가 | 완료(2026-10-30). 앱·health·ruff·pytest는 기존 것을 쓴다 |
| 1 | `models/` 전부 + `PostDraft` 전이 + `services/evaluation.py` + 단위 테스트 | 전이표 행마다 테스트 통과. DB·LLM 없음. 완료(2026-10-30) |
| 2 | `repositories/`(async Mongo) + `AgentCallService`(재시도, 영수증, 소프트 예산) + `FakeLLMClient` | 호출 서비스 테스트 통과. 완료(2026-11-02) |
| 3 | `agents/`(페르소나, 계약, 빌더, 파서, 프롬프트 v1) + `models/tiptap.py` + `ValidationGate` | 파서 위반 케이스 테스트, 게이트 테스트, 변환 golden 테스트. 완료(2026-11-02) |
| 4 | `TopicSelectionService` + `DraftPipelineService.drive` + `PipelineRunner` + CLI `run` | 가짜 LLM으로 문서 06의 시나리오 ①~⑫ 통과. 완료(2026-11-02). `RoundService`도 여기서 만들었고 `DraftDecisionService`는 6단계로 미뤘다 |
| 5 | `clients/llm/` 벤더 3종 + Post BE 카테고리 조회 + e2e 호출 테스트(계약 출력 스키마 6종) | 실제 모델로 라운드 1회 완주 (발행은 dry-run). 코드·단위 테스트 완료(2026-11-02). 실제 벤더 e2e 와 라운드 완주는 키를 넣고 사용자가 돌린다(`pytest -m e2e tests/e2e/test_llm_vendors.py`, `agents start-round --wait` + `agents run`) |
| 6 | `PublishingService` + `clients/publisher/` 발행(봇 계정 로그인, 실패 분류) + 요약 노트 + 승인 토글 + `PUBLISH_UNCERTAIN` 해소 + `DraftDecisionService` + 라우터·golden + 결정 CLI | 벤더별 관리자 계정(D-45)을 만들고, 승인 후 Post BE 에 발행해 블로그에서 사람이 쓴 글과 같게 보인다. 코드·단위 테스트 완료(2026-11-02). 벤더별 계정을 만들어 `.env`에 넣고(`USER_SERVICE_URL`·`{ANTHROPIC,GOOGLE,XAI}_PUBLISHER_EMAIL`·`{ANTHROPIC,GOOGLE,XAI}_PUBLISHER_PASSWORD`·`AGENTS_INTERNAL_API_KEY`) `pytest -m e2e tests/e2e/test_post_be.py` → 라운드 1회 → `agents approve` → 블로그 확인은 사용자가 한다 |
| 7 | 운영: 토큰 측정 → Q-07 확정, 프롬프트 튜닝(`reported_verdict`·`derived_verdict` 불일치 지표), `replay` 명령 | 라운드 3회 이상 운영 후 승인 토글 해제 검토. 도구는 완료(2026-11-02): `services/metrics.py` + `agents metrics` + `GET /api/v1/agents/metrics/`(4절 지표 전부), `AgentCallService.replay` + `agents replay`. Q-07 확정·프롬프트 튜닝·토글 해제는 실제 라운드를 돌린 뒤 사용자가 한다 |

| 8 | 품질 개선(`.claude/reviews/model_routing_and_expertise_review.md` 0~4단계): 범위·서사 규칙, 호출별 라우트, 수준 라벨, 사실 확인·렌즈 구조, 다듬기 | 코드·단위 테스트 완료(2026-11-05, 가짜 LLM). 실제 모델로는 돌리지 않았다. 키를 넣고 `pytest -m e2e tests/e2e/test_llm_vendors.py` → `agents seed-routes` → 라운드 → `agents metrics`·`compare-prompts`·`fact-check` 로 사용자가 확인한다. 밀도 감소와 "글처럼 읽힌다"는 판단, 재현율·오탐, 수준 일치도가 완료 기준이다 |
| 9 | 용어 사전·제작 기록(08 문서 G-1~G-9): 모델·리포지토리·`GLOSSARY` 호출·`POST_REVIEW` 단계·발행 따라 쓰기·정리·CLI·공개 읽기 API·vans_story 툴팁·블록 | FastAPI 쪽(G-1~G-5) 코드·단위 테스트 완료(2026-11-07, 가짜 LLM). 남은 것: `agents seed-routes` 로 `GLOSSARY` 라우트 넣기, 배포, 라운드 1회 뒤 `agents glossary pending` → 승인, `agents glossary extract --missing`, vans_story 실제 확인. 표기 표준 입력(G-8)과 2단계(G-9)는 뒤 |
| 10 | 데이터 파이프라인(09 문서 7절): (1a) 프롬프트·라우트·페르소나·조절 값을 버전 문서로 → (1b) D-80·D-81·D-84 → (1c) D-82·D-83 → (2) 단계 목록·실행기 → (3) 정리 | 1a 코드·단위 테스트 완료(2026-11-07, 가짜 LLM. `pipeline/baseline/` 과 기준 생성기의 해시 동일). 실제 시드(`agents pipeline bootstrap --routes mongo`)·배포는 사용자가 한다. 1b·1c·2·3 은 뒤 |

## 4. 측정 지표 (운영 후 튜닝 입력)

| 지표 | 의미 |
|---|---|
| fast-track 비율 | `READY_TO_PUBLISH` 에 닿은 글 중 v1 이 그대로 발행본인 비율(게이트 실패 뒤 v2 로 간 글은 아니다). 너무 높으면 리뷰가 느슨, 너무 낮으면 작성 프롬프트 문제 |
| 평균 iteration | 수렴 속도 |
| `ESCALATED`·`FAILED` 비율과 사유 분포 | 상한 튜닝 |
| 중재 발생률과 `UPHOLD_AUTHOR` 비율 | 리뷰어가 과하게 CRITICAL 을 남발하는지 |
| `reported_verdict ≠ derived_verdict` 비율 (렌즈별, `PeerReview`에서 계산) | 리뷰어 페르소나가 severity 기준을 잘 따르는지 |
| 게이트 실패율 (항목별) | 게이트를 거친 버전(통과 또는 `validation_errors` 있음) 중 떨어진 비율과 사유별 건수. 아직 `VALIDATING` 인 버전은 세지 않는다. 작성 프롬프트의 구조 지시 품질 |
| 글당 토큰·비용 | 예산 상한 결정 |
| 유지 비율(`REJECTED_WITH_REASON`·`OUT_OF_SCOPE`)과 `OUT_OF_SCOPE` 비율 | 작성자 방어권이 작동하는지 (0% 면 무조건 수용, 너무 높으면 고집) |
| 버전별 밀도·길이 증가율 | 문단당 문장·인라인 코드, 인라인 코드 4개 이상 문단. 프롬프트 효과와 "수정이 덧붙이기였는지"를 본다 |
| 주장 판정별 건수 | `UNVERIFIABLE` 을 MAJOR 로 올릴지 정하는 오탐률의 입력 |
| 다듬기 적용·버림 사유 | 안전장치 통과율 |
| 수준 일치도 | 평가자와 사용자 라벨(`agents label`). 처음에는 100·200 과 300 이상의 구분만 본다 |
| 모델·렌즈별 토큰, 잘린 호출 수 | effort·출력 한도 결정 |
| 글당 추출 용어 수와 `discarded` 사유별 건수 | 추출 프롬프트의 선정 기준과 표면형 규칙 (08 문서 7절. `agents glossary show` 로 읽는다. `metrics` 집계는 아직 없다) |
| `resolution` 비율(EXACT·SAME_AS·NEW·HUMAN), 제안 대비 승인율, 보류 비율 | 사전이 쌓일수록 NEW 가 줄어야 한다. 지어낸 정의의 빈도 |
