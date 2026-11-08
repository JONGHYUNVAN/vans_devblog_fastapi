# 다중 에이전트 블로그 파이프라인 설계

세 LLM 에이전트가 각자 주제를 정해 글을 쓰고, 서로의 글을 교차 리뷰한 뒤 발행본을 완성하는 파이프라인의 설계 문서다.
외부 통신(LLM API, 블로그 발행 API) 계층은 추상 인터페이스로만 다루고 도메인 로직·상태 전이·오케스트레이션 규칙에 집중한다.

| 문서 | 내용 |
|---|---|
| [01_overview.md](01_overview.md) | 목표, 범위, 용어, 설계 원칙, 전체 흐름 |
| [02_domain_model.md](02_domain_model.md) | 열거형, 엔티티, 값 객체, 불변식, 저장 형태 |
| [03_state_machine.md](03_state_machine.md) | `PostDraft`·`PublishingRound` 상태 전이표와 가드 조건 |
| [04_orchestration.md](04_orchestration.md) | 단계별 오케스트레이션, 평가 엔진 규칙, 가드레일, 실패 처리 |
| [05_agent_contracts.md](05_agent_contracts.md) | 에이전트 페르소나·리뷰 렌즈, 프롬프트 입출력 JSON 계약 |
| [06_fastapi_architecture.md](06_fastapi_architecture.md) | 패키지 구조, 계층별 책임, API·CLI, 비동기 모델, 테스트 전략 |
| [07_decisions_and_open_questions.md](07_decisions_and_open_questions.md) | 확정한 결정(ADR 요약), 미결 사항, 구현 로드맵 |
| [08_glossary_and_provenance.md](08_glossary_and_provenance.md) | 용어 사전(두 층 모형, 추출→등록 확인→승인→표시)과 글 단위 제작 기록. 설계와 구현 상태 |
| [09_pipeline_as_data.md](09_pipeline_as_data.md) | 조립·프롬프트·라우트·조절 값을 Mongo append-only 버전 문서로 옮기고 앱은 최신 버전을 실행만 하는 설계(경계, 레지스트리, 스키마, 실행기, 검증, CLI, 이행). 설계만, 구현 전 |

## 상태

- 2026-10-30: 초안과 아홉 차례 정합성 검토를 반영했다. 이때 정한 예산 계정·벤더 브레이커·Task 취소·시간 초과 규칙은 아래 단순화로 없앴다.
- 2026-10-30: 발행 연동을 정했다. LLM 출력은 마크다운으로 두고, 이 서비스가 Tiptap JSON으로 변환해 사람이 쓴 글과 같은 `CreatePostDto`로 Post BE에 보낸다(Post BE 수정 없음). 인증은 에이전트 전용 관리자 계정, 카테고리는 주제 제안에서 고르며, 결과를 모르는 발행은 사람이 확인한다(07 문서 D-27, D-39~D-41).
- 2026-10-30: 실행 구조를 단순화했다. 단일 프로세스에서 글마다 코루틴 하나가 LLM 호출을 직접 기다려 반영하고, 토큰 예산은 글 소프트 상한 하나다. 작업 큐·dispatch·heartbeat·lease·예산 계정·벤더 브레이커·다중 문서 트랜잭션·라운드 시간 상한을 없앴고 standalone MongoDB에서 돈다. 함께 고친 결함: 사람 결정을 덮어쓰던 라운드 시간 초과, 연속이 아니라 누적으로 세던 게이트 실패, 예산 초과 글의 재개 경로, `FAILED` 글 하나만 폐기하기(07 문서 D-18~D-38).
- 2026-10-30: 단순화 뒤 정합성 검토 22건을 반영했다. 관리자 수동 격리와 라운드 `OPEN` 상태를 없앴고(D-42), `revision_cause`를 `DRAFTING`까지 유지한다. 사람 결정 뒤 발행 카운터·의도를 지우고, 새 버전이 제출되면 이전 승인을 지운다. 주제 확정은 없을 때만 만들고, 라운드에도 낙관적 잠금을 두며, 정리를 스캔마다 돌린다. 요약 노트의 LLM 텍스트는 이스케이프한다.
- 2026-10-30: 구현 시작. 별도 저장소로 시작했다가 사용자 결정으로 이 저장소(`vans_devblog_fastapi`)의 같은 앱에 넣었다(07 문서 D-01). 로드맵 0단계(CONVENTIONS 보강)와 1단계(`models/` 전부, `PostDraft`·`PublishingRound` 전이, `services/evaluation.py`, 단위 테스트)를 마쳤다. 구현하며 02 문서에 `last_reviewed_version`·`rejected_by`·`abort_reason` 필드, `EscalationDecisionKind`, 발행 메서드(`begin_publish`·`fail_publish`)를 더했다. 미결 Q-16(내부 API 키)·Q-17(Mongo 위치)을 더했다.
- 2026-11-02: Q-17을 정했다. 파이프라인 컬렉션은 검색 서비스와 같은 Mongo·같은 DB에 두고 비동기 클라이언트만 따로 만든다(07 문서 D-43).
- 2026-11-02: 로드맵 2단계를 마쳤다. `repositories/` 4개(async, revision 잠금, 멱등 생성), `clients/llm/`(계약·레지스트리), `services/agent_calls.py`(예산·재시도·영수증), `FakeLLMClient`·`FakeAsyncCollection`. lifespan 이 비동기 클라이언트를 만들고 인덱스를 보장한다.
- 2026-11-02: 로드맵 3단계를 마쳤다. `agents/`(페르소나 3종·렌즈, 05 문서 계약 전부, 프롬프트 v1 템플릿 6개, 빌더, 파서), `models/sections.py`·`models/tiptap.py`(markdown-it-py), `services/validation.py`. 호출 서비스에 문맥 검사 `check` 인자를 더했다.
- 2026-11-02: 로드맵 4단계를 마쳤다. `services/round.py`(생성·완료·중단·reconcile), `services/topic_selection.py`(제안·충돌 판별·배정·확정), `services/draft_pipeline.py`(상태별 한 걸음·한 번 저장, 병렬 리뷰·중재 실패 처리), `services/runner.py`(스캔·코루틴 표·내부 오류 경계), lifespan 러너 시작, CLI `agents run`. 06 문서 시나리오 ①~⑫를 `PipelineHarness`·`ScriptedLLMClient`로 통과했다. 호출 서비스는 `CallResult(output, call_id)`를 돌려준다. 발행은 `DraftPublisher` 계약만 두고 6단계에서 채운다.
- 2026-11-02: 로드맵 5단계 코드를 마쳤다. `clients/llm/`(anthropic: 공식 SDK 2.x + 구조화 출력, google·xai: REST, 실패 분류는 LLMTransportError/ValueError), `clients/publisher/`(카테고리 조회), `RoundService.start_round`(카테고리 스냅샷), `Settings`(벤더 키·`AGENT_SLOT_BINDINGS`·`POST_BE_URL`), `dependencies.build_llm_registry`, CLI `agents start-round/status/abort`, e2e 테스트 2개. `LLMParams` 에 `model_id` 를 더했다. 실제 모델 완주는 키를 넣고 돌려야 한다.
- 2026-11-02: 로드맵 6단계 코드를 마쳤다. Q-16을 정해(D-44) 에이전트 API 키를 `AGENTS_INTERNAL_API_KEY`로 나눴다. `clients/publisher/post_be.py`(로그인·발행·실패 분류), `services/publishing.py`(요약 노트·Tiptap 변환·`CreatePostDto`·의도·결과 반영), `services/draft_decision.py`, `routers/agent_rounds.py`·`schemas/agent_pipeline.py`(golden 3개), CLI `agents show/resolve/approve/retry/discard`, `Settings` 발행 계정. 400은 새 `InvalidRequestError`다. 테스트 하네스는 `FakePublisher` 대신 실제 `PublishingService` 위의 `FakePostBE`를 쓴다. 실제 발행은 계정을 만들어 사용자가 확인한다.
- 2026-11-02: 로드맵 7단계의 도구를 마쳤다. `services/metrics.py`(07 문서 4절 지표, 순수 함수)·`agents metrics`·`GET /api/v1/agents/metrics/`, `AgentCallService.replay`·`agents replay [--provider] [--model]`. Q-07 확정과 프롬프트 튜닝, 승인 토글 해제는 실제 라운드 3회 이상 돌린 뒤의 일이다.
- 2026-11-02: 전 단계 재검토에서 찾은 버그 7건을 고쳤다. 수정 입력이 UPHOLD_AUTHOR 로 종결된 항목을 미해소로 보내던 것, 발행 계정 미설정이 의도 저장 뒤에 터져 재개 때 `PUBLISH_UNCERTAIN` 이 되던 것, 라운드 중단이 방금 발행된 레지스트리를 `RELEASED` 로 덮던 것(조건부 갱신), 주제 재제안 3회째의 KeyError, 노트의 `^` 미이스케이프(각주 오인), 앵커 삭제가 문단을 합치고 코드 안 앵커를 지우던 것, 인라인 코드의 마크 겹침. 남은 설계 편차·위험(SPOT_CHECK 의 비 CRITICAL 검사, 리뷰어 입력의 `rationale`, 재승인 허용, 429 세마포어, 실제 계약 스키마 e2e 등)은 아직 손대지 않았다.
- 2026-11-03: 재검토에서 남겼던 설계 편차·위험 13건을 고쳤다. SPOT_CHECK 파서는 CRITICAL 만 앵커·렌즈를 검사하고, 리뷰어·중재 입력은 `rationale` 과 슬롯 필드를 뺀 `TopicForReviewer`·`ReviewItemForReviewer` 다. `check` 의 `ValidationError` 도 재시도하고, 429 는 벤더 게이트(`ProviderGate`)를 30초 동안 1건으로 줄인다(04 문서 9절). 승인은 토글이 꺼져 있거나 재승인이면 409, `--wait` 는 승인됐거나 토글이 꺼진 `READY_TO_PUBLISH` 를 기다린다. 지표는 fast-track(v1 이 발행본)·게이트 실패율(거친 버전만)을 07 문서 정의대로 센다. 글 생성의 3xx 는 확정 실패, 종료는 발행 호출을 끊지 않고 결과 반영까지 기다리며(`drain`), 병렬 단계의 예상하지 못한 예외는 성공한 형제를 저장한 뒤 `INTERNAL_ERROR` 다. 회수 대상 `external_post_id` 가 요약 응답·CLI 에 보이고, e2e 는 계약 출력 스키마 6종을 벤더에 보낸다.
- 2026-11-03: 계층 규칙(CONVENTIONS 1절) 위반을 정리했다. 읽기 전용 `services/pipeline_views.py`(`PipelineViewService` + 엔티티 → 응답 변환 함수)를 두고 라우터·CLI 가 리포지토리 대신 이것을 부른다. `Settings.agent_slot_bindings` 는 원문 dict 이고 검증은 `RoundService.create_round` 가 한다(core 는 models 를 모른다). `schemas/agent_pipeline.py` 의 변환 classmethod 와 diff 계산을 서비스로 옮겼고, `UnconfiguredDraftPublisher` 는 `services/publishing.py` 로, 게이트의 마크다운 파서는 `models/tiptap.parse_markdown` 하나로 합쳤다. 열거형·값 객체의 타입 공유만 예외로 CONVENTIONS 에 적었다.
- 2026-11-04: 발행 계정을 벤더별로 나눴다(D-45). 글을 쓴 슬롯의 벤더 계정이 게시글 작성자가 되어 블로그에서 어느 AI가 쓴 글인지 보인다. `PUBLISHER_EMAIL`·`PUBLISHER_PASSWORD` 는 `{ANTHROPIC,GOOGLE,XAI}_PUBLISHER_EMAIL`·`_PASSWORD` 로 바뀌었다.
- 2026-11-05: 품질 개선 0~4단계 코드를 마쳤다(07 문서 D-48~D-59, 로드맵 8단계). 범위 필드·`OUT_OF_SCOPE`·MAJOR 중재, `POST_REVIEW`(다듬기·전체 읽기·수준 평가), Mongo `agent_routes` 호출별 라우트와 라운드 스냅샷(`AGENT_SLOT_BINDINGS` 삭제), effort·잘림 처리, xAI Responses API, 수준 라벨·근거 입력, 렌즈당 모델 하나, 웹 검색 사실 확인, TS 문법 검사, 프롬프트 v3. 단위 테스트는 가짜 LLM 이고 실제 벤더로는 돌리지 않았다. 옛 라운드·글은 같은 코드로 끝까지 돈다.
- 2026-11-06: 운영 라운드 첫 완주에서 프런트에 화면이 없는 `jest` 메인에 글이 발행돼 블로그 주소가 404 였다. 라운드 스냅샷을 `AGENT_MAIN_CATEGORIES` 로 거른다(D-60).
- 2026-11-07: 프롬프트 v4 코드 완료(07 문서 D-62). 가족별 템플릿 `app/agents/prompts/v4/`, 작성·수정 입력에서 페르소나 제거, `tone_markers`·`polish_format` 폐지, `conclusion` 필수 해제, `level_criteria` 입력, 라운드 생성 때 `validate_prompts`, `agents compare-prompts --out-dir`. `Settings.prompt_version` 기본값은 v4 다(같은 날 2차 비교 뒤 올림). 되돌리기는 `.env` 의 `PROMPT_VERSION=v3`. 같은 날 첫 비교에서 세 가족이 v3 보다 길어져 작성 프롬프트에 길이 예산·어미·상투 문구 규칙을 보강했고, 2차 비교에서 셋 다 게이트를 통과했다(`.claude/reviews/prompts_v4_compare_2026-11-07.md`).
- 2026-11-07: 용어 사전·제작 기록(08 문서, 07 문서 D-63~D-69, 로드맵 9단계) FastAPI 쪽 코드 완료. `POST_REVIEW` 에 `GLOSSARY` 단계(선택 라우트, 실패해도 발행), 컬렉션 5개(`glossary_headwords`·`glossary_senses`·`post_glossary`·`glossary_counters`·`post_provenance`), 발행 뒤 파생 문서 따라 쓰기와 정리, 공개 읽기 API(`/api/v1/glossary/…`, `/api/v1/agents/posts/{id}/provenance/`), CLI `agents glossary …`·`agents provenance …`. 02·03·04·05·06 에 반영했다. 가짜 LLM 테스트뿐이고 배포된 `agent_routes` 에 `GLOSSARY` 라우트는 아직 없다(`agents seed-routes`). vans_story 쪽은 08 문서 2.8절.
- 2026-11-07: 데이터 파이프라인 이행 1a(09 문서 7절) 코드 완료. 프롬프트·라우트·페르소나·조절 값이 Mongo `agent_pipeline_versions` 의 버전 문서(`v1`, `v2`…)로 옮겨 갔고 활성은 최신 버전이다. 라운드는 생성 때 `pipeline_version_id` 를 고정하고 영수증은 `pipeline_version` 을 적는다. `Settings` 의 상수 12개와 `prompt_version` 이 없어졌다. CLI `agents pipeline bootstrap/validate/export/import/edit-prompt/edit-param/edit-routes`, 기준 내보내기 `pipeline/baseline/`. 흐름(`_step`)은 아직 코드다. 실제 시드(`bootstrap`)·배포는 사용자가 한다. 1b(D-80·D-81·D-84)·1c(D-82·D-83)는 뒤.
- 2026-11-08: 글 끝 표기를 `## AI Information` + "- 역할: 모델" 목록으로 바꿨다(09 문서 D-80·D-87, `build_publish_note`). 버전 수·지적 건수·기각 인용은 제작 기록만 보인다. 용어 정의는 사전체 명사구다(D-88): 추출 계약이 "~다" 끝을 재시도로 돌려보내고 승인도 막는다. 운영 버전 문서에는 `edit-param`·`edit-prompt` 로 새 버전을 만들어야 다음 라운드부터 적용된다.
- 코드 컨벤션은 이 저장소의 [CONVENTIONS.md](../CONVENTIONS.md)다.
