# 모델 라우팅·관심사 분리·전문성 기준 검토

2026-11-05 검토 기록이다. 같은 날 실제 발행 글 피드백과 재검증 결과를 반영해 고쳤다. 아직 구현은 시작하지 않았다. 여기 적은 설계는 제안이며, `agents_design/` 01~07 문서의 규칙을 바꾸지 않는다. 구현을 정하면 그때 그 디렉터리의 02·03·04·06·07 문서를 함께 고친다.

- **코드 근거:** 커밋 `9463837` 기준이다.
- **본 것:** 공개 페이지에서 [발행 글](https://vansdevblog.online/post/view/nestjs/6aeb9a729493325088e670f7)의 최종본과 요약 노트를 읽었다.
- **표본:** 카테고리 첫 페이지 기준으로 파이프라인 코드가 생긴 뒤 발행된 글은 이 1편이다. 모델별·페르소나별 차이는 이 글로 말할 수 없다.
- **못 본 것:** 검토한 PC에 `.env` 가 없어 `agent_drafts`·`agent_calls`(v1~v3 본문, 리뷰 내역, 영수증)는 보지 못했다. 아래 원인 분석에서 이 기록이 필요한 부분은 가설로 표시했다. 벤더 API 를 실제로 호출해 확인한 것도 없다.
- **외부 자료:** 2026-11-05에 확인했다. 모델 수치는 금방 바뀐다.

## 이번 판에서 바뀐 결론

실제 글은 읽기 힘든 명세서에 가까웠다. 그래서 다음을 고쳤다.

- **순서:** 모델 라우팅보다 서사·범위 통제를 먼저 한다.
- **300 레벨 기준:** 항목 개수가 아니라 단일 판단의 인과관계를 얼마나 깊이 설명했는지로 본다.
- **외부 자료 상한:** "300 이상은 자료 필수, 없으면 200"을 철회했다. 400 과 `troubleshooter` 에만 적용한다.
- **`alternate`:** 하나짜리 필드를 우선순위 배열 `candidates` 와 충돌 회피 규칙으로 바꾼다.
- **`UNVERIFIABLE`:** 외부 문서로 검증할 수 있는 주장에만 적용한다. 설계 판단은 추출 대상에서 뺀다. 거짓 지적률을 재기 전에는 발행을 막지 않는다.
- **교정 단계:** 과밀 문단을 풀어 쓰는 일은 맡기지 않는다. 무생물 주어와 문장 호흡만 고친다.
- **`tradeoff` 필수 해제:** 100 레벨은 나중이 아니라 라벨 도입 때 함께 푼다.
- **`SPOT_CHECK` 라우트 제거:** 본인 지적을 확인하는 호출이라 FULL 을 맡은 모델을 그대로 쓴다.
- **`persona_id` 분리:** 1단계에서 3단계로 미룬다.

## 재검증에서 바로잡은 것

코드, 발행 글 원문, 벤더 문서를 다시 대조해 고친 내용이다. 이전 판과 피드백의 서술 중 사실과 달랐던 것을 남겨 둔다.

| 이전 서술 | 실제 |
|---|---|
| 글이 1.2만 자 상한에서 나왔고, 상한에 맞추려다 압축됐을 수 있다 | 최종본 마크다운이 최소 12,204자다. 실행 환경의 상한은 12,000 보다 컸고 값은 모른다 |
| Gemini 4 의 temperature 문서를 찾지 못했다 | `gemini-4-argon` 은 temperature 를 무시한다 |
| 리뷰 모델 예시 `max_output_tokens: 8000` | thinking 이 출력 한도를 함께 써서 위험하다. 예시를 고쳤다 |
| 글이 300 기준을 대부분(피드백은 100%) 채웠다 | 전환 순서와 변경 전후 코드만 뚜렷하다. "기각", "대안", "되돌", "롤백"은 본문에 없다 |
| 모델이 기준표를 맞추려다 요약 명세서가 됐다(피드백) | 기준표는 글이 발행된 뒤에 썼고 어떤 프롬프트에도 없다. 깊이 위주로 바꾸는 설계는 예방책이다 |
| 이 글은 논리적으로 완결된 300 레벨 글이다(피드백) | 아니다(0단계 참고). 자료 없이 300 을 쓸 수 있다는 규칙은 유지하지만 이 글은 근거가 아니다 |

## 구현 순서

| 단계 | 내용 | 원래 제안 | 완료 기준 |
|---|---|---|---|
| 0 | 실제 라운드 기록 확인, 서사·범위 규칙, 핵심 메커니즘 전달 검사, 발행 전 전체 읽기, `troubleshooter` 수정, 벤더 단건 호출 | 신규 | 밀도 지표가 줄고, 제목이 약속한 내용을 본문이 보여 주며, 사용자가 읽어 봐도 글로 읽힌다 |
| 1 | 호출별 모델 라우팅과 DB 조회, `candidates`, `effort`, 잘림 처리 | 1번 | API 응답과 golden 변화 없이 호출별 모델이 바뀐다 |
| 2 | 레벨 라벨, 100 레벨 `tradeoff` 해제, 400·`troubleshooter` 자료 규칙, 선택 자료 입력 | 3번 | 사용자 라벨과의 일치율을 잰다 |
| 3 | 팩트체크 도구, TypeScript 구문 검사, "글마다 렌즈 N개" 구조 | 2번 | 오류를 심은 글로 잰 재현율·거짓 지적 |
| 4 | 발행 전 단계에 교정 추가 | 2번 | 안전장치 통과율, 사용자 평가 |

0·2·4단계는 모두 리뷰 통과 뒤 `READY_TO_PUBLISH` 전에 호출을 더한다. 상태 전이는 0단계에서 한 번만 만들고, 뒤 단계는 호출만 더한다("발행 전 단계" 참고).

## 0단계: 서사와 범위 통제

### 실제 글에서 확인한 것

글은 "NestJS 모듈 경계 다시 긋기: 계층형 구조에서 클린 아키텍처로 단계적 리팩토링"이다. 요약 노트에 다음이 적혀 있다.

- 버전 v1 → v4. `claude-opus-5-5` 작성, `gemini-4-argon`·`grok-4.7` 리뷰
- CRITICAL 1, MAJOR 2, MINOR 0, NITPICK 0
- 작성자 수용 3건, 유지 0건

최종본을 재면 다음과 같다. 문장 수는 어미 기준 근사치다.

| 항목 | 값 |
|---|---|
| 본문 렌더링 길이 | 11,475자(앵커·마크다운 기호 제외) |
| 마크다운 길이 하한 | 12,204자(앵커와 기호를 최소로 더한 값) |
| 코드 블록 | 7개(TypeScript 6, JSON 1), 6,631자(58%) |
| 문단 | 25개, 3,380자, 평균 135자 |
| 문단당 문장 | 평균 2.6, 4문장 이상 6개 |
| 문단당 인라인 코드 | 평균 1.7, 4개 이상 5개 |
| 가장 과밀한 문단 | 369자, 6문장, 인라인 코드 10개 |

그 문단 하나에 Unit of Work 포트, `dataSource.transaction()`, `emitAsync()`, 최소 한 번 전달과 멱등, `SKIP LOCKED` 선점, 브로커 교체, 아웃박스 없는 `emit()` 의 용도가 함께 들어 있다. 대사 작업을 설명한 문단도 "재시도조차 오지 않는 경우"를 한 문장으로 처리한다. 왜 그런 문제가 생기는지 설명하기보다 결론을 선언하고 넘어간다는 피드백과 맞는다.

내용을 읽으면 밀도보다 큰 문제가 있다.

- **제목을 지키지 못했다.** 모듈 경계를 다룬다는 글에 `@Module` 이 0회, `exports:` 가 0회다. 변경 후 모듈 구성을 보여 주는 코드가 없고 본문의 중심은 결제 정합성이다.
- **어려운 부분이 사라진다.** 변경 전 `OrderService` 의 쿠폰, 메일, 환불은 변경 후 코드에 나오지 않는다.
- **본문과 코드가 어긋난다.** 본문은 결제 완료가 유실되면 안 된다고 하는데, `confirm-payment.use-case.ts` 는 결제 기록이 없으면 그냥 `return` 한다. 그 기록을 누가 만드는지도 나오지 않는다.
- **꾸며낸 경험담은 없다.** 1인칭 사건과 측정 수치가 없다. 0단계의 `troubleshooter` 수정은 예방 조치다.

### 원인 분석

**확인한 것**

1. **v1 프롬프트에서 나온 글로 보인다.** 게시글 ObjectId 시각은 05:49 KST 고, `v2/write_draft.md` 파일 시각은 09:32, v2 커밋(`aa72c89`)은 09:48 이다. v2 가 금지한 "~십시오"도 본문에 남아 있다. 그래서 v2 프롬프트의 효과는 아직 측정되지 않았다. 영수증의 `prompt_template_version` 으로 최종 확인한다.
2. **실행 환경의 길이 상한은 모른다.** 코드 기본값은 당시 12,000 이었지만, 최종본 마크다운이 최소 12,204자라 그 상한을 통과할 수 없다. 실행 환경의 `DRAFT_MAX_CHARS` 가 더 컸다는 뜻이다. `.env.example` 에는 이 값이 없다.
3. **v4 는 리뷰만으로 나올 수 없다.** `max_iterations=2` 면 리뷰 수정은 v3 까지다(`PostDraft.start_drafting`). 환경값을 바꾸지 않았다면 최소 한 버전은 게이트 실패(GATE)나 사람 재수정(HUMAN)으로 생겼다.
4. **리뷰 렌즈가 범위 확장을 요구한다.** `v2/review_full.md` 의 FACT 렌즈는 "누락된 필수 배경, 다루지 않은 엣지 케이스"를, CRITICAL_PRACTICAL 렌즈는 "실무 제약(비용·운영·팀 규모)"을 본다. 범위를 한정하는 입력이 없다. `TopicForReviewer` 에는 제목, 독자, 키워드, 목차만 있다.
5. **MAJOR 를 기각하면 작성자가 불리하다.** 기각의 타당성은 지적을 낸 리뷰어가 SPOT_CHECK 에서 판정한다. 중재는 CRITICAL 만 받는다(`deadlocked_items`). 리뷰어가 받아들이지 않은 MAJOR 는 상한까지 돌다 `ESCALATED` 로 간다(Q-14). 프롬프트로 방어권만 주면 이 경로가 그대로 남는다.
6. **덧붙이기를 막는 규칙이 없다.** `revise_draft.md` 는 수용과 기각의 형식을 정하고, 고치는 방식은 "분량은 주제를 넓히는 데 쓰지 않는다"는 문장 규칙 하나뿐이다. "한 문장에 새 개념 하나"는 v2 에 있지만 문단 규칙은 없다.
7. **제목 전달을 아무도 보지 않는다.** 렌즈는 오류만 찾는다. 제목이나 핵심 메커니즘을 전달했는지 묻는 항목이 없다.
8. **수정본 전체를 읽는 단계가 없다.** FULL 리뷰는 리뷰를 마친 버전이 없을 때 한 번뿐이고(D-21), 이후 SPOT_CHECK 는 이전 지적과 새 CRITICAL 만 본다(D-09). 수정이 만든 모순과 코드 누락은 걸러지지 않는다.
9. **작성자에게 실제 자료가 없다.** `WriteDraftInput` 은 페르소나, 주제, 구조 규칙뿐이다. 실제 프로젝트의 근거가 글에 들어올 경로가 없다.

**가설(기록으로 확인할 것)**

- **길이 때문에 압축했다.** 근거가 약해졌다. 코드가 58%라 설명에 쓸 자리가 적은 것은 맞지만, 실행 환경의 상한을 모른다. v4 를 만든 게이트 실패가 길이 때문인지도 모른다. 앵커나 해소 ID 오류일 수도 있다.
- **리뷰 수용으로 범위가 넓어졌다.** 차단 지적 3건이 네 묶음(도메인 분리, 결제 멱등성·대사, 아웃박스·`SKIP LOCKED`, 컷오버·ESLint·테스트)을 모두 늘렸는지, v1 에 이미 있었는지는 모른다. `overall_feedback` 도 작성자에게 넘어가므로 함께 본다.

### 먼저 확인할 것

`.env` 가 있는 PC 에서 읽기 전용으로 확인한다. 공개 페이지에는 최종본만 있다.

1. 실행 환경 `.env` 의 `DRAFT_MAX_CHARS`·`MAX_ITERATIONS`·`PROMPT_VERSION`·`DRAFT_TOKEN_LIMIT`·`AGENT_SLOT_BINDINGS` 값을 적어 둔다.
2. `agent_calls.prompt_template_version` 으로 프롬프트 버전을 본다.
3. `agent_drafts.versions[]` 의 `origin_cause`·`validation_errors`·`content_markdown` 길이로 v4 가 생긴 이유를 본다.
4. v1~v4 의 밀도 지표를 재고, 리뷰 항목 3건과 `overall_feedback` 원문을 대조해 네 묶음이 언제 들어왔는지 본다.

| 확인 결과 | 해석 | 먼저 고칠 것 |
|---|---|---|
| v1 부터 과밀하고 범위가 넓다 | 주제·작성 프롬프트 문제 | 주제 범위 제한, 문단 규칙 |
| 리뷰 수용 버전에서 범위가 넓어졌다 | 리뷰·수정 구조 문제 | 리뷰 범위 제한, 작성자 방어권 |
| 길이 때문에 게이트에 떨어진 뒤 압축됐다 | 길이 상한 문제 | 실행 환경 상한과 코드 비중 |

세 경우가 겹칠 수 있다. 아래 규칙은 어느 결과가 나와도 필요하지만, 효과를 판단하려면 이 기준선이 있어야 한다.

### 규칙 변경

| 위치 | 변경 | 강제 방식 |
|---|---|---|
| `TopicProposal`, `propose_topic` | `core_mechanism`(단일 핵심 메커니즘)과 `out_of_scope`(다루지 않을 것)를 추가한다. "모듈 경계와 도메인 분리"와 "아웃박스와 무중단 배포"는 별도 글이다 | 필드는 스키마, 단일 여부는 프롬프트 |
| `TopicForReviewer`, `ArbitrateInput` | 두 필드를 넘긴다 | 코드 |
| `review_full` | CRITICAL·MAJOR 는 `core_mechanism` 안의 논리·사실 결함에 한정한다. 범위 밖 운영 엣지 케이스(멀티 인스턴스 락, 대사 배치, 배포 컷오버 등)를 본문 코드로 구현하라는 요구는 금지한다. 그런 지적은 MINOR 이하다 | 프롬프트, 중재 |
| `review_full` 렌즈 문구 | "다루지 않은 엣지 케이스"는 핵심 메커니즘이 성립하는 데 필요한 것으로, "실무 제약"은 전제와 한계를 밝혔는지로 좁힌다 | 프롬프트 |
| `review_full` | 핵심 메커니즘을 전달하지 못한 글은 MAJOR 다. 제목과 `core_mechanism` 이 약속한 것을 본문과 코드가 실제로 보여 주는지 본다 | 프롬프트 |
| `revise_draft` | 수용할 때 문단 끝에 1~2문장을 덧붙이지 않고 문단을 다시 쓴다. 범위 밖 지적은 본문 로직을 늘리지 않고 `tradeoff` 에 "이 글의 전제와 다루지 않는 범위"로 적어 유지한다 | 프롬프트, 지표 |
| `ItemResolution`, `revise_draft` | 차단 항목마다 범위 안인지(`in_scope`) 먼저 판단해 출력하게 한다. 방어권이 있어도 전부 수용하면 소용없다(이 글은 유지 0건) | 스키마, 지표 |
| `ResolutionStatus`, `deadlocked_items`, `arbitrate` | `OUT_OF_SCOPE` 해소를 추가한다. 차단 항목에만 쓰고 `reason` 이 필수다. 리뷰어가 받아들이지 않으면 MAJOR 도 중재를 1회 받는다(Q-14 변경). 중재 입력에는 범위 선언이 적힌 `tradeoff` 섹션도 넣는다. `UPHOLD_AUTHOR` 면 종결된다 | 코드 |
| 발행 전 단계 | 수정본 전체를 1회 읽는다. 결과는 수정 루프로 보내지 않고 승인 화면에 보여 준다 | 코드 |
| `write_draft`, `revise_draft` | 한 문단에 핵심 개념 하나만 둔다. 키워드 선언으로 넘어가지 않고 문제 맥락, 코드나 해결책, 동작 원리를 산문으로 풀어 쓴다 | 프롬프트, 지표 |
| `services/metrics.py` | 버전별 문단당 문장·인라인 코드 수, 인라인 코드 4개 이상 문단 수, 버전 간 길이 증가를 낸다. 처음에는 차단하지 않는다 | 지표 |
| `personas.py` | `troubleshooter` 의 `tone_markers` 에서 "1인칭 경험담"을 뺀다 | 코드 |

구현할 때 챙길 점은 다음과 같다.

- **프롬프트 버전:** 프롬프트 변경은 새 디렉터리 `agents/prompts/v3` 로 낸다(`prompts/registry.py` 규칙). 2단계에서 다시 올리지 않도록 작성 프롬프트는 `tradeoff` 를 직접 쓰지 않고 `required_sections` 만 가리키게 한다.
- **렌즈 문구 위치:** 실제로 쓰이는 문구는 `review_full.md` 다. `personas.py` 의 `LENS_GUIDES` 는 앱 코드에서 읽는 곳이 없고 `test_personas.py` 만 본다. 함께 정리한다.
- **범위 밖 사실 오류:** 본문에 적은 내용이 틀렸으면 범위와 무관하게 CRITICAL 이다. 범위 제한은 "더 구현하라"는 요구에만 적용한다.
- **`troubleshooter`:** `writer_bias` 의 "장애 대응기"와 "극한 테스트"도 실제 사건이나 실행을 전제한다. 제거만으로 끝나지 않으니 공개 사례 분석으로 바꾸거나 2단계 자료 규칙을 적용한다. 이 값은 `author_tone_markers` 로 리뷰어에게도 넘어간다.
- **요약 노트와 지표:** `_is_rejected` 와 `rejected_with_reason_ratio` 는 `OUT_OF_SCOPE` 도 유지로 세야 한다.
- **저장 문서 호환:** `TopicProposal` 은 LLM 출력과 저장에 같이 쓰인다. 필수 필드로 넣으면 기존 문서를 읽지 못하므로 기본값을 두고, 값이 비었는지는 `topic_proposal_check` 에서 검사한다.

범위를 좁히면 실제 결함이 MINOR 로 밀려 고쳐지지 않을 위험이 있다. MINOR 는 수정 없이 통과하기 때문이다. 그래서 핵심 메커니즘이 성립하는 데 필요한 조건은 차단 대상으로 남기고, 넘어간 지적은 노트에 기록으로 남긴다.

### 발행 전 단계

전체 읽기(0단계), 레벨 판정(2단계), 교정(4단계)은 모두 리뷰 통과 뒤 `READY_TO_PUBLISH` 전에 돈다. 새 상태 하나(예: `POST_REVIEW`)에서 교정, 전체 읽기, 레벨 판정 순으로 돈다. 읽기와 판정이 최종 본문을 보게 하려는 순서다.

- **진입 경로:** `READY_TO_PUBLISH` 로 들어가는 길은 `evaluate`, 사람 재수정의 `pass_validation`, `PUBLISH_AS_IS` 세 가지다. 새 상태는 `evaluate` 경로에만 넣는다. 나머지 둘은 사람이 본문을 정했으므로 바꾸지 않는다(D-17).
- **레벨이 없는 글:** 사람 경로로 발행된 글은 판정이 없어 `level` 이 비어 있다. 표시를 생략한다.
- **호출 실패:** 교정 호출이 재시도를 소진하면 교정을 건너뛴다. 전체 읽기와 판정이 소진하면 지금처럼 `FAILED` 로 멈춘다.
- **전체 읽기 모델:** 작성 벤더가 아닌 모델이다. 1단계 전에는 리뷰어 슬롯의 모델을 쓴다.
- **함께 고칠 곳:** `_EVALUATION_TARGETS`, `EvaluationOutcome._valid_target`, `_step`, `metrics._reached_ready`, 03 문서 전이표. D-09 는 그대로 둔다. 전체 읽기는 수정을 부르지 않기 때문이다.
- **승인을 끌 때:** 전체 읽기 결과는 승인 화면으로 간다. `PUBLISH_REQUIRES_APPROVAL` 을 끄기 전에 이 결과를 어떻게 쓸지 정해야 한다.

### 효과 비교

같은 주제로 새 라운드를 돌리면 `agent_topics` 중복 검사에 걸린다. 저장된 `WRITE_DRAFT` 입력 JSON 을 v1·v2·v3 템플릿으로 보내는 스크립트로 초안을 먼저 비교한다. 조건마다 3회 이상 돌리고, `structure_rules.max_chars` 는 같은 값으로 맞춘다. v3 의 새 필드는 저장된 입력에 없으므로 스크립트가 채운다. 수정 단계의 효과는 새 주제 라운드로 본다. 표본이 이 글 하나뿐이므로 다른 주제와 작성 벤더로도 확인한다. 비교 기준은 위 측정표, 핵심 메커니즘 전달 여부, 사용자의 직접 평가다.

### 벤더 단건 호출

키를 쓰는 작은 호출로 다음을 확인한다. 결과에 따라 1단계 지원표와 3단계 호출 구조가 정해진다.

- 검색과 JSON 스키마 병행: Gemini 4 Argon, Claude, Grok 4.7(Responses API)
- effort 값과 temperature 공존 여부: Claude, Grok 4.7
- 출력 한도가 작을 때의 종료 사유와 본문: 세 벤더 모두

## 1단계: 호출별 모델 선택과 DB 조회

### 현재 코드

호출 단위 기반은 이미 있다.

- `AgentCallService.call` 은 호출마다 `binding` 을 받는다.
- `AgentCall.model_binding` 은 시도마다 모델을 스냅샷으로 남긴다.
- `LLMParams.model_id` 도 호출마다 넘어간다.
- `AgentCallService.replay(call, binding=)` 는 같은 메시지를 다른 모델로 보낼 수 있다.

`slot_bindings` 를 읽는 곳은 다음과 같다.

| 위치 | 용도 | 1단계 처리 |
|---|---|---|
| `draft_pipeline.py` 5곳, `topic_selection.py` 1곳 | LLM 호출 | 라우트 조회로 교체 |
| `publishing.py` 발행 계정(D-45) | 작성자 벤더 | 그대로. `slot_bindings` 를 작성 라우트로 채운다 |
| `publishing.py` `_model` | 노트의 모델명 | 리뷰어 모델을 라우트에서 읽도록 교체 |
| `_display`, `builders.persona_of`, `round.py` 렌즈 계산 | `persona_id` | 그대로 |
| `pipeline_views.py`, `schemas/agent_pipeline.py` | API 응답 | 그대로(아래 경계 참고) |

### 저장 형태

같은 DB(D-43)에 컬렉션을 하나 둔다. 이름과 값은 예시다.

```json
{
  "_id": "REVIEW:FACT_STRUCTURE",
  "candidates": [
    {"provider": "google", "model_id": "gemini-4-argon", "effort": "high", "temperature": null, "max_output_tokens": 16000},
    {"provider": "anthropic", "model_id": "claude-opus-5-5", "effort": "high", "temperature": null, "max_output_tokens": 16000},
    {"provider": "xai", "model_id": "grok-4.7", "effort": "high", "temperature": null, "max_output_tokens": 16000}
  ],
  "note": "구글 모델로 쓴 글은 다음 후보로 넘어간다"
}
```

키는 `PROPOSE_TOPIC:{persona}`, `WRITE_DRAFT:{persona}`, `REVISE_DRAFT:{persona}`, `REVIEW:{lens}`, `ARBITRATE`, `FINAL_READ` 이고 뒤 단계에서 `ASSESS_LEVEL`, `POLISH_FORMAT`, `POLISH_TONE` 이 더해진다.

- **조회 시점:** 라운드 생성 때 한 번 읽어 후보 배열째 스냅샷으로 저장한다. 호출마다 읽으면 진행 중인 글의 모델이 도중에 바뀐다.
- **라우트가 없을 때:** 라운드 생성을 실패시킨다. `AGENT_SLOT_BINDINGS` 로 조용히 돌아가면 설정 누락을 알 수 없다. 초기 데이터를 넣은 뒤 이 설정을 없앤다.

### 후보 선택과 충돌 회피

배열 순서대로 보고 피할 벤더가 아닌 첫 후보를 고른다. 스냅샷과 글 상태만 읽는 순수 함수로 만들어 재시작해도 같은 결과가 나오게 한다.

| 라우트 | 피할 벤더 |
|---|---|
| `PROPOSE_TOPIC`, `WRITE_DRAFT`, `POLISH_FORMAT` | 없음 |
| `REVISE_DRAFT` | 후보를 고르지 않는다. `WRITE_DRAFT` 와 같은 벤더여야 한다. 모델은 달라도 된다 |
| `REVIEW:{lens}` | 작성 벤더, 같은 글에서 먼저 정해진 리뷰 벤더 |
| `SPOT_CHECK` | 라우트 없음. FULL 을 맡은 모델을 그대로 쓴다 |
| `ARBITRATE` | 작성 벤더, 그 항목을 낸 리뷰 벤더 |
| `FINAL_READ`, `ASSESS_LEVEL`, `POLISH_TONE` | 작성 벤더 |

- **충돌 단위:** 벤더다. 같은 회사의 다른 모델은 독립 검증으로 치지 않는다.
- **세 벤더 구조:** 리뷰어 2명과 중재자가 항상 성립하려면 `REVIEW` 와 `ARBITRATE` 후보에 세 벤더가 모두 있어야 한다.
- **사전 검사:** 라운드 생성 때 작성 벤더와 리뷰어의 모든 조합을 계산해 후보가 소진되면 생성을 실패시킨다. 글 도중에 알게 되는 일을 없앤다. API 키가 없는 벤더(`LLMClientRegistry.providers` 에 없는 벤더)는 후보에서 뺀 채 계산한다. 그대로 두면 호출 때 `KeyError` 가 난다.
- **장애 대체 아님:** `candidates` 는 충돌 회피 전용이다. 호출 실패는 지금처럼 재시도 소진 뒤 `FAILED` 다.

### effort 와 temperature

| 벤더 | temperature | effort |
|---|---|---|
| Anthropic | 금지. API `2026-10-21` 은 400 을 돌려줘 지금도 보내지 않는다 | `output_config.effort`. Opus 5.5 기본값은 `medium` 이고 thinking 은 끌 수 없다 |
| Google | `gemini-4-argon` 은 무시한다. 문서는 빼라고 안내한다. Gemini 3 문서는 기본값 1.0 유지를 강하게 권고한다 | `thinkingLevel`. Argon 은 LOW·MEDIUM·HIGH, 기본 MEDIUM. `MINIMAL` 과 `thinking_budget` 은 400 이다 |
| xAI | 허용. 권장값 0.7, 1 초과는 1로 잘린다 | low~xhigh, 기본 high. 끌 수 없다 |

지금 코드의 temperature 는 의도대로 작동하지 않는다. 작성 0.7, 리뷰 0.2 를 정해 두었지만 Claude 에는 보내지 않고 Argon 은 무시한다. 실제로 적용되는 곳은 Grok 뿐이고, 리뷰의 0.2 는 xAI 권장값과 다르다. 영수증에는 적용되지 않은 값이 남는다. effort 는 보내지 않아 Opus 5.5 와 Argon 은 중간, Grok 은 높음으로 돈다. 품질에 어떤 영향을 줬는지는 재지 않았다.

규칙은 다음과 같다.

1. `temperature` 와 `effort` 는 모두 nullable 이고 기본값은 null 이다. null 이면 보내지 않는다. `Settings.review_temperature` 는 없앤다.
2. 벤더·모델 지원표를 코드에 둔다. 지원하지 않는 조합(Anthropic + temperature, Argon + temperature, Argon + `xhigh`, xAI + `max` 등)은 라운드 생성 때 실패시킨다. 클라이언트가 조용히 버리면 영수증과 실제 요청이 달라진다.
3. effort 를 우선한다. 단건 호출에서 두 값의 공존이 거부되는 모델은 지원표에서 temperature 를 금지로 둔다. 공존이 허용되면 적힌 값을 모두 보낸다.

### 출력 한도와 잘림

thinking 은 출력 한도를 함께 쓴다. 한도가 작으면 본문 없이 잘릴 수 있다.

- **Argon:** 출력에 thinking 이 포함되고, thinking 이 한도를 다 쓰면 본문이 빈 채로 올 수 있다. 직접 HTTP 비스트리밍 요청의 단일 출력 한도는 32,768 이다. 이어받기(`CONTINUATION`, `continuation_token`)를 처리하지 않는 지금 클라이언트로는 이 값을 넘기지 않는다. 8,192 에서 본문 없이 끝났다는 포럼 보고도 있다(사용자 보고).
- **Opus 5.5:** thinking 을 끌 수 없다. `stop_reason` 이 `max_tokens` 면 출력이 불완전하다.
- **지금 클라이언트:** `AnthropicClient` 와 `GoogleClient` 는 종료 사유를 보지 않는다. 잘린 응답은 계약 위반 재시도로 처리돼 같은 한도로 되풀이되다 `FAILED` 가 된다.

규칙은 다음과 같다.

1. 세 클라이언트가 종료 사유를 `LLMResponse` 에 담는다.
2. 잘림은 계약 위반과 따로 기록하고 같은 한도로 재시도하지 않는다.
3. effort 를 올리면 출력 한도와 `review_timeout_s`·`draft_timeout_s` 도 함께 잰다. 값은 단건 호출과 실제 라운드로 정한다.

### 1단계와 3단계의 경계

**1단계는 리뷰어 2명과 슬롯 구조를 유지하고 모델 조회만 바꾼다. "렌즈 N개, 렌즈마다 모델 1개"는 3단계에서 한 번에 바꾼다.** N 렌즈는 팩트체크 계약이 정해져야 모양이 나오기 때문이다.

- **응답과 golden:** 1단계에서는 `RoundPublic.slot_bindings` 와 `reviewer_assignments` 의 형태를 유지한다. 스냅샷은 `PublishingRound` 에만 두고 응답에 내보내지 않는다. 그래서 `pipeline_views.py` 와 `agents_round_detail.json` 은 바뀌지 않는다. `agents_calls.json` 은 `provider`·`model_id` 만 내보내 영향이 없다.
- **`slot_bindings` 의 의미:** 슬롯의 페르소나와 작성 모델이다. 라운드 생성 때 작성 라우트로 채운다. `publishing.py` 의 계정 선택과 `_display` 는 그대로 동작한다.
- **`effort` 필드:** `ModelBinding` 에 직접 넣으면 응답이 바뀐다. 호출과 영수증용 타입을 따로 둔다.
- **3단계에서 한 번에 바꿀 것:** `persona_id` 분리, `reviewer_assignments`, `publishing.py` 의 리뷰어 2명 가정, `_LENS_LABELS`, `pipeline_views.py`, `agents_round_detail.json`.

### 요약 노트의 모델 표기

**본문에 영향을 준 모델만 적는다.**

| 역할 | 표기 |
|---|---|
| 작성, 리뷰 | 항상 |
| 수정 | 작성 모델과 다를 때만 |
| 중재 | 중재가 있었을 때만 |
| 교정 | 패치가 실제로 적용됐을 때만 |
| 전체 읽기, 레벨 판정 | 적지 않는다. 본문을 바꾸지 않으며 `agent_calls` 에 남는다 |

실제로 선택된 후보를 적는다. 1단계에서 영향을 받는 테스트는 `test_publishing.py` 의 노트 문구 검사다.

## 2단계: 글의 전문성 기준

### 현재 코드

- 수준을 나타내는 필드는 자유 문구인 `TopicProposal.audience` 뿐이다. Post BE `Post` 스키마에는 없다.
- 주제 제안 프롬프트와 리뷰 렌즈에 깊이 기준이 없다.
- 게이트 규칙은 모든 글에 같다(길이 범위, 코드 블록 1개 이상, `REQUIRED_SECTIONS`). 입문 글에도 `tradeoff` 가 필수라 겉모양이 비슷해진다.

### 기준표

틀은 AWS 세션 레벨 100~400 에서 빌렸고 세부 기준은 이 검토의 초안이다.

| | 100 입문 | 200 실무 기초 | 300 심화 | 400 전문 |
|---|---|---|---|---|
| 핵심 질문 | 어떻게 쓰나 | 제대로 쓰려면 | 이 판단은 왜 이렇게 되고, 코드가 어떻게 달라지나 | 왜 그렇게 동작하고 어디서 깨지나 |
| 전제 지식 | 없음 | 기본 사용 경험 | 여러 방식을 직접 써 봄 | 내부 동작과 운영 경험 |
| 판정 기준 | 절차를 따라 하면 동작한다 | 방법과 이유를 함께 설명한다 | 선택한 단일 전환이나 설계 판단의 인과관계를 코드 변경 전후로 깊이 설명한다 | 내부 동작이나 실패 원인을 실측으로 설명한다 |
| 근거 | 공식 문서 | 실행되는 코드 | 변경 전후 코드와 테스트. 표준 시나리오도 된다 | 실측 수치, 운영 기록. 외부 자료 필수 |

**항목 개수는 점수에 넣지 않는다.** 이전 판은 300 기준으로 기각한 대안, 전환 순서, 되돌리는 비용을 나열했다. 발행 글이 이 표를 보고 쓰인 것은 아니다. 표는 글이 나온 뒤에 썼고 어떤 프롬프트에도 없다. 다만 프롬프트나 판정에 목록을 넣으면 한 줄씩 채우고 통과하는 글을 부를 수 있어 미리 막는다. 이제 이들은 인과 설명에 나올 수 있는 예시일 뿐이다. 판정자는 깊이를 보여 주는 문단을 인용해야 하고, 여러 주제를 한 줄씩 훑은 글은 300 이 아니다.

두 예시에 적용하면 다음과 같다.

- "Nest 모듈 만들어보기"는 100 이다. 공식 문서의 절차로 대체할 수 있다.
- "계층형 구조에서 클린 아키텍처로 단계적 리팩토링"은 300 을 목표로 삼을 수 있는 주제다. 다만 실제 발행본에는 이 기준으로 300 을 주기 어렵다. 모듈 구성 코드가 없고, 변경 전 코드의 어려운 부분이 변경 후에 사라지며, 일부 문단은 결론 선언에 그친다. 최종 레벨은 사용자 라벨의 첫 사례로 정한다.

### 적용

1. **주제:** `TopicProposal` 에 목표 레벨과 전제 지식을 더한다. 범위 필드는 0단계에서 이미 들어온다.
2. **판정:** 발행 전 단계에서 작성 벤더가 아닌 모델이 판정한다. 목표 레벨은 입력에 넣지 않고 기준별로 본문을 인용하게 한다. 모델이 자기 글을 높게 매길 수 있어 작성자 신고값은 라벨로 쓰지 않는다.
3. **구조 규칙:** 100 레벨은 `tradeoff` 를 필수에서 뺀다. 라벨과 함께 넣는다. 억지 트레이드오프가 남으면 판정과 일치율 검증이 왜곡된다.
4. **표시:** 판정값을 `level` 로 발행한다.
5. **검증:** 사용자가 직접 라벨을 단 글 10~20편과 모델 판정의 일치율을 잰다. 기준표와 기준 글은 DB 에 버전을 붙여 두고 라운드에 스냅샷으로 저장한다. 카테고리 첫 페이지 기준 공개 글은 31편이고, 제목을 확인한 글은 대부분 입문형이며, 파이프라인 글은 1편이다. 그래서 처음에는 100·200 과 300 이상을 가르는지만 보고, 300·400 구분은 기준 글이 쌓인 뒤에 잰다.
6. **차단(나중):** 일치율을 확인한 뒤 목표보다 낮은 글을 막을지 검토한다.

구조 규칙에서 고칠 곳은 다음과 같다.

- `validation.py` 의 `_check_anchors` 는 전역 `REQUIRED_SECTIONS` 를 읽는다. 글의 목표 레벨로 필수 섹션을 고르게 바꾼다. 레벨이 없는 기존 글은 현행 규칙을 쓴다.
- `StructureRules.required_sections` 의 기본값과 `test_builders.py` 의 기대값도 같이 바뀐다.
- `tradeoff` 가 없는 100 레벨에서 범위 밖 선언은 `conclusion` 에 둔다.

### 외부 자료 규칙

자료는 git 이력, `code-graph context` 출력, 실측 기록 같은 입력이다.

- **300 이하:** 자료가 없어도 쓸 수 있다. 표준 도메인 시나리오와 변경 전후 코드, 단위 테스트로 논리가 닫히면 충분하다. 발행 글은 이 조건을 채운 사례가 아니므로 실제로 가능한지는 새 글로 확인한다.
- **400:** 자료가 필수다. 없으면 목표 레벨로 400 을 고를 수 없다.
- **`troubleshooter`:** 실제 사건을 전제한 글은 자료가 필수다. 없으면 공개 사례 분석만 쓰고 상한은 200 이다. 이 값은 사용자 확인이 필요하다.
- **선택 입력:** 자료가 있으면 레벨과 무관하게 넣을 수 있도록 `WriteDraftInput` 에 선택 필드를 둔다. 필수 범위는 넓히지 않는다. 지금은 실제 프로젝트의 근거가 글에 들어올 경로가 없어서, 이 입력의 효과가 가장 클 것으로 본다.

지어낸 경험담은 웹 검색 팩트체크로 잡기 어렵다. 그래서 입력 단계에서 막는다.

### 표시할 때 주의할 점

- Post BE 는 `forbidNonWhitelisted: true` 라 DTO 에 없는 필드를 거부한다. 배포 순서는 vans_story_be_post, vans_story, vans_devblog_fastapi 다. 기존 글이 있으니 선택 필드로 둔다.
- `PostCard` 의 `renderBadge` 는 `PostList` 가 카테고리 아이콘으로 이미 쓰고 있다. 레벨 표시는 따로 추가한다.
- 목록 필터와 검색 인덱스 반영은 나중에 정한다.

## 3단계: 팩트체크, 코드 검증, 렌즈 구조

### 현재 구조의 문제

- **출처가 없다:** `LLMClient.complete` 에 도구 파라미터가 없어 리뷰어는 기억으로만 판단한다.
- **확인 불가가 무시된다:** `checklist.unverifiable` 은 저장만 된다. `post_draft.py` 의 검증기가 비었는지만 보고, 판정과 발행은 읽지 않는다.
- **링크를 확인하지 않는다:** `_warn_external_links` 는 http(s) 스킴만 본다.
- **렌즈가 전담이 아니다:** `build_review_assignments` 는 작성자 렌즈를 다른 리뷰어의 보조 렌즈로 붙인다. FACT 담당 페르소나가 쓴 글의 FACT 는 전담 리뷰를 받지 못한다.

### 추출 대상 한정

글을 주장 단위로 나눠 판정한다(SAFE 방식). 대상은 **외부 문서로 진위를 가릴 수 있는 것**으로 한정한다.

| 대상 | 예(실제 글) |
|---|---|
| 프레임워크·라이브러리 API | `emitAsync()` 의 `suppressErrors` 동작 |
| 설정 옵션명 | `import/no-restricted-paths` |
| 버전 | 특정 버전에서 동작이 바뀐다는 서술 |
| DB 문법 | `SELECT ... FOR UPDATE SKIP LOCKED` |
| 프로토콜, 수치 | 전달 보장, 벤치마크 수치 |

**추출하지 않는 것:** 설계 판단("결제에서 주문으로 가는 호출은 통지다"), 본문 예제 코드의 인과 설명, 의견과 권장. 이들은 외부 URL 이 존재할 수 없다. 전부 MAJOR 가 되면 글의 핵심 논리가 지워지거나 수정 루프에 갇힌다. 이런 내용은 LOGIC 렌즈가 본다.

스키마에서 `claim_type` 을 위 종류의 enum 으로 강제한다. 출력은 `{section_id, claim_type, claim, status, source_url, note}` 다.

심각도는 코드가 정한다(D-03 과 같은 방식).

| 판정 | 처리 |
|---|---|
| `REFUTED` | CRITICAL |
| `UNVERIFIABLE` | 처음에는 MINOR 로 기록하고 승인 화면에 보여 준다. 거짓 지적률을 확인한 뒤 MAJOR 로 올린다 |
| `SUPPORTED` + URL 없음 | `UNVERIFIABLE` 로 본다 |
| `SUPPORTED` + URL 있음 | 지적 없음 |

처음부터 MAJOR 로 막으면 `max_iterations=2` 에서 검색이 찾지 못한 참인 주장이 삭제되거나 `ESCALATED` 로 간다. 작성자에게는 검색 도구가 없어 근거를 대기도 어렵다.

검색 도구 자체가 실패하면 호출 실패로 처리한다. `UNVERIFIABLE` 로 세면 장애가 전부 지적으로 바뀐다.

버전이 바뀌면 다음을 챙긴다.

- **새 주장:** SPOT_CHECK 는 새 CRITICAL 만 보므로, 팩트체크가 FULL 에서만 돌면 v2 이후 추가된 주장은 검증되지 않는다. 수정으로 바뀐 섹션은 다시 추출해 판정한다.
- **중재:** `REFUTED` 분쟁을 판정하는 중재자에게도 검색을 준다. 지금 중재자는 기억으로만 판단한다.

### 웹 검색과 JSON 스키마

| 벤더 | 문서 내용 | 확인하거나 바꿀 것 |
|---|---|---|
| Gemini | 구조화 출력과 Google Search 병행 가능(Preview) | 문서는 "Gemini 3 series 한정"이라면서 도구 예제는 `gemini-4-argon` 이다. Argon 개발자 가이드는 이 조합을 다루지 않는다 |
| Grok 4.7 | Responses API 에서 `web_search` + `json_schema` 예제 있음 | `clients/llm/xai.py` 의 `/chat/completions` 는 xAI 문서에서 legacy 다. Responses API 로 옮겨야 한다 |
| Claude | `web_search` 있음($10/1,000회). citations 가 항상 켜지고, citations 와 `output_config.format` 을 함께 쓰면 400 이다 | 두 내용을 합치면 400 일 것으로 보인다. 이 조합을 직접 다룬 문서는 찾지 못해 추론이다 |

단건 호출에서 병행이 안 되는 벤더는 호출을 둘로 나눈다.

1. **검증:** 웹 검색으로 팩트를 검증한다. 일반 텍스트와 citations 로 받는다.
2. **변환:** 경량 모델(Haiku 5.5 등)이 그 결과를 JSON 스키마로 옮긴다.

변환 모델이 URL 을 지어낼 수 있으므로 코드로 막는다. JSON 의 `source_url` 은 1단계 citations 에 있어야 하고, 없으면 `UNVERIFIABLE` 로 본다. 이를 위해 `LLMResponse` 에 citations 를 더하고 영수증은 2건 남긴다. Anthropic 문서의 "structured data with citations" 방식도 후보로 확인한다.

링크 응답 확인은 리뷰 전 단계로 넣고 결과를 팩트체크 입력으로 준다. 네트워크 결과는 흔들리므로 결정론적 게이트에는 넣지 않는다.

검색을 붙일 때 클라이언트에서 챙길 점은 다음과 같다.

- **이어 보내기:** Claude 의 긴 검색은 `pause_turn` 으로 끊겨 돌아올 수 있어 이어 보내는 루프가 필요하다.
- **응답 파싱:** Claude 응답에는 검색 전 문장과 인용 문장이 별도 text 블록으로 온다. `AnthropicClient` 는 이를 이어 붙이므로 그대로는 JSON 으로 읽을 수 없다. 2단계 호출이 필요한 또 다른 이유다.
- **토큰과 시간:** 검색 결과는 입력 토큰으로 잡힌다. `_check_budget` 추정에는 들어가지 않아 글 상한(`draft_token_limit`)을 예상보다 빨리 쓴다. `review_timeout_s=300` 도 함께 본다.

### 코드 검증

발행 글의 코드 블록 7개 중 6개가 TypeScript 인데 게이트는 python 과 json 만 검사한다(Q-06). Q-06 은 subprocess 와 이미지 크기를 걱정했지만, 코드 그래프가 쓰는 `tree-sitter` 가 이미 의존성에 있어 구문 검사는 subprocess 없이 된다.

메서드만 떼어 낸 조각은 구문 오류로 잡힌다. `python-snippet` 과 같은 방식으로 `typescript-snippet` 을 두고 검사에서 뺀다(`tiptap.py` 는 `-snippet` 접미사를 언어와 무관하게 떼어 낸다). 작성 프롬프트에 태그 규칙을 넣는다. 구문 검사는 타입 오류와 논리 오류를 잡지 못한다. 코드와 본문의 어긋남은 전체 읽기의 몫이다.

### 렌즈 구조

"글마다 렌즈 N개, 렌즈마다 모델 1개"로 바꾼다. 중재자 규칙은 이미 1단계 라우트 규칙으로 옮겨 있으므로, 여기서는 배정, 요약 노트, 응답, golden 을 함께 바꾼다.

## 4단계: 문체·어투·문단 정리

### 역할

리뷰 렌즈로 넣으면 작동하지 않는다.

- 톤 보존 규칙이 어조와 문장 리듬 지적을 금지한다(`contracts.py` `must_not_flag`).
- 표현 지적은 MINOR 이고, MINOR 만 남으면 수정 없이 발행한다(`evaluation.py` `evaluate`).
- D-47 이 같은 안을 이미 기각했다.

그래서 교정은 0단계에서 만든 발행 전 단계 안에서 돈다. **교정은 압축된 서사를 풀지 못한다.** 아래 안전장치는 의미 왜곡을 막는 대신 새 설명을 넣는 것도 막는다. 정보 밀도는 0단계 규칙의 몫이고, 교정은 다듬기만 맡는다.

문법만 고치라고 해도 LLM 이 의미를 바꿨다는 연구가 있다(에세이 대상, 코드 포함 기술 글은 아님). 소규모 논문 교정 비교에서 AI 수정은 61% 개선·14% 악화, 사람은 90% 개선이었다(논문 2편, 8문단·표 2개). 글 전체를 다시 쓰게 하지 않는 이유다.

| 작업 | 담당 | 안전장치 |
|---|---|---|
| "~하십시오", "~라고 볼 수 있다", "~인 상황이다", 문단 길이 | 정규식 | LLM 을 쓰지 않는다 |
| 줄바꿈·문단 정리 | 가장 싼 모델 | 줄바꿈 문자만 달라야 하고 코드 블록은 바이트까지 같아야 적용한다 |
| 무생물 주어 교정, 문장 호흡 분리 | 작성 벤더가 아닌 모델 | 문단별 패치만 받는다. 코드 블록·인라인 코드·숫자·URL·앵커와 부정 표현 수가 전후 같을 때만 적용한다 |

줄바꿈 검사에서 공백 전체를 빼면 안 된다. 띄어쓰기가 바뀌면 한국어 문장의 뜻이 달라질 수 있고, 인라인 코드와 코드 들여쓰기도 깨진다.

토큰 검사는 의미를 보장하지 못한다. "막지 않는다"가 "막는다"로 바뀌어도 코드와 숫자는 그대로다. 그래서 부정 표현(않, 못, 없)의 수를 함께 비교하고 승인 화면에 교정 전후 차이를 보여 준다. 그래도 남는 의미 변화는 사람 승인이 맡는다. 승인을 끄면 이 안전장치가 사라지므로 그 전에 다시 판단한다.

한 모델이 세 페르소나의 글을 모두 고치면 문체가 비슷해질 수 있다. `tone_markers` 를 함께 넘기고, 규칙을 어긴 문단만 고치게 해서 줄인다. 페르소나별 차이가 유지되는지는 실제 결과로 확인한다.

### 게이트 복구 규칙

게이트의 마크다운 검사(`_check_anchors`, `_check_code_blocks`, `_check_length`, `_check_convertible`)는 입출력이 없는 함수라 패치마다 돌릴 수 있다.

1. 패치를 문단 순서대로 하나씩 적용한다. 안전장치나 게이트 검사를 깨는 패치는 그 패치만 버리고 다음으로 간다.
2. 남은 패치를 합친 결과에 전체 `ValidationGate.validate` 를 돌린다. 실패하면 교정본 전체를 버리고 리뷰 통과본을 발행본으로 쓴다.
3. 적용된 패치가 없으면 새 버전을 만들지 않는다.
4. 어느 경우에도 `REVISING` 으로 돌아가지 않고 LLM 을 다시 부르지 않는다. 교정 실패로 글을 멈추지 않는다.
5. 버린 패치 수와 이유를 남겨 교정 품질 지표로 쓴다.

교정본은 새 버전이지만 재리뷰는 하지 않는다. 사람 승인(D-17)은 교정본에 대해 받는다. 문단 길이 게이트는 지금 없으며, 넣으면 같은 규칙을 따른다.

기존 전이는 쓰지 못한다. `submit_version` 은 `DRAFTING` 에서만 불린다. `pass_validation` 은 리뷰를 마친 글을 SPOT_CHECK 로 보내고 미결 항목이 없으면 예외를 던지며, `fail_validation` 은 `REVISING` 으로 간다. 교정본은 검사를 마친 뒤 통과 상태로 붙이는 전용 메서드를 둔다. `_is_fast_track`("v1 이 그대로 발행본")과 노트의 "v1 → vN" 은 교정 버전을 세지 않게 고친다.

## 모델 조사 결과

찾은 자료로는 후보만 고를 수 있다.

| 관심사 | 1차 후보 | 근거 | 신뢰도 |
|---|---|---|---|
| 팩트체크 | Gemini 4 Argon + 검색 | AA-Omniscience v1.1 환각률 4%(High) | 중간 |
| Gemini 글의 팩트체크 | Opus 5.5 + 검색 | 같은 벤치마크 정답률 66%(Xhigh), 순점수 48(Max) | 중간 |
| 어투 | Claude | 한국어 정량 벤치마크를 찾지 못했다. 개인 연구 노트와 영어 블라인드 테스트(Claude 4.8, ChatGPT 4.6, Gemini 4.1)뿐이다 | 낮음 |
| 줄바꿈 | Haiku 5.5 | 입력 $0.10, 출력 $0.50(100만 토큰당). 코드 검사를 통과해야 적용되므로 실패해도 원문이 남는다 | 높음 |
| Grok 4.7 | 보류 | AA 페이지에 수치가 없다. 글쓰기 평가는 출처마다 크게 다르다 | 미정 |

읽을 때 주의할 점은 다음과 같다.

- AA 의 환각률은 `오답 / (오답 + 부분 정답 + 미응답)` 이다. 전체 오답률이 아니라 모를 때 아는 척하는 경향에 가깝다.
- AA 는 일반 지식 시험이라 한국어 기술 글의 오류를 찾는 능력과 다르다. 분야에 소프트웨어 공학이 있지만 분야별 수치는 보지 못했다.
- effort 에 따라 수치가 달라진다. Gemini 4 Carbon 은 High 가 환각률 11%·순점수 54, Medium 이 4%·47 이다. Carbon 은 구글 모델 목록 페이지에서 확인되지 않아 후보에서 뺐다.
- 보조 집계 사이트의 수치는 원 페이지에서 확인하지 못했다. Argon 환각률 15.1%, Grok 4.7 정답률 47.4%·환각률 29.3%, Opus 5.5 정답률 66.2%·환각률 58.6% 다. Opus 5.5 수치는 검색 요약으로만 봤다.

순위표 대신 우리 작업으로 잰다. `replay(binding=)` 를 쓰되 글을 바꿔 넣는 작은 스크립트만 더한다.

- **팩트체크:** 실제 글 5편에 틀린 API, 버전, 없는 옵션을 10개씩 심는다. 모델·effort·검색 조합별로 잡은 수(재현율)와 없는 오류를 지어낸 수(거짓 지적)를 잰다. 작성자 수용률은 정답 여부가 아니므로 지표로 쓰지 않는다. 실제 글에서 작성자는 3건을 모두 수용했지만, 그것이 지적이 옳았다는 뜻은 아니다.
- **어투:** 같은 문단 20개를 모델별로 고치게 하고, 모델명을 가린 채 사용자가 평가한다. 안전장치 통과율도 함께 본다.
- **비용(추정):** Opus 5.5 기준 리뷰 1회 약 $0.1~0.3, 30회면 $3~9 다. 입력 12k, 출력 3~10k 토큰을 가정했고 검색비는 별도다.
- **운영 지표:** `compute_metrics` 는 호출 종류·슬롯별로만 집계한다. 라우팅 뒤에는 모델·렌즈별 집계가 필요하다.

## 실제 호출·기록으로 확인할 것

- [ ] 실행 환경 `.env` 의 길이 상한, 반복 상한, 프롬프트 버전, 토큰 상한, 슬롯 바인딩
- [ ] 발행 글의 프롬프트 버전, 버전별 `origin_cause`·`validation_errors`·길이, 리뷰 항목과 `overall_feedback`
- [ ] 같은 라운드의 나머지 두 글이 어떻게 끝났는지
- [ ] Gemini 4 Argon: Google Search + `responseJsonSchema` 병행
- [ ] Claude: `web_search` + `output_config.format` 병행, 응답의 text 블록 구성, `pause_turn`
- [ ] Grok 4.7: Responses API 로 옮긴 뒤 `web_search` + `json_schema` 병행
- [ ] Claude·Grok 의 effort 값과 temperature 공존 여부
- [ ] 출력 한도가 작을 때 세 벤더의 종료 사유와 본문

## 사용자가 정할 것

| 항목 | 권장 |
|---|---|
| Q-14 변경: `OUT_OF_SCOPE` 로 다툰 MAJOR 도 중재 1회 | 변경. 프롬프트만 바꾸면 기각이 `ESCALATED` 로 이어진다 |
| 발행 전 전체 읽기 도입 | 도입. 결과는 승인 화면으로만 보낸다 |
| `UNVERIFIABLE` 초기 처리 | 비차단. 거짓 지적률 확인 뒤 MAJOR |
| 자료 선택 입력 | 2단계에 함께 둔다. 필수 범위는 400·`troubleshooter` 그대로 |
| 낮은 레벨 글을 막을지, 라벨만 붙일지 | 라벨 먼저. 일치율 확인 뒤 차단 검토 |
| `troubleshooter` 자료 없을 때 상한 | 200. 실제 사건을 전제한 글은 쓰지 않는다 |
| 실제 발행 글의 레벨 | 사용자 라벨로 정해 기준 글 첫 사례로 쓴다 |
| A/B 덮어쓰기(`body.bindings`) 유지 | 구현 때 사용처를 보고 결정 |
| 벤더별 발행 계정(D-45) 유지 | 유지. 작성 벤더 기준 |
| 렌즈 구성 유지 또는 재편 | 3단계 측정 결과를 보고 결정 |

## 출처

- [AA-Omniscience v1.1](https://artificialanalysis.ai/evaluations/omniscience)
- [Gemini structured outputs](https://aistudio.google.com/docs/structured-output), [Gemini models](https://aistudio.google.com/docs/models), [Gemini 3 developer guide](https://ai.google.dev/gemini-api/docs/gemini-3), [Gemini 4 Argon developer guide](https://docs.cloud.google.com/gemini-enterprise-agent-platform/models/guides/gemini-4-argon), [Argon 잘림 포럼 보고](https://discuss.ai.google.dev/t/gemini-4-argon-thought-only-max-tokens-with-preserved-signatures-including-thinkinglevel-low/198148)
- [xAI structured outputs](https://docs.x.ai/developers/model-capabilities/text/structured-outputs), [xAI web search](https://docs.x.ai/developers/tools/web-search), [xAI reasoning](https://docs.x.ai/developers/model-capabilities/text/reasoning), [xAI best practices](https://docs.x.ai/developers/model-capabilities/text/best-practices), [xAI Responses](https://docs.x.ai/developers/rest-api-reference/inference/responses), [xAI Chat Completions(legacy)](https://docs.x.ai/developers/model-capabilities/legacy/chat-completions), [Grok 4.7 guide](https://docs.x.ai/developers/grok-4-7)
- [Claude structured outputs](https://platform.claude.com/docs/en/build-with-claude/structured-outputs), [Claude citations](https://platform.claude.com/docs/en/build-with-claude/citations), [Claude web search](https://platform.claude.com/docs/en/agents-and-tools/tool-use/web-search-tool)
- [How LLMs Distort Our Written Language](https://arxiv.org/abs/2603.18161), [AI 교정 비교 기사](https://www.news-medical.net/news/20260210/AI-editing-tools-make-more-corrections-but-reduce-writing-quality.aspx), [SAFE](https://arxiv.org/abs/2403.18802)
- [언어별 LLM 글쓰기 노트](https://peterkaminski.ai/research/llm-writing-quality-by-language/), [LLM Guides 블라인드 테스트](https://llmguides.ai/lab/llm-writing-quality-test/), [BenchLM 글쓰기 순위](https://benchlm.ai/best/writing), [보조 집계: BenchLM 환각률](https://benchlm.ai/benchmarks/omnisciencehallucinationrate)
- [AWS 세션 레벨 안내](https://aws.amazon.com/events/summits/asean/online/agenda)
