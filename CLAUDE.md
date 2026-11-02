# CLAUDE.md

## 에이전트 파이프라인

세 LLM 에이전트가 주제 선정·작성·교차 리뷰를 거쳐 블로그 글을 발행하는 파이프라인이 검색 서비스와 같은 앱에 들어간다(설계 07 문서 D-01, 2026-10-30 변경). 설계는 `agents_design/`이다.

- 시작점은 [agents_design/README.md](agents_design/README.md)다. 확정 결정·미결 사항·구현 로드맵은 `07_decisions_and_open_questions.md`에 있다.
- 진행(2026-11-02): 로드맵 0~5단계 코드를 마쳤다. 1단계는 `app/models/`·`services/evaluation.py`, 2단계는 `repositories/`(agent_round·post_draft·agent_call·topic_registry, optimistic_lock)·`clients/llm/`(base·registry)·`services/agent_calls.py`·`tests/fakes.py`의 `FakeLLMClient`·`FakeAsyncCollection`이다. 3단계는 `agents/`(personas·contracts·builders·parsers·prompts/v1)·`models/sections.py`·`models/tiptap.py`·`services/validation.py`다. 4단계는 `services/`(round·topic_selection·draft_pipeline·runner)·`dependencies.build_pipeline_runner`·lifespan 러너·CLI `agents run`이고, 테스트 하네스는 `tests/fakes.py`의 `PipelineHarness`·`ScriptedLLMClient`·`FakePublisher`다. 5단계는 `clients/llm/`(anthropic·google·xai·http)·`clients/publisher/`(base·post_be)·`RoundService.start_round`·`Settings` 벤더 키·`AGENT_SLOT_BINDINGS`·`POST_BE_URL`·CLI `agents start-round/status/abort`·`tests/e2e/test_llm_vendors.py`·`test_post_be.py` 다. 5단계 완료 기준(실제 모델로 라운드 1회 완주)은 키를 `.env` 에 넣고 `pytest -m e2e tests/e2e/test_llm_vendors.py` → `agents start-round --wait` + `agents run` 으로 사용자가 확인한다. 다음은 6단계(`PublishingService`·`clients/publisher` 발행·요약 노트·승인 토글·`PUBLISH_UNCERTAIN` 해소·`DraftDecisionService`·라우터·golden·결정 CLI). Q-16 을 먼저 정한다.
- 남은 결정: Q-14(MAJOR 분쟁도 중재할지)·Q-15(중재자 독립성)는 기본 가정으로 구현했다. Q-17(Mongo 위치)은 같은 Mongo·같은 DB로 정했다(D-43, 2026-11-02). Q-16(에이전트 내부 API 키)은 6단계 전에 정한다.
- 문서끼리 서로 참조한다. 규칙 하나를 바꾸면 02(모델)·03(전이표)·04(오케스트레이션)·06(구조)·07(결정)을 함께 맞추고, 코드가 설계와 달라지면 같은 커밋에서 문서를 고친다.
