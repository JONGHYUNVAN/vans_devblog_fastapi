# 다중 에이전트 블로그 파이프라인 설계

세 LLM 에이전트가 각자 주제를 정해 글을 쓰고, 서로의 글을 교차 리뷰한 뒤 발행본을 완성하는 파이프라인의 설계 문서다.
구현 전 설계 단계이며, 외부 통신(LLM API, 블로그 발행 API) 계층은 추상 인터페이스로만 다루고 도메인 로직·상태 전이·오케스트레이션 규칙에 집중한다.

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
- 코드 컨벤션은 자매 저장소 `vans_devblog_fastapi`의 [CONVENTIONS.md](../../vans_devblog_fastapi/CONVENTIONS.md)를 따른다. 구현 시작(로드맵 0단계) 때 이 저장소로 복사해 온다.
