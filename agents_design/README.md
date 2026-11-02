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
- 코드 컨벤션은 이 저장소의 [CONVENTIONS.md](../CONVENTIONS.md)다.
