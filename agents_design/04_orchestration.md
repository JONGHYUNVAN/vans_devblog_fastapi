# 04. 오케스트레이션

## 1. 실행 모델

- **단일 프로세스.** uvicorn 워커 1개 안에서 lifespan이 소유한 `PipelineRunner`가 `asyncio`로 돈다. 새 스레드는 만들지 않는다(CONVENTIONS 3절). 아래 단순화는 모두 이 전제에 기댄다(07 문서 D-25, Q-04).
- **진행 단위는 코루틴 하나.** 글마다 `DraftPipelineService.drive(draft_id)` 하나, 주제 선정 중인 라운드마다 `TopicSelectionService.drive(round_id)` 하나다. 코루틴은 상태를 읽고, 그 상태의 LLM 호출을 직접 `await`하고, 결과를 도메인에 반영해 한 번 저장하기를 사람 대기나 종결까지 반복한다. 작업 큐, dispatch, heartbeat가 없다.
- **러너는 띄우기만 한다.** 라운드 시작은 CLI 또는 내부 API가 라운드 문서를 만드는 것으로 끝난다. 러너는 기동 시와 `SCAN_INTERVAL_S`마다 코루틴이 없는 진행 대상(`TOPIC_SELECTION` 라운드, `IN_PROGRESS` 라운드의 승인 대기가 아닌 `is_active` 글)을 찾아 띄운다. 같은 대상의 코루틴은 하나다(러너가 `id → asyncio.Task` 표를 가진다). 사람 결정(API·CLI)은 DB만 바꾸고, 다음 스캔이 이어 간다. CLI는 다른 프로세스라 러너를 직접 부를 수 없으므로 이 스캔이 유일한 연결이다.
- **재시작.** 상태는 전부 DB에 있다. 기동하면 마지막으로 저장된 상태부터 이어 가고, 종료 때 진행 중이던 단계의 호출만 다시 한다(병렬 단계면 그 단계의 호출 전부). 잃는 것은 그 호출들의 비용이다.
- **모든 단계는 끝난다.** LLM 호출에는 `REVIEW_TIMEOUT_S`·`DRAFT_TIMEOUT_S`, 발행 HTTP에는 `PUBLISH_TIMEOUT_S`가 있고, 재시도·반복 횟수에는 상한이 있다. 그래서 라운드 시간 상한을 두지 않는다(07 문서 D-34). 벤더별 동시 요청 수는 `asyncio.Semaphore`로 제한한다.
- **저장.** `pymongo.AsyncMongoClient`(06 문서). 글·라운드 저장은 `revision` 낙관적 잠금이다. 사람 결정은 코루틴이 돌지 않는 상태(승인 대기, `ESCALATED`, `FAILED`)에만 쓰므로, 코루틴의 저장과 겹치는 쓰기는 라운드 중단뿐이다. 충돌이 나면 코루틴은 결과를 버리고 다시 읽는다. 사람·관리자 쓰기는 다시 읽고, 전제 상태가 여전히 맞으면 재적용하고 아니면 409다. 버린 호출의 비용은 `AgentCall`에 남는다.
- **트랜잭션 없음.** 여러 문서를 고치는 동작은 커밋 지점이 되는 쓰기 하나를 정하고, 나머지를 결정론적 ID로 멱등하게 쓴다. 중간에 죽거나 충돌로 멈추면 정리(`RoundService.reconcile`)가 마저 쓴다. 정리는 스캔마다 돈다.

| 동작 | 쓰기 순서 (★ 커밋 지점) | 중간에 멈추면 |
|---|---|---|
| 주제 확정 | 전체 잠금을 `TopicAttempt`에 저장 → 글 3편·레지스트리 3건 `RESERVED`를 없을 때만 insert(`$setOnInsert`) → ★ 라운드 `IN_PROGRESS` + `draft_ids` (조건 `status==TOPIC_SELECTION`) | 라운드가 아직 `TOPIC_SELECTION`이면 코루틴이 저장된 잠금으로 같은 ID를 다시 쓴다. 이미 있는 문서는 건드리지 않으므로 중단이 정리한 문서를 되살리지 않는다. 글 코루틴은 `IN_PROGRESS` 라운드의 글만 돌리므로 앞쪽만 써진 글은 돌지 않는다 |
| 라운드 중단 | ★ 라운드 `ABORTED` (조건: `COMPLETED`·`ABORTED` 아님) → `PUBLISHED` 외 글 `reject_by_abort` → 그 라운드의 `PUBLISHED` 아닌 레지스트리 `RELEASED` | 정리가 `ABORTED` 라운드의 남은 글·레지스트리를 마저 정리한다. 확정 도중 중단이라 뒤늦게 만들어진 글·레지스트리도 여기서 거둔다 |
| 발행 성공 | ★ 글 `PUBLISHED` + `Publication` (조건 `status==READY_TO_PUBLISH`) → 레지스트리 `PUBLISHED` | 정리가 `PUBLISHED` 글의 레지스트리를 맞춘다 |
| 글 폐기 (`REJECT`, `discard`) | ★ 글 `REJECTED` → 레지스트리 `RELEASED` | 같은 정리 |
| 라운드 완료 | 글이 종결될 때마다(코루틴이든 사람 결정이든) `close_if_complete`: ★ `status==IN_PROGRESS`이고 3편 모두 종결이면 `COMPLETED` | 정리가 다시 확인한다. 중단이 먼저 `ABORTED`를 썼으면 조건이 맞지 않아 쓰지 않는다 |

- 중단은 진행 중인 호출을 끊지 않는다. 코루틴은 단계마다 라운드 상태를 읽어 멈추고, 이미 나간 호출의 결과는 저장 충돌이나 라운드 상태 확인에서 버려진다. 서버 종료 때만 러너가 코루틴을 cancel한다.

```
PipelineRunner.scan():                       # 기동 시와 SCAN_INTERVAL_S 마다
    await round_service.reconcile()          # 1절 표의 따라 쓰기를 마저 한다
    for round in rounds where status == TOPIC_SELECTION and round.id not in running:
        spawn(topic_selection.drive(round.id))
    for draft in drafts where round(draft).status == IN_PROGRESS and draft.is_active
                          and not awaits_approval(draft) and draft.id not in running:
        spawn(draft_pipeline.drive(draft.id))

DraftPipelineService.drive(draft_id):
    loop:
        draft, round = 다시 읽기
        if round.status != IN_PROGRESS or not draft.is_active or awaits_approval(draft):
            return
        draft = await _step(draft)       # 호출 → 도메인 반영 → save 한 번. 충돌이면 버리고 다시 읽기
        if draft 가 종결: await round_service.close_if_complete(round.id)
```

`_step`은 현재 상태에서 한 걸음만 진행하고 한 번 저장한다. 저장 단위가 작아 재시작 때 잃는 것이 그 단계의 호출뿐이다. 예상하지 못한 예외는 글을 `FAILED(INTERNAL_ERROR)`로, 주제 선정이면 라운드를 `ABORTED(INTERNAL_ERROR)`로 두고 로그를 남긴다. 다음 스캔이 같은 오류로 호출을 되풀이하지 않게 하기 위해서다.

## 2. Phase 1: 주제 선정 (유일한 배리어)

### 입력

- `theme_keyword` (선택). 있으면 세 슬롯 모두 이 범위 안에서 제안
- 레지스트리(`TopicRegistryEntry`)에서 최근 `TOPIC_AVOID_LOOKBACK_DAYS`일 발행 주제의 title·keywords (중복 회피 컨텍스트)
- 슬롯의 `PersonaProfile.writer_bias` (05 문서)
- 라운드의 `categories` 스냅샷. 제안마다 한 쌍을 고른다 (05 문서 2.1)

### 절차

1. 주제 선정 중인 라운드는 한 번에 하나다(`agent_rounds`의 부분 unique 인덱스, 02 문서 6절). 그래서 다른 라운드와 동시에 주제를 고르지 않고, 라운드 사이 중복은 레지스트리 비교만으로 막는다.
2. 잠기지 않은 슬롯에 `PROPOSE_TOPIC`을 동시에 호출한다(`gather`). 각 슬롯은 **순위를 매긴 3개** `TopicProposal`을 낸다. 받은 제안은 `TopicAttempt`에 저장해, 재시작하면 빠진 슬롯만 다시 부른다. 한 슬롯이 재시도를 모두 소진하면 다른 호출이 끝난 뒤 라운드를 `ABORTED`(`LLM_FAILED`)로 둔다. 글이 없으므로 `ESCALATED`로 올릴 대상이 없다. 주제 제안 호출은 예산을 검사하지 않는다(02 문서 3절 예산).
3. 중복 판별(결정론적). 아래 중 하나면 충돌이다.
   - 키워드 Jaccard ≥ `TOPIC_KEYWORD_OVERLAP`(0.5). 키워드가 각 3개일 때 2개 일치가 2/4 = 0.5 이고, 1개 일치는 1/5 = 0.2 라 충돌이 아니다.
   - 제목을 공백·기호 기준으로 나눈 토큰 집합의 Jaccard ≥ `TOPIC_TITLE_OVERLAP`(0.5).
   - `RESERVED` 또는 `PUBLISHED` 인 레지스트리 항목과 위 기준으로 겹침. `RELEASED`와 같은 라운드의 항목은 비교하지 않는다(확정 도중 재시작하면 자기 항목이 이미 있을 수 있다).
   - 동의어·번역("인덱싱"과 "index")은 1차에서 잡지 못한다. 임베딩 비교는 Q-05.
4. 배정: 슬롯마다 제안 하나를 고르는 조합은 3³ = 27개다. 그 가운데 충돌이 없는 조합 중 순위 합이 가장 작은 것을 고른다. 동률이면 `round_seq % 3` 으로 우선 슬롯을 정한다.
5. 그런 조합이 없으면 충돌 없는 부분 배정 중 **잠글 수 있는 슬롯 수가 가장 많고**, 그다음 **순위 합이 가장 작으며**, 그다음 `round_seq` 우선순위가 빠른 배정을 고른다. 선택 결과를 `LockedTopic` 스냅샷으로 남기고 나머지 슬롯만 재제안한다. 재제안의 회피 목록은 잠긴 주제와 레지스트리 항목이다. 글 문서는 아직 만들지 않는다. `MAX_TOPIC_ATTEMPTS`는 초기 제안을 포함한 총 시도 수다.
6. 상한까지 세 슬롯을 못 채우면 라운드는 `ABORTED`다. 레지스트리에 남기지 않는다.
7. 세 슬롯이 모두 잠기면 1절 표의 순서로 확정한다. 전체 잠금을 `TopicAttempt`에 먼저 저장하고, 결정론적 ID의 `PostDraft` 3개(`token_limit = DRAFT_TOKEN_LIMIT`)와 `TopicRegistryEntry`(`RESERVED`) 3개를 없을 때만 만든 뒤, 라운드를 `IN_PROGRESS`로 바꾸고 `draft_ids`를 저장한다. 다음 스캔이 글 코루틴을 띄운다.

## 3. Phase 2: 초안 작성과 결정론적 게이트

### 작성자 입력

- `TopicProposal`, `PersonaProfile.writer_bias`, 글 구조 제약(필수 섹션), 섹션 앵커 규칙
- `previous` 는 항상 직전 제출 버전(게이트 실패본 포함)이다.
- 리뷰를 마친 버전이 있을 때: 그 버전의 리뷰(`open_blocking_items` 기준 `ReviewItem` + `VerificationChecklist`), 중재 판정. 그 버전이 FULL 리뷰 버전이면 비차단(MINOR·NITPICK) 항목도 넘긴다. 이월 항목에는 SPOT_CHECK 리뷰어가 아직 미해소로 본 이유(`ItemCheck.note`)를 붙인다. 직전 제출이 게이트에 떨어진 경우에도 `reviews`·`resolutions` 대상은 마지막 리뷰 완료 버전이다(예: v2 가 게이트에 떨어지면 `previous` 는 v2 본문, `reviews` 는 v1 의 `open_blocking_items`).
- 직전 제출이 게이트에서 떨어졌으면: `validation_errors` 를 함께 넘긴다. 리뷰 완료 버전이 아직 없으면(v1 재작성) `reviews` 는 빈 목록이다.

### 섹션 앵커

작성자는 마크다운에 아래 마커를 포함해야 한다. 리뷰 항목의 `section_id`가 이 앵커를 가리키므로 버전이 바뀌어도 대조가 가능하다.

```
<!-- sec:intro -->        서론: 문제 정의
<!-- sec:body-1 --> …     본론: 해결 과정 (개수 자유, 번호 연속)
<!-- sec:code-N -->       코드 블록 바로 위. section_id 는 code-N (N 은 1부터)
<!-- sec:tradeoff -->     트레이드오프
<!-- sec:conclusion -->   결론
```

수정 시 기존 앵커 ID를 **유지**한다. 지적이 가리키는 섹션을 삭제·병합하면 그 항목의 `ItemResolution.section_id_after`에 새 위치를 적는다. 지적이 가리키지 않는 앵커는 바꿔도 된다. 앵커는 발행 변환(8절)에서 지운다.

### VALIDATING 게이트 (LLM 호출 없음)

| 검사 | 실패 시 |
|---|---|
| 출력 JSON 스키마(`DraftOutput`) 유효 | 파싱 실패 → 호출 재시도 (게이트가 아니라 호출 실패로 취급) |
| 필수 앵커(`intro`, `body-1`, `tradeoff`, `conclusion`) 존재, 중복 없음 | 게이트 실패 |
| 코드 블록 1개 이상, 펜스(` ``` `) 열림·닫힘 짝 맞음, 각 블록에 언어 태그, 비어 있지 않음(공백 제외 1자 이상), 바로 위 `code-N` 앵커 | 게이트 실패 |
| 코드 블록 구문 검사(경량, subprocess 없음): 태그가 `python` 이면 `ast.parse` (`python-snippet` 은 제외), `json` 이면 `json.loads` | 실패하면 게이트 실패. 그 외 언어(TS, C#, SQL 등)는 빈 블록·펜스 짝만 검사한다 |
| 본문 길이 `DRAFT_MIN_CHARS` 이상 `DRAFT_MAX_CHARS` 이하. 앵커와 코드를 포함한다 | 게이트 실패 |
| `markdown_to_tiptap` 변환 성공. 8절 변환표의 문법만 쓴다. HTML(섹션 앵커 제외), 이미지, 각주, `mermaid` 코드 블록이 있으면 실패 | 게이트 실패 |
| `origin_cause == REVIEW` 인 버전: 아래 두 층 검사 | 게이트 실패. 첫 초안(`None`)과 사람 재수정(`HUMAN`)에는 적용하지 않는다 |
| 리뷰 완료 이전 버전이 있으면, 그 버전의 `open_blocking_items`가 가리키는 앵커가 남아 있다. 없어졌으면 그 항목의 `section_id_after`가 있다 (단 `origin_cause == HUMAN` 은 면제) | 게이트 실패 |
| 외부 링크 형식 | 경고만 기록 |

발행본과의 문장 유사도 검사는 1차에 넣지 않는다. 이 서비스에는 본문 말뭉치가 없다(Q-13).

게이트 실패는 `validation_retries`를 1 올리고 `iteration`은 유지한다. 증가한 값이 상한보다 작으면 `cause=GATE`로 `REVISING`에 두고 코루틴이 이어서 `REVISE_DRAFT`를 호출한다. 상한과 같으면 `FAILED`다. 상한 2이면 연속 재작성은 한 번이다. 게이트를 통과하면 `validation_retries`는 0으로 돌아간다.

`origin_cause == REVIEW`일 때 해소는 두 층으로 검사한다. `resolution.item_id` 중복은 어느 층이든 실패다.

- 차단 항목만 모은 집합은 직전 `open_blocking_items`와 정확히 같아야 한다. 누락은 실패다. `UPHOLD_REVIEWER` 항목은 `ACCEPTED`만 허용하고, `REJECTED_WITH_REASON`에는 `reason`이 있어야 한다.
- 직전 리뷰 완료 버전이 FULL 리뷰 버전이면 그 리뷰의 `MINOR`·`NITPICK`은 생략해도 된다. 넣으면 `ACCEPTED` 또는 `DEFERRED`만 허용한다. SPOT_CHECK 버전에는 비차단 항목이 없다(신규 CRITICAL만 남는다).
- 두 층 어디에도 없는 ID는 실패다.

## 4. Phase 3: FULL 리뷰

- `IN_REVIEW`에서 `reviewer_assignments`의 두 슬롯 중 아직 리뷰가 없는 슬롯에 `REVIEW(scope=FULL)`를 병렬 호출한다.
- **블라인드**: 리뷰어 입력에 `author_slot`, 모델명, 페르소나명을 넣지 않는다. 본문·주제·렌즈 지시만 넣는다. 블라인드는 리뷰 단계 한정이며 요약 노트에는 저자가 드러난다.
- 리뷰 출력은 `ReviewOutput` 스키마(05 문서). 스키마 위반은 같은 호출 재시도. 재시도를 소진한 리뷰어를 빼고 진행하지 않는다. 리뷰 한 건으로 발행을 결정하지 않는다(07 문서 D-06). 다른 리뷰 호출이 끝나기를 기다려 성공한 리뷰는 저장하고 글을 `FAILED`로 둔다. 재개하면 빠진 리뷰만 호출한다.
- `item_id`, `source_review_id`, `reviewer_slot`, `is_new_in_version` 은 오케스트레이터가 부모 리뷰 문맥으로 붙인다. LLM 출력에는 없다. FULL 항목의 `is_new_in_version` 은 False.
- FULL 의 `derived_verdict`: `REQUEST_CHANGES` 는 차단 등급 항목이 1건 이상일 때다. `reported_verdict` 와 다르면 로그를 남긴다. 지표는 `PeerReview`에서 계산한다(07 문서 4절).
- SPOT_CHECK 의 `derived_verdict`: `item_checks` 중 미해소이거나 신규 CRITICAL 이 있으면 `REQUEST_CHANGES`. `reported_verdict` 는 저장하지 않는다.

## 5. Phase 4: 평가 엔진

`services/evaluation.py` 가 `EvaluationOutcome` 을 만들고, `PostDraft.apply_evaluation` 이 전이한다. 버전 번호로 FULL 과 SPOT_CHECK 를 가르지 않는다. 리뷰를 마친 이전 버전이 있는지로 가른다.

### FULL 리뷰 완료

```
blocking = [item for review in reviews_of(this_version) for item in review.items
            if item.severity in (CRITICAL, MAJOR)]
if not blocking:  → READY_TO_PUBLISH
else:             → REVISING (cause=REVIEW)
```

`reported_verdict` 는 사용하지 않는다. 차단이 없으면 MINOR·NITPICK 은 수정 없이 발행되고, 요약 노트에 "발행을 막지 않은 기록"으로만 남는다.

### 미결 차단 집합 `open_blocking_items(v)`

리뷰(및 중재)가 끝난 버전 `v`에서 다음 수정 버전이 반드시 답해야 하는 항목 집합이다. SPOT_CHECK 버전의 `PeerReview.items`에는 신규 CRITICAL만 들어 있고 이전 버전에서 이월된 항목은 `item_checks`에만 있으므로, 두 출처를 합쳐 정의한다.

```
if v 가 FULL 리뷰 버전:
    open_blocking_items(v) = [i for r in reviews_of(v) for i in r.items
                              if i.severity in (CRITICAL, MAJOR)]
if v 가 SPOT_CHECK 버전:
    carried_over = [find_original_item(c.item_id)
                    for r in reviews_of(v) for c in r.item_checks
                    if not c.resolved and not upheld_author(c.item_id)]
    new_criticals = [i for r in reviews_of(v) for i in r.items
                     if i.severity == CRITICAL]
    open_blocking_items(v) = carried_over ∪ new_criticals
```

`ReviseDraftInput`에 넘기는 항목, 다음 버전 게이트가 `ItemResolution` 존재를 검사하는 대상, 그 다음 SPOT_CHECK에서 각 리뷰어에게 `my_previous_items`로 넘기는 대상(본인 슬롯이 낸 것만 필터)은 모두 이 `open_blocking_items(v)`다. 그래서 v1의 항목이 v2에서 기각 → `UPHOLD_REVIEWER` 판정을 받아 v3로 넘어가도 누락되지 않는다.

### 작성자의 수정

직전 리뷰 완료 버전 `v`의 `open_blocking_items(v)`마다 `ACCEPTED` 또는 `REJECTED_WITH_REASON`이 있어야 한다(`UPHOLD_REVIEWER` 판정을 받은 항목은 `ACCEPTED`만 허용). 같은 리뷰의 `MINOR`·`NITPICK`은 생략하거나 `ACCEPTED` 또는 `DEFERRED`로 덧붙인다. 생략은 게이트 실패가 아니고, `DEFERRED`는 이 비차단 항목에만 쓴다. 리뷰어 둘의 상충 요구는 작성자가 여기서 정리한다. 사람이 `ESCALATED → REVISE`로 지시한 버전(`origin_cause == HUMAN`)은 에이전트 리뷰가 아니라 `human_instruction`을 따르므로 `ItemResolution`을 요구하지 않고, 게이트 통과 시 바로 `READY_TO_PUBLISH`로 간다.

### SPOT_CHECK 완료

리뷰어 범위는 둘이다.

1. 직전 리뷰 완료 버전 `v_prev`의 `open_blocking_items(v_prev)` 중 본인이 냈던 항목의 해소 여부 (`ItemCheck`).
2. 수정으로 새로 생긴 CRITICAL. 파서는 그 외 심각도를 버리고 로그를 남긴다. 호출을 실패시키지 않는다.

미해소 = `resolved=False` 인 항목 ∪ 신규 CRITICAL.

파서는 `item_checks`의 완전성도 검증한다.

- `item_checks.item_id` 중복 금지
- `set(item_checks.item_id) == set(my_previous_items.item_id)`
- 입력에 없던 ID 금지
- `resolved=False`이면 `note`가 비어 있지 않아야 한다

교착 = 미해소 가운데 이번 버전의 `resolution` 이 `REJECTED_WITH_REASON` 이고 심각도가 CRITICAL 이며 아직 중재되지 않은 항목. 신규 CRITICAL 은 이번 버전에 `resolution` 이 없으므로 교착이 아니다. `ACCEPTED` 인데 `resolved=False` 인 항목도 교착이 아니다.

```
if not unresolved:
    → READY_TO_PUBLISH
elif deadlocked:
    → IN_ARBITRATION
elif iteration < MAX_ITERATIONS:
    → REVISING (cause=REVIEW)
else:
    → ESCALATED
```

### 중재

- 중재자는 저자도 그 항목의 리뷰어도 아닌 슬롯이다. 렌즈는 **그 `ReviewItem.lens`**(리뷰어가 해당 항목에 태그한 주/보조 렌즈)를 그대로 쓴다.
- 입력은 해당 섹션의 전후 본문, `ReviewItem`, 기각 사유, `resolved=False` 사유다. 블라인드를 유지한다.
- 교착 항목마다 `ARBITRATE`를 병렬 호출한다. `add_arbitration` 은 저장만 한다. 교착 항목이 모두 모이면 `apply_evaluation` 이 전이한다. 일부가 재시도를 소진하면 리뷰와 같이 성공분을 저장하고 `FAILED`다.
- `UPHOLD_AUTHOR` 항목은 종결되어 `open_blocking_items` 에서 빠진다.
- `UPHOLD_REVIEWER` 항목은 `open_blocking_items` 에 남고 다음 수정에서 기각할 수 없다(`must_accept=True`).
- 같은 항목의 중재는 한 번이다.

```
remaining = open_blocking_items(this_version)   # UPHOLD_AUTHOR 가 빠진 뒤의 집합
if not remaining:
    → READY_TO_PUBLISH
elif iteration < MAX_ITERATIONS:
    → REVISING (cause=REVIEW)
else:
    → ESCALATED
```

## 6. Phase 5: 수렴과 상한

| 상한 | 조건 | 동작 |
|---|---|---|
| 반복 상한 | `iteration == MAX_ITERATIONS`인데 차단 항목 잔존 | `ESCALATED(MAX_ITERATIONS_REACHED)` |
| 글 토큰 상한 (소프트) | 글 호출 직전, 그 글의 사용량 합계 + 추정치 > `token_limit` | 호출하지 않고 글 `FAILED(BUDGET_EXCEEDED)`. 재개 때 상한을 올릴 수 있다 |
| 호출 재시도 소진 | 증가 후 시도 수 `== MAX_LLM_RETRIES` | 글 `FAILED(LLM_RETRIES_EXHAUSTED)`, 주제 선정이면 라운드 `ABORTED(LLM_FAILED)` |
| 게이트 재시도 소진 | 증가 후 `validation_retries == MAX_VALIDATION_RETRIES` | 글 `FAILED(GATE_RETRIES_EXHAUSTED)` |
| 예상하지 못한 예외 | 코루틴 안의 버그 | 글 `FAILED(INTERNAL_ERROR)`, 주제 선정이면 라운드 `ABORTED(INTERNAL_ERROR)` |
| 킬 스위치 | 관리자 `abort_round` | 1절 순서. `PUBLISHED` 외 글 `REJECTED` |

**토큰 예산은 소프트 상한이다.** 글마다 `token_limit`을 두고, 글 호출 직전에 그 글의 `AgentCall.usage` 합계에 이번 호출 추정치를 더해 상한과 비교한다. 추정치는 보낼 메시지의 문자 수 + `ModelBinding.max_output_tokens`다. 정확할 필요는 없다. 폭주를 멈추는 장치이고, 실제 사용량은 응답의 `usage`로 기록한다. 병렬 호출이 동시에 검사를 통과하면 상한을 넘을 수 있지만, 넘는 폭은 그 글의 동시 호출 몇 건이다. 라운드 상한은 두지 않는다. 라운드 비용은 글 상한 3개와 주제 선정 호출 수 상한(`MAX_TOPIC_ATTEMPTS × 3 × MAX_LLM_RETRIES`)으로 묶인다. 돈이 실제로 나가는 하드 상한은 벤더 콘솔의 지출 한도로 둔다(07 문서 D-33).

**호출 재시도.** 재시도 루프는 호출 서비스(`AgentCallService.call`)가 끝까지 맡는다. 네트워크·5xx·429·타임아웃은 지수 백오프 뒤 같은 메시지로 다시 부른다. 스키마 위반은 첫 시도 메시지 뒤에 직전 실패 출력과 오류 안내를 붙여 다시 부른다. 시도마다 `AgentCall` 영수증을 insert 하고, 예산 검사도 시도마다 한다.

**병렬 단계의 실패.** 리뷰 2건이나 중재 n건은 `asyncio.gather(..., return_exceptions=True)`로 모두 끝나기를 기다린다. 하나가 재시도를 소진하거나 예산에 걸려도 나머지를 취소하지 않는다. 성공한 결과를 도메인에 넣고 한 번 저장한 뒤 `FAILED`다. 재개하면 결과가 없는 슬롯·항목만 호출한다. 그래서 형제 취소·예약 반환·사후 정산이 없다.

## 7. 가드레일 (프롬프트 수준 규칙, 05 문서 계약에 반영)

| 규칙 | 적용 대상 | 내용 |
|---|---|---|
| 블라인드 리뷰 | 리뷰어, 중재자 | 저자·모델·페르소나 식별 정보 제거. 리뷰어는 "세 글 중 내 글이 아닌 것" 정도만 안다는 한계를 인정하고, 주된 편향 억제 수단은 체크리스트 강제로 둔다 |
| 톤 보존 | 리뷰어 | 지적 가능: 사실 오류, 논리 결함, 코드 버그, 가독성 저하(문장 구조). 지적 불가: 유머, 1인칭 화법, 문장 리듬, 어조, 비속어 아닌 직설 표현. `CRITICAL_PRACTICAL` 렌즈의 "클리셰 제거"는 의미 없는 완충 문구·상투적 도입/마무리·과도한 불릿화에 한정 |
| 체크리스트 강제 | 리뷰어 | `APPROVE`라도 `VerificationChecklist` 필수. 빈 체크리스트는 스키마 위반 |
| 최소 지적 개수 강제 금지 | 리뷰어 | 억지 지적을 만들므로 두지 않는다. 대신 체크리스트로 "무엇을 봤는지"를 요구 |
| 기각에는 근거 | 작성자 | `REJECTED_WITH_REASON.reason` 필수. "원래 의도"만으로는 부족하고 기술적·독자 관점 근거 요구 |
| 중재 결과 복종 | 작성자 | `UPHOLD_REVIEWER` 항목은 다음 버전에서 `ACCEPTED`만 허용. 게이트가 검사 |
| 신규 CRITICAL 만 | SPOT_CHECK 리뷰어 | 그 외 심각도는 파서가 버리고 로그를 남긴다. 호출은 실패하지 않는다 |

## 8. 발행과 패키징

Post BE(`vans_story_be_post`)는 바꾸지 않는다. 이 서비스가 사람이 에디터로 쓴 글과 같은 `CreatePostDto`를 만들어 기존 `POST /api/v1/posts`로 보낸다. 마크다운 → Tiptap 변환, 필드 매핑, 인증, 중복 게시 방지는 모두 이 서비스 책임이다(07 문서 D-27, D-39, D-40).

`READY_TO_PUBLISH → PUBLISHED`:

1. 승인 토글(`PUBLISH_REQUIRES_APPROVAL`)이 켜져 있으면 사람 승인을 기다린다(내부 API `approve`). 기다리는 동안 글 코루틴은 돌지 않고, 승인이 저장되면 다음 스캔이 띄운다.
2. 피어 리뷰 요약 노트 생성(템플릿, LLM 호출 없음):
   - 저자 슬롯의 표시 이름, 리뷰어 둘의 표시 이름과 렌즈
   - 버전 수, 차단 등급 항목 수, 수용/기각 수
   - 기각된 항목과 그 이유
   - fast-track 으로 남은 MINOR·NITPICK 은 "발행을 막지 않아 수정하지 않음"으로 구분한다
   - 중재가 있었으면 판정
3. 글과 라운드를 다시 읽는다. 글이 `READY_TO_PUBLISH`가 아니거나 라운드가 `IN_PROGRESS`가 아니면 발행을 호출하지 않는다.
4. `failed_at`이 없는 `publication_intent`가 남아 있으면 직전 호출이 Post BE에 닿았는지 모른다(호출 중 프로세스 종료). 다시 호출하지 않고 `ESCALATED(PUBLISH_UNCERTAIN)`로 보낸다.
5. 최종 버전 본문 뒤에 요약 노트를 붙여 아래 변환표로 Tiptap JSON을 만들고, 아래 매핑표로 `CreatePostDto`를 채운다.
6. `status==READY_TO_PUBLISH`인 문서에만 `attempt = publish_attempts + 1`인 발행 의도를 저장한다. 이 쓰기가 실패하면 발행을 호출하지 않는다.
7. 에이전트 전용 관리자 계정으로 `vans_story_be`의 `POST /api/v1/auth/login`에 로그인해 액세스 토큰(기본 900초)을 받고, Bearer 토큰으로 `POST /api/v1/posts`를 호출한다. 토큰은 저장하지 않고 발행마다 받는다. 게시글 작성자는 이 계정이다.
8. 성공하면 `status==READY_TO_PUBLISH`인 글에만 `Publication`(응답의 게시글 id)과 상태 `PUBLISHED`를 조건 갱신하고, 이어서 레지스트리를 `PUBLISHED`로 쓴다(1절 순서). 검색 색인은 Post BE가 생성 시 스스로 요청한다.
9. 그 조건 갱신이 실패하면 `PUBLISHED`로 쓰지 않고, 상태와 무관하게 반환된 `external_post_id`를 의도에만 남긴다(`reject_by_abort`도 의도를 지우지 않는다). 이 경쟁이 생기는 쓰기는 라운드 중단뿐이다. 자동으로 내리지 않는다. 운영자가 그 ID로 블로그 글을 회수한다.
10. 결과가 확정된 실패(로그인 단계의 모든 실패, 글 생성 호출의 4xx 응답·연결 거부)면 의도에 `failed_at`을 쓰고 `publish_attempts`를 그 `attempt`로 맞춘다. 상한 미만이면 지수 백오프 뒤 다시 호출하고, 상한이면 `ESCALATED(PUBLISH_FAILED)`다. 로그인에서는 글이 생기지 않으므로 그 단계의 타임아웃·5xx도 여기에 든다.
11. 글 생성 호출(`POST /api/v1/posts`)의 결과 불명(응답 전 타임아웃·연결 끊김, 5xx)이면 다시 호출하지 않고 `ESCALATED(PUBLISH_UNCERTAIN)`다. Post BE는 글을 저장한 뒤 조회·응답 변환을 하므로 5xx여도 글이 생겼을 수 있다. 글 목록 API는 카테고리·페이지로만 걸러 이 서비스가 이미 게시됐는지 확인할 수 없다.

`PUBLISH_UNCERTAIN`은 사람이 블로그를 확인해 해소한다. 글이 있으면 `CONFIRM_PUBLISHED(external_post_id)`로 `PUBLISHED`(03 문서 23a행), 없으면 `PUBLISH_AS_IS`로 다시 발행 대기(23행)다. `REVISE`·`REJECT`도 글이 올라가 있지 않은지 확인한 뒤 고른다.

### `CreatePostDto` 매핑

| 필드 | 값 |
|---|---|
| `title` | 최종 버전 `title` (100자 이하, 05 문서 `DraftOutput`) |
| `content` | 최종 버전 `content_markdown` + 요약 노트를 변환한 Tiptap JSON |
| `description` | 최종 버전 `summary` (1~150자) |
| `tags` | `topic.keywords` |
| `topic` | `topic.title` (200자 이하) |
| `mainCategoryId`, `subCategoryId` | `topic.main_category_id`, `topic.sub_category_id`. 쌍 검증은 Post BE도 한다 |
| `language` | `StructureRules.language` (`ko`) |
| `thumbnail` | 보내지 않는다 |

### 마크다운 → Tiptap 변환표

`models/tiptap.py`의 `markdown_to_tiptap`(순수 함수)가 한다. 노드·마크 이름은 블로그 에디터 스키마(`vans_story`의 `EditorExtensions`)를 따른다. 본문은 3절 게이트가 같은 함수로 검사했고, 노트는 넣는 LLM 텍스트를 이스케이프하므로(05 문서 5절) 발행 단계에서는 변환이 실패하지 않는다.

| 마크다운 | Tiptap |
|---|---|
| 문단 | `paragraph`. 문단 안 줄바꿈(soft break)은 공백으로 바꾼다. 뷰어가 `whitespace-pre-wrap`이라 그대로 두면 줄이 갈린다 |
| `#`~`######` | `heading` (`level`) |
| `-`, `1.` 목록 | `bulletList` / `orderedList` → `listItem` → `paragraph` |
| `>` 인용 | `blockquote` |
| 코드 펜스 | `codeBlock` (`language`). `python-snippet`은 `python`으로 바꾼다. snippet 표시는 게이트 전용이다 |
| GFM 표 | `table` → `tableRow` → 첫 행은 `tableHeader`, 나머지는 `tableCell`. 셀 내용은 `paragraph` |
| `---` | `horizontalRule` |
| 줄 끝 `\` 또는 공백 두 칸 | `hardBreak` |
| `**` `*` `~~` `` ` `` | `bold`, `italic`, `strike`, `code` 마크 |
| `[text](url)` | `link` 마크 (`href`) |
| `<!-- sec:id -->` | 지운다 |
| 그 외 HTML, 이미지, 각주, `mermaid` 코드 블록 | 변환 실패 (`ValueError`). 게이트가 `validation_errors`로 바꾼다 |

노트 템플릿은 05 문서 5절. 노트도 이 변환표로 본문과 함께 변환한다.

## 9. 실패 처리 요약

| 실패 | 처리 |
|---|---|
| 벤더 5xx, 429, 타임아웃 | 지수 백오프 재시도 (`MAX_LLM_RETRIES`). 429는 벤더 세마포어도 일시 축소 |
| 출력 스키마 위반 | 같은 호출 재시도. 첫 시도 메시지 뒤에 직전 실패 출력과 오류 안내를 붙인다(6절) |
| 출력이 스키마는 맞지만 불변식 위반(예: 차단 항목에 resolution 없음) | 게이트 실패로 처리 (`REVISING`) |
| 재시도 소진 | 글 `FAILED(LLM_RETRIES_EXHAUSTED)`. 병렬 단계는 다른 호출을 기다려 성공분을 저장한다. 라운드는 계속. 사람이 재개하거나 폐기한다 |
| 글 예산 초과 | 호출하지 않고 글 `FAILED(BUDGET_EXCEEDED)`. 재개 때 `token_limit`을 올릴 수 있다 |
| 예상하지 못한 예외 | 글 `FAILED(INTERNAL_ERROR)`, 주제 선정이면 라운드 `ABORTED(INTERNAL_ERROR)`. 로그가 신호다 |
| 프로세스 재시작 | 마지막 저장 상태부터 이어 간다. 끊긴 단계의 호출만 다시 한다. 스캔마다 도는 정리가 1절 표의 따라 쓰기를 마저 한다 |
| 발행 결과 불명 | 다시 호출하지 않는다. `ESCALATED(PUBLISH_UNCERTAIN)`. 사람이 블로그를 확인해 `CONFIRM_PUBLISHED` 또는 `PUBLISH_AS_IS` (8절) |
| 동시 쓰기 충돌 | `revision` 불일치 → 코루틴은 결과를 버리고 다시 읽는다. 사람·관리자 쓰기는 다시 읽어 전제가 맞으면 재적용하고, 아니면 409 |
| 관리자 중단 | 1절 순서. 진행 중인 호출은 끊지 않고 결과를 버린다. `IN_PROGRESS`에서는 `PUBLISHED` 외 글만 `REJECTED` |
| 서버와 `agents run`을 동시에 띄움 | 지원하지 않는다. 같은 글을 두 프로세스가 돌리면 호출이 중복된다(07 문서 Q-04) |
