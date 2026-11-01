# CLAUDE.md

## 에이전트 파이프라인

세 LLM 에이전트가 주제 선정·작성·교차 리뷰를 거쳐 블로그 글을 발행하는 파이프라인이 검색 서비스와 같은 앱에 들어간다(설계 07 문서 D-01, 2026-10-30 변경). 설계는 `agents_design/`이다.

- 시작점은 [agents_design/README.md](agents_design/README.md)다. 확정 결정·미결 사항·구현 로드맵은 `07_decisions_and_open_questions.md`에 있다.
- 진행(2026-10-30): 로드맵 0단계(CONVENTIONS 보강)와 1단계(`app/models/`의 enums·model_binding·topic·agent_call·publishing_round·post_draft, `app/services/evaluation.py`, 테스트)를 마쳤다. 다음은 2단계(async Mongo 리포지토리, `AgentCallService`, `FakeLLMClient`).
- 남은 결정: Q-14(MAJOR 분쟁도 중재할지)·Q-15(중재자 독립성)는 기본 가정으로 구현했다. Q-17(Mongo 위치)은 같은 Mongo·같은 DB로 정했다(D-43, 2026-11-02). Q-16(에이전트 내부 API 키)은 6단계 전에 정한다.
- 문서끼리 서로 참조한다. 규칙 하나를 바꾸면 02(모델)·03(전이표)·04(오케스트레이션)·06(구조)·07(결정)을 함께 맞추고, 코드가 설계와 달라지면 같은 커밋에서 문서를 고친다.
