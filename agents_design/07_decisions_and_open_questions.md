# 07. 결정 기록과 미결 사항

## 1. 확정한 결정

| ID | 결정 | 이유 | 대안 |
|---|---|---|---|
| D-01 | 별도 서비스로 구현하고 컨벤션은 공유한다 | 장기 실행 비동기 러너, 벤더 키, 배포 주기가 검색 서비스와 다르다 | 같은 저장소에 추가(구조는 06 문서 그대로 합칠 수 있다) |
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
| D-40 | Post BE 인증은 에이전트 전용 관리자 계정으로 발행마다 `vans_story_be`에 로그인해 받은 토큰을 쓴다. 게시글 작성자는 이 계정이다 | Post BE 글 생성은 관리자 JWT(HS512, `sub`=이메일)를 요구한다. `JWT_SECRET`을 공유해 직접 서명하면 이 서비스가 어떤 사용자로든 위장할 수 있다 | `JWT_SECRET` 공유 후 직접 서명 |
| D-41 | 카테고리는 주제 제안이 라운드 스냅샷(`PublishingRound.categories`)에서 한 쌍을 고르고, 파서가 목록에 있는지 검사한다 | 승인 토글을 끈 뒤에도 사람 없이 정해져야 한다. 잘못 고른 카테고리는 발행 후 관리자가 블로그 에디터에서 고친다 | 승인 때 사람이 지정 |
| D-42 | 관리자 수동 격리(`escalate(MANUAL)`)를 두지 않는다. 라운드 상태에서도 `OPEN`을 없애 라운드는 `TOPIC_SELECTION`으로 생긴다 | 수동 격리는 거는 API·CLI가 없었고, 발행 호출과 겹치면 이미 올라간 게시글을 놓쳤으며, 원래 상태로 돌아갈 경로도 없었다. 글은 상한으로 반드시 멈추고, 승인 토글이 발행을 막으며, 버릴 때는 `ESCALATED`·`FAILED`의 폐기나 라운드 중단을 쓴다. `OPEN`은 생성 API가 거치지 않는 상태였다 | `escalate(MANUAL)` + API·CLI + 복귀 경로, `OPEN → start_round` |

## 2. 미결 사항

| ID | 질문 | 영향 | 기본 가정 (결정 전까지) |
|---|---|---|---|
| Q-01 | 별도 저장소(`vans_devblog_agents`)를 git 저장소로 초기화할 시점과 원격 호스트 | 로드맵 0단계 | 구현 시작 시 초기화. 호스트는 다른 `vans_*` 저장소와 동일 |
| Q-03 | 실제 슬롯 ↔ 모델 바인딩과 모델 ID | 설정값. 도메인 영향 없음 | A=architect, B=systems_engineer, C=troubleshooter 페르소나. 모델은 설정 |
| Q-04 | 단일 인스턴스 전제를 유지할지 | 다중 인스턴스면 글 단위 실행 소유권(lease)과 트랜잭션(replica set)이 다시 필요하다 | 단일 (CONVENTIONS 3절과 동일). 실행 모델 전체가 이 전제에 기댄다(D-25) |
| Q-05 | 주제 중복 판별에 임베딩을 쓸지 | 임베딩 API 의존 추가 | 1차는 키워드·제목 토큰 유사도만. 임베딩은 2단계 |
| Q-06 | 코드 블록 문법 검사에 외부 컴파일러(TS, Go 등 subprocess)까지 붙일지 | Docker 이미지 크기, 게이트 신뢰도 | 1차는 subprocess 없이 경량 검사(펜스 짝, 빈 블록 금지, `python` 은 `ast.parse`, `json` 은 `json.loads`)만 수행 |
| Q-07 | 글 토큰 예산 수치(`DRAFT_TOKEN_LIMIT`) | 소프트 상한 민감도. 추정치가 문자 수 기준이라 영어 위주 입력에서는 크게 잡힌다 | 첫 라운드 몇 번 돌려 글당 실사용을 본 뒤 결정. 초기엔 넉넉히 |
| Q-08 | 글 길이 범위(`DRAFT_MIN_CHARS`, `DRAFT_MAX_CHARS`) | 게이트, 프롬프트 | 4,000 ~ 12,000자 |
| Q-09 | 요약 노트의 모델명 문구를 운영 후에 뺄지 | 독자 신뢰 | 템플릿에 모델명을 넣는다 (05 문서). 빼는 것은 나중에 |
| Q-10 | `ESCALATED` 해소 UI: CLI 로 충분한가, 간단한 관리자 페이지가 필요한가 | 범위 | 1차는 CLI + `GET .../escalation/` JSON |
| Q-11 | 라운드 실행 트리거: 수동(CLI/API)만인지, 주기 스케줄(cron)도 두는지 | 러너 설계 | 1차 수동 |
| Q-13 | 발행본 본문을 이 서비스가 들고 자기 표절 검사를 할지 | 게이트, 저장 | 1차 게이트에서 제외. 레지스트리에는 제목·키워드만 있다 |
| Q-14 | MAJOR 분쟁도 중재할지 | 교착은 CRITICAL 한정이라, 작성자가 근거를 들어 기각한 MAJOR를 리뷰어가 받아들이지 않으면 반복 상한까지 돈 뒤 `ESCALATED`다. 덜 심각한 MAJOR가 더 무거운 경로(사람)를 탄다 | 현행(CRITICAL만 중재) |
| Q-15 | 중재자 독립성 | 슬롯이 셋이라 제3 슬롯 중재자는 그 글을 이미 FULL 리뷰한 다른 리뷰어다 | 현행. 독립이 필요하면 중재 전용 바인딩을 설정으로 둔다 |

## 3. 구현 로드맵

각 단계는 독립적으로 머지·테스트 가능해야 한다.

| 단계 | 내용 | 완료 기준 |
|---|---|---|
| 0 | Q-01 결정, 저장소 생성, 컨벤션·ruff·pytest 세팅 | 빈 FastAPI 앱 + health |
| 1 | `models/` 전부 + `PostDraft` 전이 + `services/evaluation.py` + 단위 테스트 | 전이표 행마다 테스트 통과. DB·LLM 없음 |
| 2 | `repositories/`(async Mongo) + `AgentCallService`(재시도, 영수증, 소프트 예산) + `FakeLLMClient` | 호출 서비스 테스트 통과 |
| 3 | `agents/`(페르소나, 계약, 빌더, 파서, 프롬프트 v1) + `models/tiptap.py` + `ValidationGate` | 파서 위반 케이스 테스트, 게이트 테스트, 변환 golden 테스트 |
| 4 | `TopicSelectionService` + `DraftPipelineService.drive` + `PipelineRunner` + CLI `run` | 가짜 LLM으로 문서 06의 시나리오 ①~⑫ 통과 |
| 5 | `clients/llm/` 벤더 3종 + Post BE 카테고리 조회 + e2e 1회 호출 테스트 | 실제 모델로 라운드 1회 완주 (발행은 dry-run) |
| 6 | `PublishingService` + `clients/publisher/` 발행(봇 계정 로그인, 실패 분류) + 요약 노트 + 승인 토글 + `PUBLISH_UNCERTAIN` 해소 + 라우터·golden | 에이전트 전용 관리자 계정을 만들고, 승인 후 Post BE 에 발행해 블로그에서 사람이 쓴 글과 같게 보인다 |
| 7 | 운영: 토큰 측정 → Q-07 확정, 프롬프트 튜닝(`reported_verdict`·`derived_verdict` 불일치 지표), `replay` 명령 | 라운드 3회 이상 운영 후 승인 토글 해제 검토 |

## 4. 측정 지표 (운영 후 튜닝 입력)

| 지표 | 의미 |
|---|---|
| fast-track 비율 | v1 에서 바로 발행된 비율. 너무 높으면 리뷰가 느슨, 너무 낮으면 작성 프롬프트 문제 |
| 평균 iteration | 수렴 속도 |
| `ESCALATED`·`FAILED` 비율과 사유 분포 | 상한 튜닝 |
| 중재 발생률과 `UPHOLD_AUTHOR` 비율 | 리뷰어가 과하게 CRITICAL 을 남발하는지 |
| `reported_verdict ≠ derived_verdict` 비율 (슬롯별, `PeerReview`에서 계산) | 리뷰어 페르소나가 severity 기준을 잘 따르는지 |
| 게이트 실패율 (항목별) | 작성 프롬프트의 구조 지시 품질 |
| 글당 토큰·비용 | 예산 상한 결정 |
| `REJECTED_WITH_REASON` 비율 | 작성자 방어권이 작동하는지 (0% 면 무조건 수용, 너무 높으면 고집) |
