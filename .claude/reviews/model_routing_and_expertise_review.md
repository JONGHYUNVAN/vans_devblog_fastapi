# 모델 라우팅·관심사 분리·전문성 기준 검토

2026-11-05 검토 기록이다. 아직 결정 전이고 코드는 바꾸지 않았다. 여기 적은 설계는 제안이며, `agents_design/` 01~07 문서의 규칙을 바꾸지 않는다. 구현을 정하면 그때 그 디렉터리의 02·03·04·06·07 문서를 함께 고친다.

- **코드 근거:** 커밋 `aa72c89` 기준이다.
- **못 본 것:** 검토한 PC에 `.env` 가 없어 실제 라운드 결과(`agent_drafts`·`agent_calls`, 발행 글)를 보지 못했다. 아래 문제가 실제 글에서 얼마나 나오는지는 확인하지 않았다.
- **외부 자료:** 2026-11-05에 확인했다. 모델 수치는 금방 바뀐다.

## 제안과 권장 순서

| 번호 | 사용자 제안 | 검토 결과 |
|---|---|---|
| 1 | 호출마다 모델을 고르고, 작업별 모델은 내 DB에서 조회한다 | 그대로 진행한다. 변경이 작고 2번의 전제다 |
| 2 | 모델마다 관심사를 나눈다(팩트체크 전담, 문체 전담 등). 누가 뭘 잘하는지 조사한다 | 방향은 맞다. 다만 팩트체크는 도구, 문체는 단계부터 고쳐야 한다 |
| 3 | 글 자체에 전문성 기준을 둔다. "계층형 구조에서 클린 아키텍처로 단계적 리팩토링"과 "Nest 모듈 만들어보기"의 차이를 보여줄 방법이 없다 | 글 품질에 효과가 가장 크다. 먼저 라벨로 시작한다 |

권장 순서는 1번, 3번, 2번 팩트체크, 2번 문체다.

## 1. 호출별 모델 선택과 DB 조회

### 현재 코드

호출 단위 기반은 이미 있다.

- `AgentCallService.call` 은 호출마다 `binding` 을 받는다.
- `AgentCall.model_binding` 은 시도마다 모델을 스냅샷으로 남긴다.
- `LLMParams.model_id` 도 호출마다 넘어간다.
- `AgentCallService.replay(call, binding=)` 는 같은 메시지를 다른 모델로 보낼 수 있다.

`ModelBinding` 은 슬롯 하나에 벤더·모델·페르소나를 함께 묶는다. `slot_bindings` 를 읽는 곳은 다음과 같다.

| 위치 | 용도 | 라우팅 도입 시 |
|---|---|---|
| `services/draft_pipeline.py` 5곳(작성·수정·FULL·SPOT_CHECK·중재) | LLM 호출 | 라우트 조회로 교체 |
| `services/topic_selection.py` | LLM 호출 | 라우트 조회로 교체 |
| `services/publishing.py` 발행 계정(D-45) | 작성자 벤더 | `WRITE_DRAFT` 라우트의 벤더로 교체 |
| `services/publishing.py` `_model` | 요약 노트의 모델명 | 슬롯 모델이 실제와 달라지므로 교체 |
| `publishing.py` `_display`, `builders.persona_of`, `round.py` 렌즈 계산 | `persona_id` | 슬롯 → 페르소나로 유지 |
| `pipeline_views.py`, `schemas/agent_pipeline.py` | API 응답 | 응답 형태와 golden 파일 갱신 |

입력은 `routers/agent_rounds.py` 의 `body.bindings or settings.agent_slot_bindings` 다.

### 제안

같은 DB(D-43)에 컬렉션을 하나 둔다. 이름과 값은 예시다.

```json
{
  "_id": "REVIEW:FACT_STRUCTURE",
  "provider": "google",
  "model_id": "gemini-4-argon",
  "effort": "high",
  "temperature": 0.2,
  "max_output_tokens": 8000,
  "alternate": {"provider": "anthropic", "model_id": "claude-opus-5-5", "effort": "high"},
  "note": "작성 모델이 같은 벤더면 alternate 를 쓴다"
}
```

키 후보는 `PROPOSE_TOPIC:{persona}`, `WRITE_DRAFT:{persona}`, `REVISE_DRAFT:{persona}`, `REVIEW:{lens}`, `SPOT_CHECK:{lens}`, `ARBITRATE` 다. `CallKind.REVIEW` 는 FULL 과 SPOT_CHECK 를 함께 쓰므로 키에서 나눈다.

정할 규칙은 다음과 같다.

1. **조회 시점:** 라운드 생성 때 한 번 읽어 라운드에 스냅샷으로 저장한다. `slot_bindings` 와 같은 방식이다. 호출마다 읽으면 진행 중인 글의 모델이 도중에 바뀐다. DB 변경은 다음 라운드부터 적용된다.
2. **라우트가 없을 때:** 라운드 생성을 실패시킨다. `AGENT_SLOT_BINDINGS` 로 조용히 돌아가면 설정 누락을 알 수 없다. 초기 데이터를 넣은 뒤 이 설정을 없앤다. 요청 본문의 A/B 덮어쓰기(`body.bindings`)를 유지할지는 구현 때 정한다.
3. **모델과 페르소나 분리:** `ModelBinding` 에서 `persona_id` 를 빼고 슬롯 → 페르소나, 라우트 → 모델로 나눈다. D-02 의 3분리 취지와 맞는다.
4. **effort 추가:** `LLMParams` 에 넣고 벤더 클라이언트가 변환한다. 지금 `AnthropicClient` 는 effort 를 보내지 않아 Opus 5.5 는 기본값 `medium` 으로 돈다. Opus 5.5 는 thinking 을 끌 수 없어 effort 가 유일한 조절 수단이다. Grok 4.7 은 low~xhigh 를 받는다. Gemini 쪽 파라미터는 확인하지 못했다.
5. **작성자와 검증자 분리:** 슬롯마다 벤더가 다르면 지금은 자기 글을 같은 모델이 리뷰할 일이 없다. 라우트로 풀면 깨질 수 있으므로 라운드 생성 때 검사하고, 같으면 `alternate` 를 쓴다.
6. **발행 표시:** 발행 계정은 `WRITE_DRAFT` 라우트의 벤더를 따른다. 노트의 모델명은 실제 호출 기준으로 적는다.

## 2. 모델별 관심사 분리

### 현재 구조

렌즈는 이미 셋이지만 모델이 아니라 페르소나에 묶여 있다(`agents/personas.py`). 글마다 리뷰어 2명, 렌즈 3개로 고정이다.

- `build_review_assignments` 는 작성자 렌즈를 다른 리뷰어의 보조 렌즈로 붙인다. 그래서 FACT 담당 페르소나가 쓴 글의 FACT 는 전담 리뷰를 받지 못한다.
- `publishing.py` 는 `reviewer_1, reviewer_2 = draft.reviewer_assignments` 로 2명을 가정한다.
- 중재자는 작성자와 그 리뷰어를 뺀 나머지 슬롯이다(`draft_pipeline.py`).

전담 구조로 가려면 "글마다 렌즈 N개, 렌즈마다 모델 1개"로 바꿔야 한다. 중재자 규칙과 요약 노트도 함께 바뀐다.

### 팩트체크

지금 구조의 문제는 모델 선택 이전에 있다.

- **출처가 없다:** `LLMClient.complete` 에 도구 파라미터가 없어 리뷰어는 기억으로만 판단한다.
- **확인 불가가 무시된다:** `checklist.unverifiable` 은 저장만 된다. `post_draft.py` 의 검증기가 비었는지만 보고, 판정과 발행은 읽지 않는다. 리뷰어가 확인하지 못했다고 적어도 그대로 발행된다.
- **링크를 확인하지 않는다:** `_warn_external_links` 는 http(s) 스킴만 본다.

제안은 다음과 같다.

1. 글을 주장 단위로 나눠 주장마다 판정한다(SAFE 방식). 출력은 `{section_id, claim, status, source_url, note}` 이고 `status` 는 `SUPPORTED`·`REFUTED`·`UNVERIFIABLE` 이다.
2. 심각도는 코드가 정한다(D-03 과 같은 방식). `REFUTED` 는 CRITICAL, `UNVERIFIABLE` 은 MAJOR 다. 작성자는 그 주장을 지우거나 근거를 댄다. 출처 URL 이 없는 `SUPPORTED` 는 `UNVERIFIABLE` 로 본다.
3. 리뷰 호출에 웹 검색을 연결한다.
4. 링크 응답 확인을 리뷰 전 단계로 넣고 결과를 팩트체크 입력으로 준다. 네트워크 결과는 흔들리므로 결정론적 게이트에는 넣지 않는다.

웹 검색과 JSON 스키마를 함께 쓸 수 있는지는 벤더마다 다르다.

| 벤더 | 문서 내용 | 확인하거나 바꿀 것 |
|---|---|---|
| Gemini | 구조화 출력과 Google Search 병행 가능(Preview) | 문서는 "Gemini 3 series 한정"이라면서 도구 예제는 `gemini-4-argon` 이다. 실제 호출로 확인한다 |
| Grok 4.7 | Responses API 에서 `web_search` + `json_schema` 예제 있음 | `clients/llm/xai.py` 의 `/chat/completions` 는 xAI 문서에서 legacy 다. Responses API 로 옮겨야 한다 |
| Claude | `web_search` 있음($10/1,000회). citations 가 항상 켜지고, citations 와 `output_config.format` 을 함께 쓰면 400 이다 | 두 문서가 충돌해 보인다. 실제 호출로 확인한다 |

### 문체·어투·문단 정리

리뷰 렌즈로 넣으면 작동하지 않는다.

- 톤 보존 규칙이 어조와 문장 리듬 지적을 금지한다(`contracts.py` `must_not_flag`).
- 표현 지적은 MINOR 이고, MINOR 만 남으면 수정 없이 발행한다(`evaluation.py` `evaluate`).
- D-47 이 같은 안을 "톤 보존 규칙과 충돌한다"며 이미 기각했다.

그래서 리뷰 통과 뒤 `READY_TO_PUBLISH` 전에 교정 단계를 둔다. 글 전체를 다시 쓰게 하지는 않는다. 문법만 고치라고 해도 LLM 이 의미를 바꿨다는 연구가 있고(에세이 대상, 코드 포함 기술 글은 아님), 소규모 논문 교정 비교에서 AI 수정은 61% 개선·14% 악화, 사람은 90% 개선이었다(논문 2편, 8문단·표 2개).

| 작업 | 담당 | 안전장치 |
|---|---|---|
| "~하십시오", "~라고 볼 수 있다", "~인 상황이다", 문단 길이 | 정규식 | LLM 을 쓰지 않는다 |
| 줄바꿈·문단 정리 | 가장 싼 모델 | 공백을 뺀 전후 문자열이 같을 때만 적용한다. 글자가 바뀌지 않았음을 코드로 보장한다 |
| 어투 교정 | 작성 모델과 다른 모델 | 문단별 패치만 받는다. 코드 블록·인라인 코드·숫자·URL·앵커가 전후 같을 때만 적용한다 |

v2 문장 규칙 중 "한 문장에 새 개념 하나"나 "무생물 주어 피하기"는 정규식으로 잡기 어려워 어투 교정의 몫이다.

단계 규칙은 다음과 같다.

- 안전장치에 걸리면 그 패치를 버리고 원문을 유지한다. 교정 실패로 글을 멈추지 않는다.
- 교정본은 새 버전이고 게이트를 다시 통과해야 한다. 재리뷰는 하지 않는다. 안전장치가 그 역할을 맡는다.
- 사람 승인(D-17)은 교정본에 대해 받는다.
- `PostStatus`·`RevisionCause` 추가와 03 문서 전이표 변경이 필요하다.

위험이 하나 있다. 한 모델이 세 페르소나의 글을 모두 고치면 문체가 비슷해질 수 있다. `tone_markers` 를 함께 넘기고, 규칙을 어긴 문단만 고치게 해서 줄인다. 페르소나별 차이가 유지되는지는 실제 결과로 확인해야 한다.

### 어떤 모델이 무엇을 잘하는가

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

### 직접 재는 방법

순위표 대신 우리 작업으로 잰다. `replay(binding=)` 를 쓰되 글을 바꿔 넣는 작은 스크립트만 더한다.

- **팩트체크:** 실제 글 5편에 틀린 API, 버전, 없는 옵션을 10개씩 심는다. 모델·effort·검색 조합별로 잡은 수(재현율)와 없는 오류를 지어낸 수(거짓 지적)를 잰다. 작성자 수용률은 정답 여부가 아니므로 지표로 쓰지 않는다.
- **어투:** 같은 문단 20개를 모델별로 고치게 하고, 모델명을 가린 채 사용자가 평가한다. 안전장치 통과율도 함께 본다.
- **비용(추정):** Opus 5.5 기준 리뷰 1회 약 $0.1~0.3, 30회면 $3~9 다. 입력 12k, 출력 3~10k 토큰을 가정했고 검색비는 별도다.
- **운영 지표:** `compute_metrics` 는 호출 종류·슬롯별로만 집계한다. 라우팅 뒤에는 모델·렌즈별 집계가 필요하다.

## 3. 글의 전문성 기준

### 현재 코드

파이프라인은 두 글을 구별하지 못한다.

- 수준을 나타내는 필드는 자유 문구인 `TopicProposal.audience` 뿐이다. Post BE `Post` 스키마에는 없다.
- 주제 제안 프롬프트와 리뷰 렌즈에 깊이 기준이 없다.
- 게이트 규칙은 모든 글에 같다(길이 범위, 코드 블록 1개 이상, `REQUIRED_SECTIONS` = intro·body-1·tradeoff·conclusion). 입문 글에도 `tradeoff` 가 필수라 겉모양이 비슷해진다.

### 기준표 초안

틀은 AWS 세션 레벨 100~400 에서 빌렸고 세부 기준은 이 검토의 초안이다. 작성자 신고가 아니라 본문에서 확인할 수 있는 항목으로 잡았다.

| | 100 입문 | 200 실무 기초 | 300 심화 | 400 전문 |
|---|---|---|---|---|
| 핵심 질문 | 어떻게 쓰나 | 제대로 쓰려면 | 언제, 왜, 어떤 순서로 바꾸나 | 왜 그렇게 동작하고 어디서 깨지나 |
| 전제 지식 | 없음 | 기본 사용 경험 | 여러 방식을 직접 써 봄 | 내부 동작과 운영 경험 |
| 판단 | 절차뿐 | 모범 사례 | 기각한 대안, 전환 순서, 되돌리는 비용 | 한계와 실패 원인 분석 |
| 근거 | 공식 문서 | 실행되는 코드 | 실제 코드의 변경 전후 | 측정값, 운영 기록 |

- "Nest 모듈 만들어보기"는 100 이다. 공식 문서의 절차로 대체할 수 있다.
- "계층형 구조에서 클린 아키텍처로 단계적 리팩토링"은 300 이다. 두 구조를 전제로 전환 순서와 중간 상태를 판단해야 한다. 실측과 실패 기록이 있으면 400 이다.

### 적용 단계

1. **주제:** `TopicProposal` 에 목표 레벨, 전제 지식, 다룰 판단을 추가한다.
2. **판정:** 리뷰 통과 뒤 작성자와 다른 모델이 판정한다. 목표 레벨은 입력에 넣지 않고, 기준별로 본문을 인용하게 한다. 모델이 자기 글을 높게 매길 수 있어 작성자 신고값은 라벨로 쓰지 않는다.
3. **표시:** 판정값을 `level` 로 발행한다.
4. **검증:** 사용자가 직접 라벨을 단 글 10~20편과 모델 판정의 일치율을 잰다. 기준표와 기준 글은 DB 에 버전을 붙여 두고 라운드에 스냅샷으로 저장한다.
5. **차단(나중):** 일치율을 확인한 뒤 목표보다 낮은 글을 MAJOR 로 막을지 검토한다. 근거 없이 수정만 반복하면 분량만 늘 수 있어 처음부터 넣지 않는다.
6. **구조 규칙(나중):** `StructureRules` 를 레벨별로 나눈다. 예를 들어 100 은 `tradeoff` 를 빼고, 300 이상은 기각한 대안을 필수로 둔다.

### 표시할 때 주의할 점

- Post BE 는 `forbidNonWhitelisted: true` 라 DTO 에 없는 필드를 거부한다. `CreatePostDto`·`Post`·응답 DTO 를 먼저 배포한 뒤 파이프라인이 `level` 을 보내야 한다. 기존 글이 있으니 선택 필드로 둔다.
- 배포 순서는 vans_story_be_post, vans_story, vans_devblog_fastapi 다.
- `PostCard` 의 `renderBadge` 는 `PostList` 가 카테고리 아이콘으로 이미 쓰고 있다. 레벨 표시는 따로 추가한다.
- 목록 필터와 검색 인덱스 반영은 나중에 정한다.

### 가장 큰 위험: 근거

300·400 의 핵심은 실제 경험인데 LLM 에는 그 경험이 없다. `troubleshooter` 페르소나는 장애 대응기와 "1인칭 경험담"을 요구하고(`personas.py`), v2 프롬프트에는 꾸며낸 경험을 막는 규칙이 없다. 실제 글에서 나타났는지는 확인하지 못했다. 겪지 않은 장애 이야기는 웹 검색 팩트체크로도 잡기 어렵다.

- 300 이상은 실제 자료를 입력으로 준다. git 이력과 `code-graph context` 출력이 후보다. 자료가 없으면 상한을 200 으로 둔다.
- "1인칭 경험담" 지시는 세 제안과 별개로 먼저 고칠 만하다.

## 실제 호출로 확인할 것

- [ ] Gemini 4 Argon: Google Search + `responseJsonSchema` 병행
- [ ] Claude: `web_search` + `output_config.format` 병행
- [ ] Grok 4.7: Responses API 로 옮긴 뒤 `web_search` + `json_schema` 병행
- [ ] 벤더별 effort 파라미터와 temperature 허용 여부
- [ ] 실제 발행 글에서 꾸며낸 경험담과 팩트 오류가 얼마나 나오는지

## 사용자가 정할 것

| 항목 | 권장 |
|---|---|
| 낮은 레벨 글을 막을지, 라벨만 붙일지 | 라벨 먼저. 일치율 확인 뒤 차단 검토 |
| 레벨 하한(예: 200 미만 주제 제외) | 일치율 확인 뒤 |
| A/B 덮어쓰기(`body.bindings`) 유지 | 구현 때 사용처를 보고 결정 |
| 벤더별 발행 계정(D-45) 유지 | 유지. 작성 모델 벤더 기준 |
| 렌즈 구성(팩트, 논리, 실무) 유지 또는 재편 | 측정 결과를 보고 결정 |

## 출처

- [AA-Omniscience v1.1](https://artificialanalysis.ai/evaluations/omniscience)
- [Gemini structured outputs](https://aistudio.google.com/docs/structured-output), [Gemini models](https://aistudio.google.com/docs/models)
- [xAI structured outputs](https://docs.x.ai/developers/model-capabilities/text/structured-outputs), [xAI web search](https://docs.x.ai/developers/tools/web-search), [xAI Chat Completions(legacy)](https://docs.x.ai/developers/model-capabilities/legacy/chat-completions), [Grok 4.7 guide](https://docs.x.ai/developers/grok-4-7)
- [Claude structured outputs](https://platform.claude.com/docs/en/build-with-claude/structured-outputs), [Claude web search](https://platform.claude.com/docs/en/agents-and-tools/tool-use/web-search-tool)
- [How LLMs Distort Our Written Language](https://arxiv.org/abs/2603.18161), [AI 교정 비교 기사](https://www.news-medical.net/news/20260210/AI-editing-tools-make-more-corrections-but-reduce-writing-quality.aspx), [SAFE](https://arxiv.org/abs/2403.18802)
- [언어별 LLM 글쓰기 노트](https://peterkaminski.ai/research/llm-writing-quality-by-language/), [LLM Guides 블라인드 테스트](https://llmguides.ai/lab/llm-writing-quality-test/), [BenchLM 글쓰기 순위](https://benchlm.ai/best/writing), [보조 집계: BenchLM 환각률](https://benchlm.ai/benchmarks/omnisciencehallucinationrate)
- [AWS 세션 레벨 안내](https://aws.amazon.com/events/summits/asean/online/agenda)
