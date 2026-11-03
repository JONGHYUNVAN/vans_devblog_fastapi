# CLAUDE.md

## 에이전트 파이프라인

세 LLM 에이전트가 주제 선정·작성·교차 리뷰를 거쳐 블로그 글을 발행하는 파이프라인이 검색 서비스와 같은 앱에 들어간다(설계 07 문서 D-01, 2026-10-30 변경). 설계는 `agents_design/`이다.

- 시작점은 [agents_design/README.md](agents_design/README.md)다. 확정 결정·미결 사항·구현 로드맵은 `07_decisions_and_open_questions.md`에 있다.
- 진행(2026-11-02): 로드맵 0~7단계 코드를 마쳤다(7단계는 도구만. 운영 판단은 남았다). 1단계는 `app/models/`·`services/evaluation.py`, 2단계는 `repositories/`(agent_round·post_draft·agent_call·topic_registry, optimistic_lock)·`clients/llm/`(base·registry)·`services/agent_calls.py`·`tests/fakes.py`의 `FakeLLMClient`·`FakeAsyncCollection`이다. 3단계는 `agents/`(personas·contracts·builders·parsers·prompts/v1)·`models/sections.py`·`models/tiptap.py`·`services/validation.py`다. 4단계는 `services/`(round·topic_selection·draft_pipeline·runner)·`dependencies.build_pipeline_runner`·lifespan 러너·CLI `agents run`이고, 테스트 하네스는 `tests/fakes.py`의 `PipelineHarness`·`ScriptedLLMClient`·`FakePostBE`다. 5단계는 `clients/llm/`(anthropic·google·xai·http)·`clients/publisher/`(base·post_be)·`RoundService.start_round`·`Settings` 벤더 키·`AGENT_SLOT_BINDINGS`·`POST_BE_URL`·CLI `agents start-round/status/abort`·`tests/e2e/test_llm_vendors.py`·`test_post_be.py` 다. 6단계는 `clients/publisher/post_be.py`(로그인·발행)·`services/publishing.py`·`services/draft_decision.py`·`routers/agent_rounds.py`·`schemas/agent_pipeline.py`·`services/pipeline_views.py`(조회·엔티티→응답 변환)·`tests/golden/agents_*.json`·CLI `agents show/resolve/approve/retry/discard`·`Settings`(`USER_SERVICE_URL`·`PUBLISHER_EMAIL`·`PUBLISHER_PASSWORD`·`AGENTS_INTERNAL_API_KEY`)다. 5·6단계 완료 기준(실제 모델로 라운드 1회 완주, 승인 후 Post BE 발행)은 키와 발행 계정을 `.env` 에 넣고 `pytest -m e2e tests/e2e/` → `agents start-round --wait` + `agents run` → `agents approve` 로 사용자가 확인한다. 7단계 도구는 `services/metrics.py`·`AgentCallService.replay`·CLI `agents metrics/replay`·`GET /api/v1/agents/metrics/` 다. 남은 일은 운영이다: 실제 라운드를 3회 이상 돌려 `agents metrics` 로 글당 토큰을 보고 Q-07(`DRAFT_TOKEN_LIMIT`)을 정하고, 판정 불일치·게이트 실패 지표로 프롬프트(`agents/prompts/v1`)를 튜닝하며, 그 뒤 `PUBLISH_REQUIRES_APPROVAL` 해제를 검토한다.
- 남은 결정: Q-14(MAJOR 분쟁도 중재할지)·Q-15(중재자 독립성)는 기본 가정으로 구현했다. Q-17(Mongo 위치)은 같은 Mongo·같은 DB로 정했다(D-43, 2026-11-02). Q-16(에이전트 내부 API 키)은 별도 `AGENTS_INTERNAL_API_KEY` 로 정했다(D-44, 2026-11-02).
- 문서끼리 서로 참조한다. 규칙 하나를 바꾸면 02(모델)·03(전이표)·04(오케스트레이션)·06(구조)·07(결정)을 함께 맞추고, 코드가 설계와 달라지면 같은 커밋에서 문서를 고친다.

## 코드 그래프 (code_graph)

내 저장소들의 의존 그래프를 이 앱이 만들고, 블로그(vans_story) 프로젝트 페이지가 그린다. 손으로 쓰는 아키텍처 문서·그림을 대체한다. 기획은 `code_graph_design/` 하나다.

- 시작점은 [code_graph_design/README.md](code_graph_design/README.md)다. 확인된 전제, 결정 G-01~G-22, 저장소 설정(예시는 `code_graph_design/repos.example.json`), 코드 배치, API, 로드맵 A/B/C가 거기 있다. 설계 문서 세트는 만들지 않는다. 코드가 기획과 달라지면 같은 커밋에서 그 README 를 고친다.
- 진행(2026-11-03): A 단계 완료(파일 그래프·진단·context, CLI `code-graph analyze/context`). B 단계 코드 완료(호출 지점 추출 `services/code_graph_calls.py`, `clients/openapi.py`, `models/service_graph.py`·`services/service_graph.py`, API `services/`·`flows/{flow}/`, CLI `code-graph flow`). 사용자 확인이 남았다: Mongo 저장(`code-graph analyze`)은 Atlas 허용 IP 에서, NestJS·Spring hop 의 스키마는 `.env` 에 `POST_BE_URL`·`POST_BE_SWAGGER_PASSWORD`·`USER_SERVICE_URL` 을 넣고. 다음은 C(vans_story 뷰어, 별도 저장소).
