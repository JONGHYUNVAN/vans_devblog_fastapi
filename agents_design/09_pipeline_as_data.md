# 09. 데이터로 기술되는 파이프라인

에이전트 파이프라인의 **조립·프롬프트·라우트·조절 값**을 Mongo 의 append-only 버전 문서로 옮기고, 앱은 최신 버전을 읽어 실행만 하게 하는 설계다. 원시 동작(입력 빌더, 파서·정규화, 결과 처리기, 게이트, TipTap 변환, 발행, 낙관적 잠금, 중단 뒤 재개)은 이름으로 참조되는 코드로 남긴다. 전부를 DB 해석기로 빼는 극단은 하지 않는다.

배경(2026-11-07 사용자 판단). 프롬프트가 자주 바뀌는데 바꿀 때마다 커밋·푸시·CloudType 재배포가 따라오고, 앱이 커서 파이프라인 흐름이 `draft_pipeline.py`·`polishing.py`·`publishing.py`·`glossary.py`·`topic_selection.py` 에 흩어져 한눈에 읽기 어렵다. 호출별 모델 표는 이미 Mongo `agent_routes` 에 있고 라운드마다 스냅샷된다(07 문서 D-51). 프롬프트는 저장소 파일 `app/agents/prompts/v1~v4/` 이고 버전 디렉터리는 배포 뒤 고치지 않는다(05 문서 머리말). 이 둘을 하나의 버전 문서로 합치고 단계 목록까지 데이터로 옮기면, 프롬프트 수정은 CLI 한 번으로 새 버전이 되고 흐름은 문서 하나에서 읽힌다.

이 문서는 설계만 담는다. 구현은 하지 않았다. 02~08 문서는 고치지 않고, 구현 때 반영할 변경을 9절에 목록으로 둔다. 결정은 07 문서에 옮기지 않고 10절에 D-70 부터 둔다(D-69 는 08 문서의 용어 템플릿 결정이 이미 쓴다). 결정 대기는 11절에 Q-32 부터다.

## 1. 경계 선언

### 1.1 데이터로 가는 것과 코드로 남는 것

| 구분 | 데이터(버전 문서) | 코드(이름으로 참조) |
|---|---|---|
| 조립 | 단계 목록·순서, 단계가 도는 상태(`at`), 실행 조건(이름 목록), 실패 정책(`fail`·`skip`), 호출 재시도 수, 반복 상한(`max_iterations`·`max_validation_retries`·`max_publish_attempts`·`max_topic_attempts`), `POST_REVIEW` 하위 단계의 순서와 포함 여부 | 상태 기계(`PostDraft`·`PublishingRound` 의 전이 메서드와 가드, 03 문서 전이표), 단계 종류(`kind`)의 실행 방식(단일 호출, 병렬 fan-out 과 성공분 저장, 검색→변환 절차), 러너·스캔·정리 |
| 프롬프트 | 가족(`claude`·`gemini`·`grok`·`haiku`·`common`) × 템플릿 이름 → 본문. 페르소나 프로필(`display_name`·`writer_bias`·`primary_lens`·근거 없는 수준 상한) | 렌더링(시스템 = 템플릿, 사용자 = 입력 JSON), `string.Template` 치환(`$writer_bias`), 가족 결정 `prompt_family(CallModel)` |
| 라우트 | 호출별 후보 배열(지금 `agent_routes`), `SLOTS`, `LEVEL_CRITERIA` | 후보 선택 규칙(작성 벤더 회피 등, `models/routing.py` 의 `select_*`), 벤더·모델 지원표(`candidate_errors`), 키 없는 벤더 제외 |
| 조절 값 | 길이 상한, 코드 블록 최소 수, 승인 필요 여부, 글 끝 표기 문구, 게이트 임계값, 주제 중복 임계, 회피 기간, 글 토큰 상한, 용어 사전 값, 구조 규칙(필수 섹션·금지 문법), 톤 보존 목록, 사실 확인 심각도 표, 전체 읽기 차단 종류, 다듬기 허용폭 | 값을 읽어 쓰는 검사·변환 코드 자체 |
| 입출력 | – | 계약 모델(`agents/contracts.py`), 입력 빌더, 문맥 검사, 정규화, 도메인 처리기 |
| 외부 | – | LLM 클라이언트, Post BE 발행, 링크 확인, Tiptap 변환 |
| 저장 | – | 리포지토리, 낙관적 잠금, 결정론적 ID, 중단 뒤 재개, 정리 |

가르는 기준은 "바꿀 때 코드 리뷰가 필요한가"다. 문구·수치·순서·모델은 바꿔도 전이표가 깨지지 않으니 데이터다. 상태를 바꾸는 메서드와 병렬 실패 처리, 외부 호출은 틀리면 중복 게시나 비용 유출이 나므로 코드다. 데이터가 코드를 가리킬 때는 **이름**으로만 가리키고, 이름이 레지스트리에 없으면 시드가 거절한다(4절).

### 1.2 원시 동작 레지스트리

`app/services/pipeline_registry.py` 하나에 종류별 `dict[str, 항목]` 을 둔다. 항목은 호출 가능 객체와 타입 정보(받는 타입, 돌려주는 타입, `skip` 허용 여부)다. 데코레이터로 모으지 않고 명시적 표로 쓴다. `agents/` 는 `services` 를 import 할 수 없으므로(CONVENTIONS 1절) 레지스트리는 `services/` 에 있고 `agents`·`models`·`services` 의 함수를 모은다.

이름은 지금 함수 이름에서 접두 `build_`·`_check` 를 뗀 것이다. 아래 표의 "현재 코드" 가 이름이 가리키는 실제 함수다.

**빌더** (`builders`): 글·라운드 상태 → 입력 모델. 템플릿 이름은 단계 문서가 따로 적는다(지금 `_TEMPLATE_BY_INPUT` 의 대응이 기본값).

| 이름 | 현재 코드 | 입력 모델 |
|---|---|---|
| `propose_topic` | `agents.builders.build_propose_topic_input` | `ProposeTopicInput` |
| `write_draft` | `build_write_draft_input` | `WriteDraftInput` |
| `revise_draft` | `build_revise_draft_input` | `ReviseDraftInput` |
| `review_full` | `build_review_input` | `ReviewInput` |
| `review_spot_check` | `build_spot_check_input` | `SpotCheckInput` |
| `fact_check` | `build_fact_check_input` | `FactCheckInput` |
| `fact_transform` | `build_fact_transform_input` | `FactTransformInput` |
| `arbitrate` | `build_arbitrate_input` | `ArbitrateInput` |
| `arbitrate_search` | `build_arbitrate_search_input` | `ArbitrateSearchInput` |
| `final_read` | `build_final_read_input` | `FinalReadInput` |
| `assess_level` | `build_assess_level_input` | `AssessLevelInput` |
| `polish_tone` | `polishing.py` 안의 인라인 `PolishToneInput(paragraphs=…)`. 함수 `build_polish_tone_input(document)` 로 뽑는다 | `PolishToneInput` |
| `glossary` | `build_glossary_input` | `GlossaryInput` |

**출력 계약** (`schemas`): 이름은 클래스 이름 그대로다. `ProposeTopicOutput`, `DraftOutput`, `RevisionOutput`, `ReviewOutput`, `SpotCheckOutput`, `FactCheckOutput`, `FactSpotCheckOutput`, `ArbitrateOutput`, `FinalReadOutput`, `AssessLevelOutput`, `PolishOutput`, `GlossaryOutput`. 호출 서비스가 `model_validate_json` 으로 파싱하는 대상이다.

**문맥 검사** (`checks`): `AgentCallService.call` 의 `check` 인자. 위반은 `LLMOutputError` 로 재시도.

| 이름 | 현재 코드 | 받는 출력 |
|---|---|---|
| `topic_proposal` | `agents.parsers.topic_proposal_check` | `ProposeTopicOutput` |
| `review_output` | `review_output_check` | `ReviewOutput` |
| `spot_check_output` | `spot_check_output_check` | `SpotCheckOutput` |
| `fact_output` | `fact_output_check` | `FactCheckOutput` |
| `fact_spot_check_output` | `fact_spot_check_output_check` | `FactSpotCheckOutput` |
| `final_read` | `final_read_check` | `FinalReadOutput` |
| `level_assessment` | `level_assessment_check` | `AssessLevelOutput` |

**정규화·변환** (절차 내부. 단계 문서가 직접 참조하지 않고 절차가 쓴다): `review_items`, `spot_check_items`, `normalize_claims`(D-56 URL 대조), `claim_items`(심각도 표는 조절 값 `fact_check.severity` 에서 읽는다), `normalize_section_id`(D-46).

**결과 처리기** (`appliers`): 파싱된 출력을 도메인에 반영하고 전이한다. 전부 `PostDraft`·`PublishingRound` 메서드 또는 그 조합이다. 실행기는 상태를 직접 바꾸지 않는다.

| 이름 | 현재 코드 | 받는 것 | `skip` 허용 |
|---|---|---|---|
| `start_drafting` | `PostDraft.start_drafting(max_iterations)` | – | – |
| `submit_version` | `PostDraft.submit_version(…)` + `find_anchors` | `DraftOutput`·`RevisionOutput`, 호출 모델 | 아니오 |
| `gate_outcome` | `pass_validation` / `fail_validation(errors, max_validation_retries)` | 게이트 오류 목록 | – |
| `add_review` | `PostDraft.add_review(PeerReview)` + `derive_verdict` | `PeerReview` (절차가 만든다) | 아니오 |
| `evaluate` | `services.evaluation.evaluate` + `apply_evaluation` + `enter_post_review(first_stage)` | 완료된 글 | – |
| `add_arbitration` | `PostDraft.add_arbitration` | `Arbitration` | 아니오 |
| `polish_outcome` | `finish_polish(record, next)` / `attach_polished_version(…, next)` | `PolishRecord`(+본문) | 예(절차가 `skipped` 에 사유) |
| `record_final_read` | `record_final_read(final_read, next)` | `FinalReadOutput` | 아니오 |
| `record_level_assessment` | `record_level_assessment(assessment, next)` | `AssessLevelOutput` | 아니오 |
| `finish_glossary` | `finish_glossary(record, next)` | `GlossaryRecord` | 예 |
| `record_proposals` | `TopicAttempt.proposals[slot] = …` (라운드) | `ProposeTopicOutput` | 아니오 |

`next` 는 `POST_REVIEW` 하위 단계의 다음 값이다. 지금은 `_advance_post_review` 가 하드코딩한 순서를 안다. 실행기가 단계 목록에서 다음 하위 단계를 읽어 넘기고, 마지막이면 `DONE` 을 넘긴다. `record_final_read` 의 차단 종류 → `DONE`+`ESCALATED` 분기는 그대로 메서드 안에 있다.

**선택기** (`selectors`): 라운드의 라우트 스냅샷과 글에서 호출 모델 하나를 고른다. 지금 `models/routing.py` 와 `services/routing.py` 의 `DraftRoutes` 다. 각 항목은 검증용으로 "볼 라우트 id 들"을 돌려주는 `route_ids(slots)` 를 함께 가진다.

| 이름 | 현재 코드 | 라우트 | 규칙 |
|---|---|---|---|
| `proposer` | `select_proposer` | `PROPOSE_TOPIC:{persona}` | 첫 후보 |
| `writer` | `select_writer` | `WRITE_DRAFT:{persona}` | 첫 후보 |
| `reviser` | `select_reviser` | `REVISE_DRAFT:{persona}` | 작성 벤더 안 |
| `reviewer` | `select_reviewers`(렌즈 하나) | `REVIEW:{lens}` | 작성 벤더 밖, 앞선 리뷰 벤더 회피 |
| `arbiter` | `select_arbiter` | `ARBITRATE` | 작성·리뷰 벤더 밖 |
| `independent` | `select_independent` | 단계가 적은 라우트 | 작성 벤더 밖 |
| `any` | `select_any` | 단계가 적은 라우트 | 제약 없음 |
| `optional` | `select_optional` | 단계가 적은 라우트 | 없으면 `None`, 단계를 건너뛴다 |

**조건** (`conditions`): 글(또는 라운드·항목)을 받아 참·거짓. 단계의 `when` 과 분기의 `when` 이 이름으로 가리킨다. 레지스트리는 배타 쌍도 선언한다(4절 R-03).

| 이름 | 현재 코드 | 배타 쌍 |
|---|---|---|
| `is_first_draft` | `draft.revision_cause is None` | `is_revision` |
| `is_revision` | `draft.revision_cause is not None` | |
| `scope_full` | `draft.current_review_scope == FULL` | `scope_spot_check` |
| `scope_spot_check` | `… == SPOT_CHECK` | |
| `checks_facts` | `PostDraft.checks_facts(lens)` (렌즈 항목) | `plain_review` |
| `plain_review` | `not checks_facts(lens)` | |
| `from_fact_check` | `ReviewItem.from_fact_check` (중재 항목) | |
| `fact_spot_check_has_work` | `fact_input.changed_section_ids or fact_input.my_previous_items` | |
| `has_pending_slots` | 라운드: 제안도 잠금도 없는 슬롯이 있다 | `all_slots_proposed` |
| `all_slots_proposed` | 라운드: 잠기지 않은 슬롯이 모두 제안을 냈다 | |

**반복 대상** (`iterators`): fan-out 단계가 돌 항목. `pending_reviewers`(`PostDraft.pending_reviewers`), `pending_arbitration_items`(`pending_arbitration_items`), `pending_slots`(라운드: 제안도 잠금도 없는 슬롯).

**게이트** (`gates`): `draft`(`ValidationGate.validate`: 본문 + 해소 두 층 + 앵커 유지), `markdown`(`ValidationGate.check_markdown`: 본문만. 다듬기가 패치마다 쓴다). 내부의 `_check_anchors`·`_check_code_blocks`·`_check_syntax`·`_check_length`·`_check_convertible`·`_check_resolutions`·`_check_anchor_persistence` 는 이름을 받지 않는다. 게이트는 조절 값(`draft_min_chars`·`draft_reject_chars`·`min_code_blocks`·구조 규칙)을 버전에서 읽는다. `draft_max_chars` 는 작성·수정 프롬프트에만 간다(D-93).

**다듬기 안전장치** (이름 없음, `polish` 절차 안): `_preserved_violation`, `_tone_violation`, `_signature`, `_inline_markup`, `_Document`. 정규식 어미 치환(`_fix_phrases`·`_PHRASE_FIXES`)과 긴 문단 알림(`_LONG_PARAGRAPH_CHARS`)은 없앤다(D-81).

**절차** (`procedures`): 호출 하나로 끝나지 않고 안에 데이터 흐름이 있는 동작. 코드가 흐름을 갖고, 단계 문서는 절차에 라우트·템플릿·계약·조절 값을 꽂는다.

| 이름 | 현재 코드 | 꽂는 것 | 흐름(코드) |
|---|---|---|---|
| `plain_review` | `DraftPipelineService._call_review` 의 비사실 경로 | `select`, `template`·`schema`·`check`(FULL·SPOT 각각) | 선택 → 빌더 → 호출 → `review_items`/`spot_check_items` → `derive_verdict` → `PeerReview` |
| `fact_review` | `_call_fact_review` | 검색 `select`·템플릿, 변환 라우트·템플릿, 계약(FULL·SPOT), 심각도 표 | 링크 상태 → 검색 호출 → 변환 호출 → 출처 없는 주장은 호출 실패 → `normalize_claims` → `claim_items`. SPOT 에서 바뀐 섹션도 미결 항목도 없으면 호출 없이 빈 리뷰 |
| `arbitrate_item` | `_call_arbitrate` | `arbiter` 선택, 검색 템플릿(`from_fact_check` 일 때), 판정 템플릿·계약 | 선택 → (검색) → 판정 → `Arbitration` |
| `polish` | `PolishingService.polish` | `independent` 선택 라우트, 템플릿, 계약, 허용폭(조절 값) | 호출(실패는 건너뜀) → 패치마다 안전장치·`markdown` 게이트 → 합친 본문 게이트 → `polish_outcome`. 정규식 어미 치환은 없앤다. 작성 모델이 프롬프트를 보고도 쓴 표현은 그대로 둔다(D-81) |
| `glossary_extract` | `GlossaryService.extract`·`_run`·`_resolve` | `optional` 선택 라우트, 템플릿, 계약, 용어 값 | 표제어 목록 → 호출(실패는 건너뜀) → 등록 확인(D-65) → `finish_glossary` |
| `publish` | `PublishingService.publish`·`_call_and_record` | 글 끝 표기 문구, `publish.requires_approval`, `max_publish_attempts` | 04 문서 8절 그대로. `shield`·`drain` 포함. 수준은 평가가 있으면 항상 `level` 로 보낸다(D-83) |
| `propose` | `TopicSelectionService._propose` | `proposer` 선택, 템플릿, 계약, 검사 | 슬롯별 병렬 호출 → `record_proposals`, 소진이면 `ABORTED(LLM_FAILED)` |
| `assign_and_commit` | `_assign`·`_commit` | 중복 임계, `max_topic_attempts`, `draft_token_limit` | 04 문서 2절 4~7단계. 전체 잠금이면 확정, 아니면 배정 |

**변환·발행 코드** (이름 없음, 절차가 쓴다): `markdown_to_tiptap`, `build_publish_note`(글 끝 "AI Information" 표기. 문구는 조절 값, D-80·D-87), `build_payload`, `PostBEPublisher.publish`, `DerivedRecordsWriter.write`.

**잠금·재개·정리** (이름 없음, 실행기 바깥): `replace_with_revision`, `PostDraft.revision`·`PublishingRound.revision`, `DraftPipelineService.drive` 루프의 다시 읽기, `PostDraft.post_review_stage`·`TopicAttempt` 포인터, `retry_from_failed`, `PipelineRunner.scan`, `RoundService.reconcile`.

### 1.3 레지스트리의 형태

```python
# app/services/pipeline_registry.py (설계 스케치)
@dataclass(frozen=True)
class Applier:
    fn: Callable[..., Any]
    accepts: tuple[type, ...]        # 받는 출력 계약. 비어 있으면 출력이 없는 처리기
    skippable: bool = False          # on_failure=skip 을 허용하는지

@dataclass(frozen=True)
class Selector:
    fn: Callable[..., CallModel | None]
    route_ids: Callable[[PipelineSlots], list[str]]   # 검증이 라우트 존재를 확인할 때 쓴다

BUILDERS: dict[str, Builder]          # 이름 → (함수, 입력 모델 타입)
SCHEMAS: dict[str, type[BaseModel]]
CHECKS: dict[str, Check]              # 이름 → (함수, 받는 출력 타입)
APPLIERS: dict[str, Applier]
SELECTORS: dict[str, Selector]
CONDITIONS: dict[str, Condition]      # 이름 → (함수, 배타 쌍 이름)
ITERATORS: dict[str, Callable]
GATES: dict[str, Callable]
PROCEDURES: dict[str, Procedure]      # 이름 → (코루틴, 꽂을 수 있는 슬롯 이름과 타입)
STAGE_KINDS = ("transition", "gate", "llm_call", "fan_out", "procedure")
```

새 호출 종류를 더하려면 계약 모델·빌더·검사·처리기를 코드로 쓰고 레지스트리에 이름을 등록한 뒤, 새 버전 문서에서 그 이름을 쓴다. 코드 배포와 데이터 버전이 함께 가야 하는 경우는 이것뿐이다. 이름을 빼거나 바꾸면 옛 버전 문서가 깨지므로 이름은 안정적이어야 하고, 빼야 할 때는 최신 버전과 진행 중 라운드가 가리키는 버전이 그 이름을 쓰지 않는지 `agents pipeline validate --all-referenced` 로 먼저 확인한다(5절).

## 2. 버전 문서 스키마

### 2.1 컬렉션

| 컬렉션 | 문서 | 인덱스 | 쓰기 |
|---|---|---|---|
| `agent_pipeline_versions` | 버전 하나. 조립·프롬프트·라우트·조절 값 전부 | `_id`(버전 id), `content_hash`, `created_at` | `insert_one` 만. 갱신·삭제 메서드를 리포지토리에 두지 않는다. 활성은 순번이 가장 큰 문서다(2.7절) |
| `agent_pipeline_counters` | `{_id: "version", seq}` | `_id` | `find_one_and_update($inc)`. 버전 id 순번 (`glossary_counters` 와 같은 방식) |

파이프라인 컬렉션은 검색 서비스와 같은 Mongo·같은 DB 다(D-43). CloudType 러너와 로컬 CLI 가 같은 문서를 본다. `agent_routes` 는 3단계 이행이 끝나면 쓰지 않는다(7절).

크기. v4 템플릿 전부가 약 72KB, v3 평면이 약 29KB 다. 라우트·조절 값·단계 목록은 수 KB 다. 버전 하나가 100KB 안팎이라 16MB 한도와 무관하고, 100 버전이 쌓여도 수십 MB 다. 정리 정책은 두지 않는다(Q-39).

### 2.2 버전 문서

```
PipelineVersion                         agent_pipeline_versions 문서
 ├─ _id: "v7"                        버전 id. "v" + 순번(Q-36). 프롬프트 디렉터리 v1~v4 는 3단계에서 없어지고, 글 버전(DraftVersion 의 v1~v3)과는 문맥이 달라 겹치지 않는다
 ├─ schema_version: 2                    문서 형식. 1 = 1단계(stages 없음), 2 = 2단계부터
 ├─ parent_version_id: "v6" | null   어느 버전에서 파생했는지. diff 의 기본 비교 대상
 ├─ content_hash: "sha256:…"             meta 를 뺀 본문의 정규화 JSON(키 정렬) 해시. 같은 내용을 알아보는 정보다. unique 가 아니다(되돌리기가 같은 내용의 새 버전을 만든다)
 ├─ label: "terminology-rules" | null    사람이 부르는 이름. 선택
 ├─ note: "…"                            왜 만들었는지 한 줄
 ├─ created_at, created_by               CLI 사용자 또는 "bootstrap"
 ├─ source: "bootstrap" | "import" | "edit-prompt" | "edit-param" | "edit-routes"
 ├─ slots: {A: "architect", B: "systems_engineer", C: "troubleshooter"}      ← agent_routes 의 SLOTS
 ├─ personas: {persona_id → {display_name, writer_bias, primary_lens, max_level_without_evidence?}}   ← agents/personas.py, parsers._MAX_LEVEL_WITHOUT_EVIDENCE
 ├─ routes: {route_id → [CallModel…]}    ← agent_routes 의 라우트 문서들. 키 없는 벤더 후보도 들어 있다(라운드 생성 때 뺀다)
 ├─ level_criteria: LevelCriteria        ← agent_routes 의 LEVEL_CRITERIA 또는 models/level.DEFAULT_LEVEL_CRITERIA
 ├─ prompts: {family → {template_name → 본문}}   family ∈ claude·gemini·grok·haiku·common. 평면 배치(v1~v3)는 common 에 넣는다
 ├─ parameters: Parameters              2.4절
 ├─ draft_stages: [Stage…]              2.3절. 글 코루틴의 단계. 배열 순서가 POST_REVIEW 하위 단계의 순서다
 └─ round_stages: [Stage…]              주제 선정 코루틴의 단계
```

해시는 본문(`schema_version`·`slots`·`personas`·`routes`·`level_criteria`·`prompts`·`parameters`·`draft_stages`·`round_stages`)만 넣는다. meta(`_id`·`parent_version_id`·`content_hash`·`label`·`note`·`created_*`·`source`)는 넣지 않는다. 최신 버전과 본문이 같은 들여오기는 그 id 를 알려 주고 거절한다. 아무것도 바꾸지 않는 버전을 만들지 않기 위해서다. 더 옛 버전과 같은 본문은 되돌리기이므로 허용하고 `note` 에 그 id 를 적는다.

### 2.3 단계(`Stage`)

```
Stage
 ├─ id: "write_draft"                    문서 안에서 유일. 로그·영수증·제작 기록의 단계 이름
 ├─ at: {status: PostStatus, stage?: PostReviewStage}   어느 상태에서 도는지. POST_REVIEW 는 하위 단계도 적는다
 │        라운드 단계는 {round_status: "TOPIC_SELECTION"}
 ├─ when: [condition…]                   전부 참이어야 고른다. 비어 있으면 그 상태의 유일한 단계
 ├─ kind: "transition" | "gate" | "llm_call" | "fan_out" | "procedure"
 ├─ on_failure: "fail" | "skip"          재시도 소진·예산 초과·잘림 뒤. fail = FAILED(03 문서 5·5a 행), skip = 결과 없이 다음으로(처리기가 skippable 이어야 한다)
 ├─ retries: 3                           한 호출의 시도 수. 없으면 parameters.max_llm_retries
 ├─ timeout: "draft" | "review"          core/config.TIMEOUTS 의 draft_s·review_s 중 어느 것인지(지금 _SHORT_KINDS, D-84)
 ├─ budget: true                         글 예산 검사 여부. 주제 제안만 false
 └─ kind 별 필드
     transition : apply
     gate       : gate, apply
     llm_call   : select, route?, builder, template, schema, check?, apply, call_kind
     fan_out    : items, branches: [{when, llm_call | procedure}], on_complete: {apply}, call_kind
     procedure  : procedure, slots: {이름 → 라우트·템플릿·계약·검사·선택기 이름}
```

`call_kind` 는 영수증 `AgentCall.kind` 에 적을 값이다(`CallKind` 열거형). 지표·제작 기록이 이 값으로 단계를 가르므로 데이터가 고를 수 있어야 하지만, 열거형 밖 값은 검증이 거절한다.

종류의 실행 방식은 코드다.

| `kind` | 실행기가 하는 일 | 지금 코드 |
|---|---|---|
| `transition` | `apply` 를 부르고 저장 | `_step` 의 `TOPIC_LOCKED \| REVISING` 분기 |
| `gate` | `gate` 로 오류 목록을 얻어 `apply` 에 넘기고 저장 | `_validate` |
| `llm_call` | `select` 로 모델 → `builder` 로 입력 → `template`(가족은 모델로) 렌더 → `call(schema, check, retries, timeout, budget)` → `apply(output, model, next)` → 저장. 실패는 `on_failure` | `_write`, `_final_read`, `_assess_level` |
| `fan_out` | `items` 마다 첫 일치 분기를 골라 병렬(`_gather`) → 성공분을 `apply` → 실패가 있으면 `on_failure`(항상 `fail`: 성공분을 저장한 뒤 FAILED, 04 문서 6절) → 전부 모였으면 `on_complete.apply` | `_review`, `_arbitrate`, `_propose` |
| `procedure` | 절차 코루틴에 `slots` 를 꽂아 부른다. 절차가 처리기까지 부른다 | `polish`, `glossary.extract`, `publisher.publish`, `_call_fact_review`, `_call_arbitrate` |

### 2.4 조절 값(`Parameters`)

| 묶음 | 필드 | 지금 위치 |
|---|---|---|
| 반복 상한 | `max_iterations`, `max_validation_retries`, `max_llm_retries`, `max_publish_attempts`, `max_topic_attempts` | `Settings` |
| 예산 | `draft_token_limit` | `Settings` (Q-07) |
| 주제 | `topic_keyword_overlap`, `topic_title_overlap`, `topic_avoid_lookback_days` | `Settings` |
| 구조 | `structure.required_sections`, `structure.sections_optional_by_level`(`{"100": ["tradeoff"]}`), `structure.anchor_format`, `structure.forbidden_syntax`, `structure.language`, `draft_min_chars`, `draft_max_chars`, `draft_reject_chars`, `min_code_blocks` | `models/sections.REQUIRED_SECTIONS`·`required_sections_for`, `StructureRules` 기본값, `Settings`, `ValidationGate` 인자 |
| 리뷰 | `review.tone_preservation.may_flag`·`must_not_flag` | `TonePreservationRule` 기본값 |
| 사실 확인 | `fact_check.severity`: `{REFUTED: CRITICAL, UNVERIFIABLE: MINOR}` (Q-19 가 데이터 변경이 된다) | `parsers.claim_items` 상수 |
| 전체 읽기 | `final_read.blocking_kinds`: `[BODY_CODE_CONTRADICTION, CORE_MECHANISM_MISSING]` | `FinalReadKind.is_blocking` |
| 다듬기 | `polish.length_slack_chars`, `polish.length_slack_ratio` | `polishing.py` 상수. 정규식 어미 치환(`_PHRASE_FIXES`)과 긴 문단 알림은 없앤다(D-81) |
| 발행 | `publish.requires_approval`, `publish.note`(글 끝 표기의 제목 `heading`, 줄 문구 `writer`·`reviewers`·`revisers`·`arbiters`·`polishers` 와 `lens_labels`) | `Settings.publish_requires_approval`, `publishing.py` 의 머리말 템플릿(05 문서 5절). 형식은 D-80·D-87 로 줄였다 |
| 용어 | `glossary.full_list_max`, `glossary.min_terms`, `glossary.max_terms`, `glossary.surface_max_chars`, `glossary.terminology_max` | `Settings` |
| 문체 | `style.minority_register_ratio`(0.1), `style.minority_register_min`(3), `style.forbid_imperatives`, `structure.prose_register`(코드 기본 None, 운영 `polite`) | 신설(D-85·D-86). 어미는 프롬프트로 고정하지 않고 게이트가 서술문 어미 분포와 기대 문체를 잰다. 기대 문체는 `structure_rules` 데이터로 모델에 알린다 |
| 코드 주석 | `code_comments.min_lines`(12), `code_comments.lines_per_comment`(25) | 신설(D-85). 긴 코드 블록의 설명 주석 수 |
| 프롬프트 lint(선택) | `prompt_lint`: 템플릿 이름 → 있어야 하는 구절 목록(작성·수정의 어미·길이 예산·표기 규칙 문구). R-08 이 경고로 검사한다(Q-38) | `tests/agents/prompts/test_registry.py` 의 문구 단언 |

글 끝 표기(D-80·D-87, Q-34)는 본문 뒤에 `---`, `## AI Information` 제목, "역할: 모델" 목록으로 붙는다. 작성·리뷰 줄은 항상, 수정 줄은 작성 모델과 다른 모델이 고쳤을 때, 중재·다듬기 줄은 있었을 때만이다. 같은 모델이 렌즈 둘을 맡으면 `모델(렌즈, 렌즈)` 로 한 번 적는다. 버전 수·차단 건수·기각 인용은 넣지 않는다. 그것은 제작 기록(08 문서 3절)이 보인다. 줄 문구에 `$models` 가 없으면 R-09 오류다.

```markdown
---

## AI Information

- 작성: claude-opus-5-5
- 리뷰: grok-4.7(논리·가독성, 비판·실무), gemini-4-argon(사실·구조)
- 중재: gemini-4-argon
```

`Settings`(환경 변수)에 남는 것은 비밀과 주소뿐이다: 벤더 키, Mongo, `POST_BE_URL`, `USER_SERVICE_URL`, 발행 계정, `AGENTS_INTERNAL_API_KEY`. `prompt_version` 은 버전 문서로, `agent_main_categories` 는 삭제(Post BE 의 활성 카테고리가 기준, D-82), `publish_post_level` 은 삭제(수준을 항상 보낸다, D-83)다. 시간(작성·리뷰·발행 타임아웃)과 동시성(벤더당 동시 요청 수, 429 뒤 냉각 시간, 스캔 주기)은 환경 변수가 아니라 `core/config.py` 의 불변 객체 두 개에 상수로 모은다(D-84). 값마다 전역 변수나 모듈 상수를 흩어 두지 않는다.

```python
# app/core/config.py (설계 스케치)
@dataclass(frozen=True)
class Timeouts:
    draft_s: float = 600       # 주제 제안·작성·수정
    review_s: float = 300      # 리뷰·중재·전체 읽기·수준 평가·다듬기·변환·용어 추출
    publish_s: float = 30      # 로그인·글 생성 HTTP

@dataclass(frozen=True)
class Concurrency:
    per_provider: int = 2          # 벤더별 동시 요청 수(세마포어)
    rate_limit_cooldown_s: int = 30  # 429 뒤 1건으로 줄이는 시간. 지금 agent_calls._RATE_LIMIT_COOLDOWN_S
    scan_interval_s: int = 15      # 러너 스캔 주기

TIMEOUTS = Timeouts()
CONCURRENCY = Concurrency()
```

테스트는 인스턴스를 바꿔 넣는다(`replace(TIMEOUTS, review_s=1)`). 단계 문서의 `timeout: "draft" | "review"` 는 이 객체의 어느 값을 쓰는지만 고른다. 시간·동시성은 환경(네트워크·요금)의 성질이고, 배포 사실은 다른 저장소의 배포 상태라 파이프라인 버전과 수명이 다르다(Q-33).

### 2.5 예시 JSON (축약)

```json
{
  "_id": "v7",
  "schema_version": 2,
  "parent_version_id": "v6",
  "content_hash": "sha256:3f9c…",
  "label": "terminology-rules",
  "note": "작성 프롬프트에 용어 표기 규칙 보강(D-62 2차 비교 뒤)",
  "created_at": "2026-11-07T09:12:00Z",
  "created_by": "cli:owner",
  "source": "edit-prompt",
  "slots": {"A": "architect", "B": "systems_engineer", "C": "troubleshooter"},
  "personas": {
    "architect": {"display_name": "아키텍처와 경계", "writer_bias": "Module boundaries, …", "primary_lens": "LOGIC_READABILITY"},
    "systems_engineer": {"display_name": "시스템 내부 동작", "writer_bias": "Database execution plans, …", "primary_lens": "FACT_STRUCTURE"},
    "troubleshooter": {"display_name": "엣지 케이스와 실패 모드", "writer_bias": "Public failure postmortems, …", "primary_lens": "CRITICAL_PRACTICAL", "max_level_without_evidence": 200}
  },
  "routes": {
    "WRITE_DRAFT:architect": [{"provider": "anthropic", "model_id": "claude-opus-5-5", "effort": "high", "max_output_tokens": 24000}],
    "REVIEW:FACT_STRUCTURE": [
      {"provider": "google", "model_id": "gemini-4-argon", "effort": "high", "max_output_tokens": 16000},
      {"provider": "anthropic", "model_id": "claude-opus-5-5", "effort": "high", "max_output_tokens": 16000},
      {"provider": "xai", "model_id": "grok-4.7", "effort": "high", "max_output_tokens": 16000}
    ],
    "FACT_TRANSFORM": [{"provider": "anthropic", "model_id": "claude-haiku-5-5", "effort": "low", "max_output_tokens": 8000}],
    "GLOSSARY": [{"provider": "anthropic", "model_id": "claude-haiku-5-5", "effort": "low", "max_output_tokens": 8000}]
  },
  "level_criteria": {"version": "v1", "criteria": ["…"], "reference_posts": []},
  "prompts": {
    "claude": {"write_draft": "<task>\nWrite a complete Korean technical article…", "review_full": "…"},
    "gemini": {"write_draft": "…"},
    "grok": {"write_draft": "…"},
    "haiku": {"fact_transform": "…", "glossary": "…"},
    "common": {"arbitrate_search": "…"}
  },
  "parameters": {
    "max_iterations": 2, "max_validation_retries": 2, "max_llm_retries": 3, "max_publish_attempts": 3, "max_topic_attempts": 2,
    "draft_token_limit": 300000,
    "topic_keyword_overlap": 0.5, "topic_title_overlap": 0.5, "topic_avoid_lookback_days": 180,
    "draft_min_chars": 4000, "draft_max_chars": 20000, "draft_reject_chars": 25000, "min_code_blocks": 1,
    "structure": {"required_sections": ["intro", "body-1", "tradeoff"], "sections_optional_by_level": {"100": ["tradeoff"]},
                  "anchor_format": "<!-- sec:{id} -->", "forbidden_syntax": ["HTML tags (except section anchors)", "Images (![alt](url))", "Footnotes", "mermaid code blocks"], "language": "ko"},
    "review": {"tone_preservation": {"may_flag": ["…"], "must_not_flag": ["…"]}},
    "fact_check": {"severity": {"REFUTED": "CRITICAL", "UNVERIFIABLE": "MINOR"}},
    "final_read": {"blocking_kinds": ["BODY_CODE_CONTRADICTION", "CORE_MECHANISM_MISSING"]},
    "polish": {"length_slack_chars": 30, "length_slack_ratio": 0.25},
    "publish": {"requires_approval": false,
                "note": {"writer": "작성 AI - $models", "reviewers": "리뷰 AI - $models", "revisers": "수정 AI - $models",
                         "arbiters": "중재 AI - $models", "polishers": "다듬기 AI - $models",
                         "lens_labels": {"LOGIC_READABILITY": "논리·가독성", "FACT_STRUCTURE": "사실·구조", "CRITICAL_PRACTICAL": "비판·실무"}}},
    "glossary": {"full_list_max": 300, "min_terms": 8, "max_terms": 12, "surface_max_chars": 40, "terminology_max": 150}
  },
  "round_stages": [
    {"id": "propose", "at": {"round_status": "TOPIC_SELECTION"}, "kind": "fan_out", "items": "pending_slots", "call_kind": "PROPOSE_TOPIC",
     "branches": [{"llm_call": {"select": "proposer", "builder": "propose_topic", "template": "propose_topic",
                                "schema": "ProposeTopicOutput", "check": "topic_proposal", "apply": "record_proposals"}}],
     "on_failure": "fail", "timeout": "draft", "budget": false},
    {"id": "assign_and_commit", "at": {"round_status": "TOPIC_SELECTION"}, "kind": "procedure", "procedure": "assign_and_commit", "slots": {}}
  ],
  "draft_stages": [
    {"id": "start_drafting", "at": {"status": "TOPIC_LOCKED"}, "kind": "transition", "apply": "start_drafting"},
    {"id": "start_revising", "at": {"status": "REVISING"}, "kind": "transition", "apply": "start_drafting"},
    {"id": "write_draft", "at": {"status": "DRAFTING"}, "when": ["is_first_draft"], "kind": "llm_call", "call_kind": "WRITE_DRAFT",
     "select": "writer", "builder": "write_draft", "template": "write_draft", "schema": "DraftOutput", "apply": "submit_version",
     "on_failure": "fail", "timeout": "draft"},
    {"id": "revise_draft", "at": {"status": "DRAFTING"}, "when": ["is_revision"], "kind": "llm_call", "call_kind": "REVISE_DRAFT",
     "select": "reviser", "builder": "revise_draft", "template": "revise_draft", "schema": "RevisionOutput", "apply": "submit_version",
     "on_failure": "fail", "timeout": "draft"},
    {"id": "validate", "at": {"status": "VALIDATING"}, "kind": "gate", "gate": "draft", "apply": "gate_outcome"},
    {"id": "review", "at": {"status": "IN_REVIEW"}, "kind": "fan_out", "items": "pending_reviewers", "call_kind": "REVIEW",
     "branches": [
       {"when": ["checks_facts"], "procedure": "fact_review",
        "slots": {"search_select": "reviewer", "search_template": "fact_check", "transform_route": "FACT_TRANSFORM",
                  "transform_template": "fact_transform", "schema": "FactCheckOutput", "check": "fact_output", "apply": "add_review"}},
       {"when": ["plain_review"], "llm_call": {"select": "reviewer", "builder": "review_full", "template": "review_full",
                                               "schema": "ReviewOutput", "check": "review_output", "apply": "add_review"}}
     ],
     "on_complete": {"apply": "evaluate"}, "on_failure": "fail", "timeout": "review"},
    {"id": "spot_check", "at": {"status": "IN_SPOT_CHECK"}, "kind": "fan_out", "items": "pending_reviewers", "call_kind": "REVIEW",
     "branches": [
       {"when": ["checks_facts"], "procedure": "fact_review",
        "slots": {"search_select": "reviewer", "search_template": "fact_check", "transform_route": "FACT_TRANSFORM",
                  "transform_template": "fact_transform", "schema": "FactSpotCheckOutput", "check": "fact_spot_check_output", "apply": "add_review"}},
       {"when": ["plain_review"], "llm_call": {"select": "reviewer", "builder": "review_spot_check", "template": "review_spot_check",
                                               "schema": "SpotCheckOutput", "check": "spot_check_output", "apply": "add_review"}}
     ],
     "on_complete": {"apply": "evaluate"}, "on_failure": "fail", "timeout": "review"},
    {"id": "arbitrate", "at": {"status": "IN_ARBITRATION"}, "kind": "fan_out", "items": "pending_arbitration_items", "call_kind": "ARBITRATE",
     "branches": [{"procedure": "arbitrate_item",
                   "slots": {"select": "arbiter", "search_template": "arbitrate_search", "template": "arbitrate",
                             "schema": "ArbitrateOutput", "apply": "add_arbitration"}}],
     "on_complete": {"apply": "evaluate"}, "on_failure": "fail", "timeout": "review"},
    {"id": "polish", "at": {"status": "POST_REVIEW", "stage": "POLISH"}, "kind": "procedure", "procedure": "polish",
     "slots": {"select": "independent", "route": "POLISH_TONE", "template": "polish_tone", "schema": "PolishOutput", "apply": "polish_outcome"},
     "on_failure": "skip", "timeout": "review"},
    {"id": "final_read", "at": {"status": "POST_REVIEW", "stage": "FINAL_READ"}, "kind": "llm_call", "call_kind": "FINAL_READ",
     "select": "independent", "route": "FINAL_READ", "builder": "final_read", "template": "final_read", "schema": "FinalReadOutput",
     "check": "final_read", "apply": "record_final_read", "on_failure": "fail", "timeout": "review"},
    {"id": "assess_level", "at": {"status": "POST_REVIEW", "stage": "ASSESS_LEVEL"}, "kind": "llm_call", "call_kind": "ASSESS_LEVEL",
     "select": "independent", "route": "ASSESS_LEVEL", "builder": "assess_level", "template": "assess_level", "schema": "AssessLevelOutput",
     "check": "level_assessment", "apply": "record_level_assessment", "on_failure": "fail", "timeout": "review"},
    {"id": "glossary", "at": {"status": "POST_REVIEW", "stage": "GLOSSARY"}, "kind": "procedure", "procedure": "glossary_extract",
     "slots": {"select": "optional", "route": "GLOSSARY", "template": "glossary", "schema": "GlossaryOutput", "apply": "finish_glossary"},
     "on_failure": "skip", "timeout": "review"},
    {"id": "publish", "at": {"status": "READY_TO_PUBLISH"}, "kind": "procedure", "procedure": "publish", "slots": {}}
  ]
}
```

`publish.note` 의 `$models` 는 `string.Template` 치환이다. 어느 줄을 넣을지(작성·리뷰는 항상, 수정은 작성 모델과 다를 때, 중재·다듬기는 있을 때)는 `build_publish_note` 코드가 정하고 문구만 데이터다.

### 2.6 append-only 원칙

- 버전 문서는 만들 때만 쓴다. 리포지토리에 `update`·`replace`·`delete` 메서드를 두지 않는다. 고치려면 자식 버전을 만든다(`parent_version_id`).
- 활성은 최신이다. 포인터 문서가 없으므로 바뀌는 문서가 없다. 새 버전이 들어와도 진행 중인 라운드는 자기 버전을 계속 쓴다(2.8절).
- 되돌리기도 새 버전이다. git 에 있는 옛 내보내기를 다시 들여오면 그 본문을 복사한 새 버전이 생기고 부모는 직전 최신이다. 그래서 이력은 한 줄이다.
- 삭제하지 않는다. 라운드·영수증·제작 기록이 버전 id 를 가리키므로 지우면 과거 글의 조건을 잃는다. 정리는 Q-39.

### 2.7 활성 버전

활성 버전은 **순번이 가장 큰 버전**이다. 포인터 문서를 두지 않는다. 들여오기·`edit-*` 는 모두 새 버전을 만들고, 그 버전이 다음 라운드부터 쓰인다. 진행 중인 라운드는 자기 버전을 계속 쓴다(2.8절, D-73).

- 되돌리기 명령은 없다(Q-40). git 에 있는 옛 내보내기 디렉터리를 다시 `import` 하면 그 본문을 복사한 새 버전이 생긴다. git 의 revert 와 같다. 이력이 한 줄로 읽히고, 어느 라운드가 어느 내용으로 돌았는지는 버전 id 하나로 남는다.
- 버전이 하나도 없으면 라운드 생성은 `ValueError`(400) 로 거절하고, 러너의 옛 라운드 진행은 로그를 남기고 그 스캔을 건너뛴다. 저장소 파일을 대체값으로 두지 않는다(D-51 과 같은 원칙).
- 들여오기 전에 검증(4절)을 통과해야 들어가므로 최신 버전은 늘 검증된 버전이다. 코드에서 이름을 뺄 때는 배포 전에 최신 버전과 진행 중 라운드의 버전을 현재 레지스트리로 다시 검증한다(R-12).
- 환경을 가르지 않는다. 러너도 Mongo 도 하나이고, 로컬 CLI 가 만든 라운드도 CloudType 러너가 돌린다. 문구만 시험할 때는 `compare-prompts --version <디렉터리>` 로 들여오지 않고 써 본다. 차기 버전 프롬프트를 라운드까지 돌려 볼 때는 들여온 뒤(최신이 된다) `start-round` 로 시험 라운드를 만들고, 그동안 안정 버전으로 돌릴 라운드는 `start-round --pipeline-version v7` 처럼 버전을 고른다(Q-42). 나쁘면 옛 내보내기를 다시 들여온다.

### 2.8 지금 것과의 대응

| 지금 | 어떻게 되는가 |
|---|---|
| `agent_routes` 컬렉션(라우트·`SLOTS`·`LEVEL_CRITERIA`), `agents seed-routes` | 버전 문서의 `routes`·`slots`·`level_criteria`. 1단계 bootstrap 이 읽어 첫 버전을 만든다. 3단계 뒤 컬렉션·명령을 없앤다 |
| `RoutingSnapshot`(`models/routing.py`) | 유지한다. 라운드 생성 때 `routes` 에서 키 없는 벤더 후보를 빼고 A/B `writer_overrides` 를 얹은 **유효 스냅샷**이 `PublishingRound.routing` 에 그대로 남는다. 버전의 `routes` 는 입력, 라운드의 `routing` 은 그 라운드가 실제로 쓰는 것이다(D-51 의 "스냅샷" 의미 유지). `relaxed`·`legacy_snapshot` 은 옛 라운드용으로 남는다 |
| `PublishingRound` | `pipeline_version_id: str \| None` 추가. 생성 때 최신 버전 id 를 적는다. 글 코루틴은 라운드의 버전으로 단계·프롬프트·조절 값을 읽는다. 옛 라운드(`None`)는 최신 버전으로 돈다(지금도 배포가 바뀌면 진행 중 글의 프롬프트가 바뀌는 것과 같다) |
| `Settings.prompt_version`, `registry.PROMPT_VERSION`, `load_template(name, version, family)` | 없어진다. `load_template(version: PipelineVersion, name, family)` 가 `prompts[family]` → `prompts.common` 순으로 찾는다. 평면 디렉터리는 없다 |
| `render_messages(model, prompt_version, call_model)` | `render_messages(model, version, call_model, template_name)` |
| `AgentCall.prompt_template_version` | 새 영수증은 `pipeline_version`(`v7`)을 적고 `prompt_template_version` 은 비워 둔다. 옛 영수증의 `v3`·`v4` 는 프롬프트 디렉터리 이름이라 파이프라인 버전 `v3`·`v4` 와 겹치므로 필드를 나눈다(Q-36 의 결과). 제작 기록·`PostGlossary` 는 둘 중 있는 것을 보인다 |
| `PublishingRound.routing.level_criteria` | 그대로. 생성 때 버전의 `level_criteria` 를 복사한다 |
| `agents/personas.py`, `parsers._MAX_LEVEL_WITHOUT_EVIDENCE` | 버전의 `personas`. `get_persona(version, id)` 가 된다. `PersonaProfile` 모델은 그대로 |
| `Settings` 의 상수 열두 개 | `parameters`(2.4절). 서비스는 `settings` 대신 라운드 버전의 `parameters` 를 받는다. `ValidationGate` 는 조립 때 한 번이 아니라 버전으로 만든다(`ValidationGate.from_parameters`) |
| `PostReviewStage` 의 순서(`_advance_post_review` 하드코딩) | `draft_stages` 에서 `at.status == POST_REVIEW` 인 단계의 배열 순서. 열거형은 어휘로 남는다(모르는 하위 단계는 검증이 거절) |
| `.dockerignore` 의 `!app/agents/prompts/**/*.md` | 3단계 뒤 프롬프트가 이미지에 없다. 예외 줄을 없앤다 |
| `Settings.agent_main_categories`, `RoundService.start_round` 의 `category_options(…, main_values)` 거르기 | 없앤다. Post BE 의 활성 메인·서브 전부가 라운드 스냅샷이다. 화면 없는 메인은 Post BE 에서 비활성화한다(D-82) |
| `Settings.publish_post_level`, `build_payload(include_level=…)` | 없앤다. 수준 평가가 있는 글은 항상 `CreatePostDto.level` 을 보낸다. Post BE 가 그 필드를 받은 뒤 배포한다(D-83) |
| `Settings` 의 `*_timeout_s`·`scan_interval_s`·`llm_concurrency_per_provider`, `agent_calls._RATE_LIMIT_COOLDOWN_S` | `core/config.py` 의 `TIMEOUTS`·`CONCURRENCY` 불변 객체(2.4절). 환경 변수가 아니다(D-84) |

## 3. 실행기

### 3.1 일반 실행기

`DraftPipelineService._step` 의 `match draft.status` 를 버전의 단계 목록으로 바꾼다. `drive` 루프, `_should_run`, 저장 충돌 처리, `close_if_complete` 는 그대로다.

```
DraftPipelineService.drive(draft_id):                  # 루프는 그대로. 버전 읽기 한 줄이 더해진다
    loop:
        draft, round = 다시 읽기
        version = versions.get(round.pipeline_version_id or latest())      # 불변 문서라 id 로 프로세스 안 LRU 캐시
        if not _should_run(draft, round, version.parameters): return       # awaits_approval 이 publish.requires_approval 을 읽는다
        try: draft = await executor.step(draft, round, version)
        except ConcurrentUpdateError: continue
        if draft.is_terminal: await rounds.close_if_complete(round.round_id)

PipelineExecutor.step(draft, round, version):
    stage = version.stage_for(draft)                   # at(status, post_review_stage) 일치 + when 전부 참. 0개나 2개 이상이면 ValueError
    params = version.parameters
    match stage.kind:
        case "transition": APPLIERS[stage.apply](draft, params)
        case "gate":       errors = GATES[stage.gate](draft, params); APPLIERS[stage.apply](draft, errors, params)
        case "llm_call":   await self._llm_call(draft, round, version, stage)
        case "fan_out":    await self._fan_out(draft, round, version, stage)
        case "procedure":  procedure = PROCEDURES[stage.procedure]
                           await procedure.fn(ctx(draft, round, version, stage), stage.slots)
                           if procedure.saves_itself: return draft           # publish 만. 의도·결과를 스스로 저장한다
    await drafts.save(draft)
    return draft

_llm_call(draft, round, version, stage):
    model = SELECTORS[stage.select](routing(round), draft, stage.route)
    if model is None and stage.on_failure == "skip": APPLIERS[stage.apply](draft, None, next_of(version, stage)); return
    input = BUILDERS[stage.builder](draft, round, version)      # 빌더는 version 에서 level_criteria·구조 규칙·톤 목록을 읽는다
    messages = render_messages(input, version, model, stage.template)
    try:
        result = await calls.call(kind=stage.call_kind, binding=model, messages=messages, schema=SCHEMAS[stage.schema],
                                  check=CHECKS[stage.check](...) if stage.check else None,
                                  retries=stage.retries or params.max_llm_retries, timeout=stage.timeout, budget=stage.budget,
                                  pipeline_version=version.version_id, …)
    except _STEP_FAILURES as exc:
        if stage.on_failure == "skip": APPLIERS[stage.apply](draft, None, next_of(version, stage), skipped=str(exc)); return
        _fail(draft, [exc]); return                            # 지금 _fail 그대로
    APPLIERS[stage.apply](draft, result.output, model, result.call_id, next_of(version, stage))

_fan_out(draft, round, version, stage):
    items = ITERATORS[stage.items](draft)
    results, failures = await _gather([self._branch(draft, round, version, stage, item) for item in items])   # 지금 _gather 그대로
    for result in results: APPLIERS[branch_apply](draft, result)
    if failures: _fail(draft, failures)                        # 성공분을 저장한 뒤 FAILED (04 문서 6절)
    elif complete(draft): APPLIERS[stage.on_complete.apply](draft, params, first_post_review_stage(version))
```

`_branch` 는 항목에 대해 `when` 이 전부 참인 첫 분기를 고른다. 분기가 `llm_call` 이면 `_llm_call` 과 같은 길이고, `procedure` 면 절차에 항목을 넘긴다. 어느 분기도 맞지 않으면 `ValueError` 이고 `_gather` 가 그 항목의 실패로 모아 `FAILED(INTERNAL_ERROR)` 가 된다.

### 3.2 지금 흐름과의 대응

| 지금 `_step` 분기 | 지금 메서드 | 단계 문서 |
|---|---|---|
| `TOPIC_LOCKED \| REVISING` → `start_drafting` | `_step` | `start_drafting`·`start_revising`(`transition`, `apply: start_drafting`) |
| `DRAFTING` → `revision_cause` 로 `WRITE_DRAFT`·`REVISE_DRAFT` | `_write` | `write_draft`(`when: is_first_draft`)·`revise_draft`(`when: is_revision`) |
| `VALIDATING` → 게이트 | `_validate` | `validate`(`gate: draft`) |
| `IN_REVIEW \| IN_SPOT_CHECK` → 미응답 렌즈 병렬, 사실 렌즈는 검색 경로 | `_review`, `_call_review`, `_call_fact_review` | `review`·`spot_check`(`fan_out`, 분기 `checks_facts` → `fact_review`, `plain_review` → `llm_call`) |
| `IN_ARBITRATION` → 교착 항목 병렬, 사실 지적은 검색 먼저 | `_arbitrate`, `_call_arbitrate` | `arbitrate`(`fan_out`, 분기 하나 `arbitrate_item`. 검색 여부는 절차 안의 `from_fact_check`) |
| `POST_REVIEW` → `post_review_stage` 로 네 단계 | `_post_review`, `PolishingService.polish`, `_final_read`, `_assess_level`, `GlossaryService.extract` | `polish`·`final_read`·`assess_level`·`glossary`(`at.stage`). 순서는 배열 순서 |
| `READY_TO_PUBLISH` → 발행 | `_publisher.publish` | `publish`(`procedure`) |
| 그 외 → `ValueError` | `_step` | `stage_for` 가 0개면 `ValueError` → 러너가 `FAILED(INTERNAL_ERROR)` |

`DraftPublisher` 계약과 `UnconfiguredDraftPublisher` 는 `publish` 절차의 구현체로 남는다. `drain` 도 같다.

### 3.3 불변식이 코드에 남는 방식

| 불변식(03·04 문서) | 데이터화 뒤 보장 |
|---|---|
| 상태 전이는 `PostDraft` 메서드만 한다. 잘못된 전이는 `InvalidTransitionError` | 처리기가 전부 도메인 메서드다. 실행기는 `status` 를 쓰지 않는다. 단계 문서가 `at: DRAFTING` 에 `record_final_read` 를 붙이면 메서드의 `_require` 가 던지고 글은 `FAILED(INTERNAL_ERROR)` 다. 검증 R-06 이 처리기의 `at` 호환표로 시드 때 먼저 잡는다 |
| 한 걸음 = 호출 → 반영 → 저장 한 번. `revision` 낙관적 잠금 | `step` 끝의 `drafts.save` 하나다. `publish` 절차만 예외(의도·결과를 스스로 저장, 지금과 같다) |
| 중단 뒤 재개: 상태와 포인터(`post_review_stage`, `TopicAttempt`, `pending_*`)로 끝낸 호출을 되풀이하지 않는다 | 포인터는 그대로 글 문서에 있다. 라운드가 버전을 고정하므로 재개 때도 같은 단계 목록을 본다. 새 버전이 들어와도 진행 중 글의 단계·프롬프트·조절 값은 바뀌지 않는다(지금보다 강하다: 지금은 배포가 프롬프트를 바꾼다) |
| 게이트는 LLM 앞의 결정론적 검사 | `gate` 종류와 `ValidationGate` 코드. 임계값만 조절 값이다 |
| 병렬 단계의 실패: 형제를 기다려 성공분을 저장한 뒤 `FAILED`, 재개하면 빠진 호출만 | `fan_out` 종류의 코드(`_gather` + `_fail`). `on_failure` 는 `fail` 만 허용한다(R-07) |
| 다듬기·용어 추출 실패는 글을 멈추지 않는다 | `on_failure: skip`. 처리기가 `skippable` 이어야 한다(R-07). 어느 단계가 건너뛰어도 되는지가 데이터가 된다. 전체 읽기를 `skip` 으로 두면 R-07 이 거절한다(`record_final_read` 는 skippable 이 아니다) |
| 라운드 중단은 호출을 끊지 않고 결과를 저장 충돌로 버린다 | `drive` 루프 그대로 |
| 사람 결정은 코루틴이 돌지 않는 상태에만 쓴다 | `DraftDecisionService` 그대로. `publish.requires_approval` 은 라운드 버전의 조절 값을 읽는다(러너 스캔의 `awaits_approval` 도 같다) |
| 예산·재시도·타임아웃으로 모든 단계가 끝난다 | 호출 서비스 그대로. `retries`·`budget`·`timeout` 분류만 단계 문서에서 받는다 |
| `FAILED` 재개는 `failure.from_status` 로 돌아간다 | 그대로. 그 상태의 단계를 다음 걸음이 고른다 |

### 3.4 실행 조건의 표현과 한계

- `when` 은 조건 **이름의 목록**이고 전부 참이어야 한다(AND). 부정·OR·비교식은 없다. 부정은 레지스트리가 쌍으로 선언한 반대 조건을 쓴다(`is_first_draft`·`is_revision`). 식 언어를 두면 해석기가 되고, 지금 분기는 전부 2~3갈래라 이름으로 충분하다(Q-41).
- 새 조건은 코드다. 예를 들어 "수준 100 글은 다듬기를 건너뛴다" 는 `is_level_100` 조건을 코드에 더한 뒤 `polish` 단계에 `when` 을 붙인다. 조건 자체는 순수 함수라 테스트가 쉽다.
- `FACT_STRUCTURE` 렌즈가 항상 검색 경로인 것은 `checks_facts` 조건과 `fact_review` 절차의 조합이다. 검색을 끄고 평범한 리뷰로 돌리려면 그 분기를 지우면 되지만, 그러면 `fact_check` 템플릿이 쓰이지 않는다는 R-05 경고가 난다.
- 조건은 글 하나(또는 라운드, 항목)만 본다. DB 를 읽는 조건은 두지 않는다. 등록 확인처럼 조회가 필요한 판단은 절차 안의 코드다(D-65 와 같은 이유).
- 같은 `at` 의 단계가 둘 이상이면 `when` 으로 갈라야 하고, 검증은 배타 쌍이 들어 있는지 본다(R-03). 배타 쌍이 아닌 조건 조합은 실행 때 두 단계가 맞을 수 있으므로 거절한다.

### 3.5 주제 선정 코루틴

`TopicSelectionService._step` 도 `round_stages` 를 본다. `propose` 는 `fan_out`(항목 `pending_slots`, 분기 하나)이고, `assign_and_commit` 은 절차다. 중복 판별·배정 조합·확정 쓰기 순서(D-13, D-28)는 절차 코드가 갖고 임계·상한만 조절 값이다. 제안 호출의 실패는 `publishing_round.abort(LLM_FAILED)` 로 지금과 같다. 라운드 코루틴은 라운드 문서 하나라 `at` 은 `round_status` 뿐이고, 단계 선택은 "잠기지 않은 슬롯이 있으면 `propose`, 없으면 `assign_and_commit`" 인데 이것을 조건 `has_pending_slots`·`all_slots_proposed` 로 적는다.

### 3.6 옛 라운드

- `pipeline_version_id` 가 없는 라운드: 최신 버전으로 돈다. `routing` 이 없는 더 옛 라운드는 `legacy_snapshot` 으로 유효 스냅샷을 만든다(지금과 같다).
- 슬롯 리뷰어 글(D-55 이전): `checks_facts` 가 거짓이라 `plain_review` 분기로 간다. 지금과 같다.
- 라운드가 가리키는 버전의 `schema_version` 이 1(단계 없음)이면 2단계 코드는 코드의 기준 단계 목록(7절의 `baseline_stages()`)을 쓴다. 3단계에서 기준 생성기를 없앨 때는 `schema_version: 1` 을 가리키는 진행 중 라운드가 없어야 한다(`agents pipeline validate --all-referenced`).

## 4. 검증 (`agents pipeline validate`)

시드(`bootstrap`·`import`·`edit-*`) 때 돈다. 하나라도 오류면 쓰지 않는다. 경고는 찍고 진행한다. 지금의 `validate_snapshot`(끝까지 고를 수 있는지)과 `validate_prompts`(가족마다 템플릿이 있는지)는 각각 R-04·R-05 로 흡수되고, `tests/agents/prompts/test_registry.py` 의 템플릿 불변식은 R-05·R-08 이 된다.

| 규칙 | 검사 | 실패 메시지 예 |
|---|---|---|
| R-01 이름 | `kind`·`apply`·`gate`·`builder`·`schema`·`check`·`select`·`procedure`·`items`·`when`·`call_kind`·`at.status`·`at.stage` 가 레지스트리·열거형에 있다 | `stage review: unknown condition "check_facts" (did you mean checks_facts?)` |
| R-02 종류 필드 | `kind` 별 필수 필드가 있고 다른 종류의 필드가 없다(종류마다 Pydantic 모델) | `stage validate: gate stage has no "gate"` |
| R-03 상태 덮기 | 글이 코루틴으로 도는 상태(`TOPIC_LOCKED`·`DRAFTING`·`VALIDATING`·`IN_REVIEW`·`IN_SPOT_CHECK`·`IN_ARBITRATION`·`REVISING`·`POST_REVIEW` 의 각 하위 단계·`READY_TO_PUBLISH`)마다 단계가 하나 이상 있다. 같은 `at` 의 단계가 둘 이상이면 `when` 이 레지스트리의 배타 쌍으로 갈린다. `POST_REVIEW` 하위 단계는 중복 없고 `GLOSSARY` 가 있으면 `POLISH` 뒤다(08 문서: 표면형은 발행본 기준) | `status DRAFTING: stages write_draft and revise_draft are not told apart by an exclusive pair` |
| R-04 라우트 | 모든 선택기가 볼 라우트 id(`Selector.route_ids(slots)`)가 `routes` 에 있고 후보가 비지 않는다. `optional` 은 없어도 된다. 세 벤더로 끝까지 고를 수 있다(지금 `validate_snapshot`: 페르소나마다 작성·수정·리뷰 셋·중재·독립 라우트). `candidate_errors`(지원표)가 비어 있다 | `route REVIEW:FACT_STRUCTURE has no candidate outside writing vendor google` |
| R-05 템플릿 | 단계가 가리키는 템플릿이, 그 단계의 선택기가 고를 수 있는 모든 후보의 가족(`prompt_family`)에 있다(`prompts[family]` 또는 `common`). 지금 `validate_prompts`. 어느 단계도 가리키지 않는 템플릿은 경고 | `stage final_read: template final_read for grok (grok-4.7) is missing` |
| R-06 타입 연결 | `schema` 가 `check.accepts` 와 `apply.accepts` 에 있다. `builder` 의 입력 모델이 `template` 의 기본 대응(`_TEMPLATE_BY_INPUT`)과 다르면 경고(의도한 실험일 수 있다). `apply` 가 `at.status` 에서 허용되는 처리기다(처리기마다 `allowed_at` 표). `fan_out` 의 `items` 가 돌려주는 항목 타입이 분기의 조건·절차가 받는 타입과 같다 | `stage assess_level: check final_read does not accept AssessLevelOutput` |
| R-07 실패 정책 | `on_failure: skip` 은 처리기(또는 절차)가 `skippable` 일 때만. `fan_out`·`gate`·`transition`·`publish` 는 `fail` 만. `retries ≥ 1` | `stage final_read: record_final_read cannot be skipped` |
| R-08 템플릿 내용 | 구조화 출력 호출의 템플릿에 "JSON" 이 있다. `$writer_bias` 는 `propose_topic` 템플릿에만 있고 그 밖의 `$` 가 없다(`$display_name` 금지). 가족 키는 `claude`·`gemini`·`grok`·`haiku`·`common` 만. 작성·수정 템플릿의 어미·길이 예산·표기 규칙 문구(지금 `test_v4_write_and_revise_templates_keep_the_register_and_length_budget`)는 "필수 구절" 목록으로 검사한다. 기본 목록은 코드에 있고 `parameters.prompt_lint` 의 목록을 더해 합친다. 문구와 lint 가 같은 문서에 있어 문구를 고치며 lint 를 지울 수 있으므로 데이터에서 빼도 기본 검사는 남는다(Q-38, 원 세션 검토) | `prompts.claude.write_draft: stray "$" at offset 1203` |
| R-09 조절 값 | `draft_min_chars < draft_max_chars ≤ draft_reject_chars`, `min_code_blocks ≥ 0`, 상한 전부 `≥ 1`(`max_iterations ≥ 1`: 03 문서 14행이 방어 행이 되는 조건), 임계는 0~1, `structure.required_sections` 에 `intro`·`body-1` 포함, `fact_check.severity` 의 키는 `ClaimStatus`·값은 `IssueSeverity`, `final_read.blocking_kinds ⊂ FinalReadKind`, `publish.note` 치환자는 `$models` 만 | `parameters.max_iterations must be at least 1` |
| R-10 슬롯·페르소나 | `slots` 에 A·B·C 전부, 페르소나 셋이 서로 다르고 `primary_lens` 가 서로 다르다(D-19) | `slots B and C share persona troubleshooter` |
| R-11 해시·부모 | `content_hash` 가 본문과 같다(들여오기). `parent_version_id` 가 있으면 존재한다. 최신 버전과 해시가 같으면 거절하고 id 를 알려 준다. 더 옛 버전과 같으면 되돌리기라 경고로만 알려 준다 | `same content as the latest version v7` |
| R-12 참조 보존(`--all-referenced`) | 최신 버전과 진행 중 라운드(`TOPIC_SELECTION`·`IN_PROGRESS`)가 가리키는 모든 버전이 현재 코드의 레지스트리로 R-01~R-10 을 통과한다. 코드에서 이름을 빼기 전·배포 전에 돈다 | `v3 (round 3f2a…, IN_PROGRESS) uses condition is_level_100 which this build lacks` |
| R-13 설명문 파이프라인 | `prompts.expository` 와 `EXPOSITORY:*` 라우트가 둘 다 없으면 통과. 하나라도 있으면 아홉 단계 모두 프롬프트(`inputs`·`expected_output_schema` 가 있는 JSON 객체)와 라우트 후보가 있고, 모르는 단계 이름이 없다(D-99) | `prompts.expository.5_write is missing` |

검증은 `services/pipeline_validation.py` 의 순수 함수 `validate_version(version, registry) -> list[Finding]` 이다. `Finding(rule, level, message)` 를 돌려주고, CLI 는 오류가 있으면 종료 코드 1 이다. 라운드 생성 때도 최신 버전에 R-04·R-05 를 돌린다(지금 `create_round` 가 `validate_snapshot`·`validate_prompts` 를 부르는 것과 같다. 키 없는 벤더를 뺀 유효 스냅샷으로 다시 봐야 하기 때문이다).

## 5. CLI

`agents pipeline …` 하위 명령. 넣는 쪽만 둔다. 조회·되돌리기 명령과 API 는 두지 않는다(Q-40). `edit-prompt`·`edit-param` 은 내부 API 로도 연다(D-89, 06 문서 3절). 보는 것은 Mongo 를 직접 보거나 `export` 한 파일이고, diff 는 내보낸 디렉터리끼리 git 이 하고, 되돌리기는 옛 내보내기를 다시 `import` 하는 것이다. 모두 `_run_agents` 로 비동기 Mongo 를 연다.

| 명령 | 동작 | 출력 |
|---|---|---|
| `bootstrap [--routes mongo\|<file>] [--prompts-dir v4] [--note] [--dry-run]` | 1단계 전용. 저장소 프롬프트 디렉터리(가족 디렉터리 → `prompts[family]`, 평면 → `common`), `agent_routes`(또는 파일), `Parameters` 기본값, 코드 상수(페르소나·구조·톤·심각도·다듬기·머리말)로 첫 버전 `v1` 을 만들어 검증하고 넣는다. `--dry-run` 은 넣지 않고 해시·라우트·가족·조절 값과 검증 결과만 찍는다. 이미 버전이 있으면 거절 | 버전 id, 검증 결과 |
| `validate <dir\|file\|version> [--all-referenced]` | 4절 규칙. 디렉터리는 들여오기 형식(아래) | 규칙 id 별 오류·경고. 오류가 있으면 종료 코드 1 |
| `export [<version>] --out-dir pipeline/<label>/` | 디렉터리로 내보낸다(기본은 최신). git 으로 리뷰·보관하는 형식 | 쓴 파일 목록 |
| `import <dir> [--note] [--label]` | 디렉터리를 읽어 검증하고 새 버전으로 넣는다. 부모는 최신이다. 최신과 같은 해시면 거절 | 버전 id, 최신과의 diff 요약 |
| `edit-prompt <family> <name> --file <md> [--note]` | 최신을 부모로 템플릿 하나만 바꾼 자식 버전. 다음 라운드부터 쓰인다. 잦은 문구 조정은 이것으로 끝낸다 | 버전 id, 그 템플릿의 unified diff, 검증 결과 |
| `edit-param <key.path>=<value> […] [--note]` | 조절 값만 바꾼 자식 버전. 값은 JSON 으로 읽는다(`publish.requires_approval=false`, `max_iterations=3`) | 버전 id, 바뀐 키 |
| `edit-route <route_id> <provider/model_id> […] --max-output-tokens N [--effort E] [--note]` | 라우트 하나의 후보 목록만 바꾼 자식 버전(D-102). 나머지 라우트는 최신 것을 잇는다. 최신 버전에 없는 라우트 id 는 거절한다(새 라우트는 `edit-routes`·`cli.expository seed`). 설명문 단계 모델도 이것으로 바꾼다(`EXPOSITORY:5_write`). 내부 API `POST /pipeline/routes/{route_id}/` 도 같다 | 버전 id |
| `edit-routes --file <routes.json> [--note]` | `routes`·`slots`·`level_criteria` 만 바꾼 자식 버전. 지금 `seed-routes` 의 자리 | 버전 id, 바뀐 라우트 |
| `python -m cli.expository seed [--dir pipeline/expository] [--note]` | 설명문 파이프라인의 `prompts/{단계}.json`·`routes.json` 을 최신 버전에 넣은 자식 버전(D-99). `expository` 가족과 `EXPOSITORY:*` 라우트만 통째로 바꾼다. 한 단계만 고칠 때는 `edit-prompt expository <단계>`·`edit-route EXPOSITORY:<단계>` | 버전 id·해시 |

기존 명령의 변화: `start-round` 는 최신 버전을 읽어 라운드에 적는다(`--pipeline-version <id>` 로 다른 버전을 고를 수 있다. 차기 버전 프롬프트를 시험하는 동안 안정 버전으로 라운드를 돌릴 때 쓴다, Q-42). `status`·`show` 응답과 `RoundPublic` 에 `pipeline_version_id` 가 보인다. `compare-prompts --version` 은 버전 id 또는 들여오기 디렉터리 경로를 받는다(저장하지 않고 글만 써 보는 길). `fact-check` 도 `--pipeline` 으로 버전을 고른다. `seed-routes`·`routes` 는 3단계에서 없앤다.

### 내보내기 형식

```
pipeline/<label>/
├─ pipeline.json          slots, personas, routes, level_criteria, parameters, draft_stages, round_stages, meta{version_id, parent_version_id, content_hash, label, note}
└─ prompts/
   ├─ claude/write_draft.md …
   ├─ gemini/…
   ├─ grok/…
   ├─ haiku/fact_transform.md, glossary.md
   └─ common/arbitrate_search.md
```

`import` 는 `meta.version_id`·`content_hash` 를 무시하고 다시 계산한다(파일을 고친 뒤 들여와도 되게). 저장소에는 `pipeline/baseline/` 하나를 둔다. 운영 중 `edit-prompt` 로 쌓인 버전은 때때로 `export` 해 `baseline` 을 덮어 커밋한다. 그래서 git 은 "리뷰하고 보관하는 곳", Mongo 는 "실행하는 곳" 이다. 커밋 없이 운영 버전이 앞서가도 Mongo 의 이력(부모·메모·시각)과 영수증이 어느 문구로 글을 썼는지 알려 준다. 버전끼리의 diff 는 내보낸 디렉터리를 git 으로 본다. 되돌리기는 git 에서 옛 `baseline` 을 꺼내 `import` 하는 것이다.

## 6. 테스트 전략

06 문서 6절에 더할 행이다.

| 범위 | 방법 |
|---|---|
| `models/pipeline.py` | 버전 문서·단계·조절 값 Pydantic 모델. 종류별 필수 필드, 열거형 밖 값 거절, 정규화 JSON 해시가 키 순서와 무관, meta 제외 |
| `services/pipeline_registry.py` | 모든 이름이 호출 가능하고 타입 정보가 실제 함수 시그니처와 맞는다(`inspect`). 배타 쌍이 대칭이다. 처리기 `allowed_at` 표가 `PostDraft` 메서드의 `_require` 와 일치한다(상태마다 메서드를 불러 `InvalidTransitionError` 여부 대조) |
| `services/pipeline_validation.py` | 규칙마다 통과 1건·위반 1건. 기준 버전(`pipeline/baseline/`)이 오류 0 으로 통과한다. 변이 테스트: 템플릿 하나를 빼면 R-05 가 가족·모델을 이름 짓는다(지금 `test_validate_prompts_names_the_missing_family_template`), `final_read` 를 `skip` 으로 두면 R-07, `check` 를 바꾸면 R-06, `when` 을 지우면 R-03, 같은 해시 두 번은 R-11 |
| 템플릿 불변식 | `test_registry.py` 의 "JSON 언급", "`$` 잔존", "가족 배치", "어미·길이·표기 문구" 는 R-05·R-08 의 데이터 검사가 된다. 테스트는 기준 버전을 검증기에 넣는 것 하나와, 각 규칙의 위반 1건이다. 가족 배치 표(`V4_EXPECTED`)는 "라우트가 요구하는 가족에 템플릿이 있다(R-05)" 로 바뀌고, 고아 템플릿은 경고다 |
| `repositories/pipeline_version.py` | `insert_one` 만 있다. `latest` 가 순번이 가장 큰 버전을 돌려주고 비어 있으면 `None`. `find_one_and_update($inc)` 순번이 동시 생성에도 겹치지 않는다 |
| `services/pipeline_executor.py` | `PipelineHarness` 가 **픽스처 버전**(`tests/factories.make_pipeline_version`: 가짜 벤더 라우트 + 기본 페르소나 + 스텁 프롬프트)을 넣고 06 문서 시나리오 ①~⑰ 을 그대로 통과한다(회귀). 새 시나리오: ⑱ `POST_REVIEW` 순서를 바꾼 버전(`GLOSSARY` 를 `ASSESS_LEVEL` 앞에)이 그 순서로 돈다 ⑲ `polish` 의 `on_failure` 를 `fail` 로 둔 버전은 다듬기 실패에 `FAILED` ⑳ 어느 상태의 단계를 지운 버전은 그 상태에서 `FAILED(INTERNAL_ERROR)` 이고 스캔이 되풀이하지 않는다 ㉑ 라운드 진행 중 새 버전을 넣어도 글은 처음 버전으로 끝까지 돌고 영수증의 `pipeline_version` 이 그 id 다 ㉒ `pipeline_version_id` 없는 옛 라운드는 최신 버전으로 돈다 ㉓ `max_iterations=1` 인 버전은 v2 미해소에서 `ESCALATED` |
| `tests/fakes.py` | `ScriptedLLMClient` 는 **스텁 프롬프트**의 문구로 종류를 가린다. 픽스처 버전의 `prompts.common.<name>` 은 `"[template:<name>] <종류 문구>. 입력(JSON)을 읽고 JSON 으로만 답한다."`(`tests/factories.STUB_PHRASES`)이고 `_KIND_MARKERS` 는 그 문구 표다. 실제 템플릿 문구와 분리됐다. 가짜 벤더 `fake`·`fake-a/b/c` 는 가족이 없어 `common` 으로 내려간다. 하네스의 `prompt_version` 인자가 없어지고 overrides 는 조절 값(`Parameters` 필드, 옛 평면 이름 포함)과 `Settings` 로 나뉜다(`split_overrides`) |
| `tests/factories.py` | `make_pipeline_version(**overrides)`: 픽스처를 읽어 일부만 바꾼 버전. `make_routing` 은 그대로(유효 스냅샷) |
| 기준 버전 회귀 | 1·2단계 동안 `build_baseline_version()`(코드 상수·저장소 프롬프트·`routes.example.json` 으로 기준 버전을 만드는 생성기)의 결과가 `pipeline/baseline/` 과 해시가 같다. 코드 상수를 고치고 내보내기를 잊으면 실패한다. 3단계에서 생성기를 없애면 이 테스트도 없어지고 `pipeline/baseline/` 이 유일한 기준이다 |
| `cli/` | 명령마다 `FakeAsyncDatabase` 위에서 서비스 호출과 종료 코드. `export` → `import` 왕복은 최신과 같은 해시라 거절, `edit-prompt` 가 부모·메모·diff 를 남긴다, 옛 내보내기를 다시 `import` 하면 그 본문의 새 버전이 생기고 같은 내용의 버전 id 를 경고로 알려 준다, `validate --all-referenced` 가 진행 중 라운드의 버전을 본다 |
| 골든 | `agents_round_detail.json` 에 `pipeline_version_id`. `provenance.json` 에 `pipeline_version` |
| `services/publishing.py` | 글 끝 표기(D-80): 작성·리뷰 줄은 항상, 수정 줄은 작성 모델과 다른 모델일 때만, 중재·다듬기 줄은 있을 때만. `$models` 치환. 버전 수·건수·인용이 없다. 변환 성공 |
| `services/polishing.py` | 정규식 단계가 없다(D-81): "하십시오" 가 든 문단이 호출 없이 그대로 남고 `PolishRecord` 에 정규식 항목이 없다 |
| `services/round.py` | 카테고리 거르기가 없다(D-82): Post BE 응답의 활성 메인 아래 활성 서브 전부가 스냅샷이다 |
| `services/publishing.py`(`level`) | 수준 평가가 있는 글은 `level` 을 항상 보내고, 사람 경로 글(평가 없음)은 키를 뺀다(D-83) |
| `core/config.py` | `Timeouts`·`Concurrency` 가 frozen 이고 기본값이 03 문서 5절 표와 같다. 하네스는 `replace()` 로 바꿔 넣는다(D-84) |
| e2e | 그대로. 실제 벤더로는 `compare-prompts --version v7 --version pipeline/draft/` 로 문구 변경의 효과를 본다 |

## 7. 이행 순서

단계마다 독립적으로 머지·배포할 수 있다. 단계마다 지금 동작을 그대로 재현하는 기준 버전을 두어 회귀를 막는다. 사용자가 정한 동작·설정 변경(D-80~D-84)은 데이터 이행(1a)에 섞지 않고 1b(이 저장소 안)·1c(다른 저장소 의존)로 나눈다. 되돌리기 단위를 작게 두기 위해서다(원 세션 검토, 2026-11-07). CloudType 러너와 로컬 CLI 는 Mongo 를 공유한다(D-43). 시드는 로컬에서 하고 배포는 그 뒤다.

| 단계 | 내용 | 배포·시드 순서 | 되돌리기 | 완료 기준 |
|---|---|---|---|---|
| (1a) 데이터 이행(동작 동일) | `models/pipeline.py`(`schema_version: 1`, `draft_stages` 없음), `models/persona.py`(`PersonaProfile` 이동), `repositories/pipeline_version.py`, `services/pipeline_versions.py`(읽기·캐시·최신·create·bootstrap·export·import·edit_*), `services/pipeline_validation.py`(R-04·R-05·R-08~R-11), CLI `agents pipeline bootstrap/validate/export/import/edit-prompt/edit-param/edit-routes`. `render_messages`·템플릿 로더가 버전의 `prompts` 에서 읽는다. `RoundService.create_round` 가 최신 버전으로 유효 스냅샷을 만들고 `pipeline_version_id` 를 적는다. 영수증 `AgentCall.pipeline_version`(옛 `prompt_template_version` 은 옛 영수증용). 서비스가 `Settings` 상수 대신 라운드 버전의 `parameters` 를 읽는다. `Settings.prompt_version`·`registry.PROMPT_VERSION` 삭제. `pipeline/baseline/` 내보내기와 `build_baseline_version()` 해시 동일 테스트, 픽스처 버전·스텁 프롬프트 하네스(D-78). 기준 버전은 현재 코드 상수·v4 프롬프트·`routes.example.json` 을 그대로 재현한다. 발행 머리말·다듬기 정규식·카테고리 거르기·수준 토글·타임아웃은 **지금 그대로** 둔다(값만 `parameters` 로 옮긴다. `publish.note` 도 지금 머리말 문구를 그대로 담는다). `_step` 은 그대로 | ① 로컬에서 `agents pipeline bootstrap --routes mongo` (현재 `agent_routes` 와 저장소 v4 프롬프트로 `v1`. 옛 코드는 새 컬렉션을 모른다. 실제 시드는 사용자 승인 뒤) ② 배포. 새 라운드는 `v1`, 진행 중 라운드는 최신 버전 | 이전 이미지 재배포. `agent_routes` 는 지우지 않았으므로 옛 코드가 그대로 돈다. `edit-prompt` 로 만든 버전은 Mongo 에만 있고 옛 코드는 저장소 v4 를 쓴다 | **완료(2026-11-07)**: 운영 Mongo 에 `v1` 시드(`bootstrap --routes mongo`, 해시 `sha256:152e37cd…`. 운영 `agent_routes` 기준이라 `pipeline/baseline/`(`routes.example.json`)의 해시와 다르다. R-05 경고 1건 외 오류 없음), origin/main 3275d97. CloudType 재배포 확인은 사용자 몫. 기준: 기존 단위 테스트가 그대로 통과한다(856). 기준 버전의 프롬프트 바이트·조절 값이 지금 코드와 같다(해시 테스트). `edit-prompt` 뒤 재배포 없이 다음 라운드의 영수증 `messages` 에 바뀐 문구가 있고 `pipeline_version` 이 새 id 다 |
| (1b) 이 저장소 안의 동작 변경 | D-80 글 끝 표기(`build_publish_note`. D-87 의 제목·줄 꼴로 코드 완료 2026-11-08), D-81 정규식 다듬기 삭제, D-84 `TIMEOUTS`·`CONCURRENCY` 불변 객체와 `Settings` 의 시간·동시성 변수 삭제 | 1a 가 main 에 올라간 뒤 배포 | 이전 이미지 재배포 | 글 끝 표기가 짧고, 정규식 패치가 없고, `.env` 의 타임아웃 변수가 무시된다 |
| (1c) 다른 저장소 의존 | D-82 카테고리 거르기 삭제, D-83 수준 항상 전송(설계대로 삭제. 2026-11-07 사용자 결정) | ⓪ 화면 없는 메인(`jest`·`cypress`·`junit5`·`git`)과 그 서브 21개는 공유 Mongo 에서 `isActive=false` 로 바꿨다(2026-11-07 완료. 활성 메인 9개 = vans_story `POST_THEMES`). 남은 전제는 Post BE(`vans_story_be_post`)에 `CreatePostDto.level` 을 더해 배포하는 것뿐이다 ① 이 저장소 배포 | 이전 이미지 재배포. Post BE 의 `level` 필드는 선택이라 옛 코드도 보내지 않을 뿐이다 | `level` 전송이 400 이 아니고 404 글이 생기지 않는다 |
| (2) 단계 목록·실패 정책·실행기 | `Stage` 모델(`schema_version: 2`), `services/pipeline_registry.py`, `services/pipeline_executor.py`, 검증 R-01~R-03·R-06·R-07·R-12. `DraftPipelineService._step`·`_write`·`_review`·`_arbitrate`·`_post_review`·`_final_read`·`_assess_level` 을 실행기로 대체. `_call_review`·`_call_fact_review`·`_call_arbitrate` 는 절차로 옮긴다. `PostDraft` 의 `POST_REVIEW` 처리기가 `next` 를 받는다. `TopicSelectionService._step` 이 `round_stages` 를 본다. `build_baseline_version()` 이 단계 목록도 만든다 | ① 배포(2단계 코드는 `schema_version: 1` 버전을 만나면 코드의 기준 단계 목록을 쓴다) ② 로컬에서 `agents pipeline import pipeline/baseline/` 로 단계가 든 `v0N`. 이때부터 새 라운드는 데이터 단계로 돈다 | 이전 이미지 재배포. 1단계 코드는 `draft_stages` 를 읽지 않으므로 `schema_version: 2` 문서도 그대로 쓴다(Pydantic `extra="ignore"`) | 시나리오 ①~㉓ 통과. `draft_pipeline.py` 에 `match draft.status` 가 없다 |
| (3) 정리 | `agent_routes`·`seed-routes`·`routes`·`app/agents/prompts/`·`registry.PROMPT_VERSION`·기준 생성기 삭제. `pipeline/baseline/` 이 유일한 기준. `.dockerignore` 예외 줄 삭제 | ① `agents pipeline validate --all-referenced` 로 진행 중 라운드가 `schema_version: 1` 을 안 가리키는지 확인 ② 배포 ③ `db.agent_routes.drop()` (Q-37: 지금 개발 중인 것이 v1 이라 보존할 옛것이 없다) | 이전 이미지 재배포. 지운 저장소 프롬프트는 git 에 있다 | `pipeline/baseline/` 을 `import` 하면 최신 버전과 같은 해시로 거절된다(저장소와 운영이 같다) |

각 단계의 기준 버전은 "지금 코드가 하는 일을 데이터로 적은 것" 이라 동작이 바뀌지 않아야 한다. 1단계는 프롬프트 바이트와 조절 값이 같은지(해시 비교), 2단계는 시나리오 ①~⑰ 이 같은 호출 종류·순서·상태를 남기는지(`ScriptedLLMClient.kinds`)로 본다.

운영 절차(3단계 뒤). 문구 조정: `agents pipeline edit-prompt claude write_draft --file new.md --note "…"` → 다음 라운드부터 적용. 문구만 보기: `agents compare-prompts <call_id> --version pipeline/next/` (들여오지 않는다). 차기 버전 시험: `agents pipeline import pipeline/next/` → `agents start-round`(최신이라 그대로 쓴다). 그동안 안정 버전으로 돌릴 라운드는 `agents start-round --pipeline-version v7`. 나쁘면 git 의 옛 `pipeline/baseline/` 을 `import`. 보관: `agents pipeline export --out-dir pipeline/baseline/` → 커밋.

## 8. 코드 배치 (06 문서 2절에 더할 것)

```
app/
├─ models/pipeline.py              PipelineVersionBody·PipelineVersion, Stage(종류별 모델, 2단계), Parameters. 해시 계산(순수 함수)
├─ models/persona.py               PersonaProfile (agents/personas.py 의 상수는 bootstrap 입력·테스트 기본값)
├─ repositories/pipeline_version.py  agent_pipeline_versions(insert·get·latest·list), 순번. ensure_indexes
├─ services/
│  ├─ pipeline_registry.py         1.2절 표. 이름 → 함수·타입·배타 쌍·allowed_at·skippable
│  ├─ pipeline_validation.py       validate_version(version, registry) -> list[Finding]. R-01~R-12
│  ├─ pipeline_versions.py         PipelineVersionService: get(캐시)·latest·create(검증·해시·순번)·export·import·edit_prompt·edit_param·edit_routes·bootstrap(1·2단계)
│  └─ pipeline_executor.py         PipelineExecutor.step(draft, round, version), _llm_call, _fan_out, _branch. 절차 모음은 services/pipeline_procedures.py(fact_review·plain_review·arbitrate_item·polish·glossary_extract·publish·propose·assign_and_commit 을 기존 서비스 메서드에 위임)
├─ agents/
│  ├─ builders.py                  render_messages(model, version, call_model, template_name). 빌더가 version 의 level_criteria·구조·톤을 읽는다
│  ├─ personas.py                  PersonaProfile 모델만. 상수 PERSONAS 는 3단계에서 삭제
│  └─ prompts/                     3단계에서 삭제(registry.py 의 prompt_family 는 agents/prompt_family.py 로)
pipeline/
└─ baseline/                       5절 내보내기 형식. git 이 보관하는 기준
tests/factories.py                 make_pipeline_body·make_pipeline_version(스텁 프롬프트가 든 픽스처 버전), STUB_PHRASES
```

의존 방향은 그대로다. `pipeline_registry` 는 `services`·`agents`·`models` 를 모으고, `pipeline_executor` 는 레지스트리와 호출 서비스만 안다. `models/pipeline.py` 는 다른 `app` 패키지를 import 하지 않는다(열거형은 `models/enums.py`).

## 9. 문서 반영 목록 (구현 때)

| 문서 | 위치 | 변경 |
|---|---|---|
| 01 | 2절 범위, 4절 원칙 | "프롬프트 문구는 별도 파일로 버전 관리" → "버전 문서(09)". 원칙 9 "파이프라인은 데이터로 기술되고 원시 동작은 코드다" |
| 02 | 2절 엔티티 관계 | `PublishingRound.pipeline_version_id?`, `routing` 설명을 "버전의 routes 에서 만든 유효 스냅샷" 으로, `AgentCall.pipeline_version?` 추가(`prompt_template_version?` 은 옛 영수증용). 새 집합 루트 `PipelineVersion`(2.2절) |
| 02 | 3절 값 객체 | `RoutingSnapshot` 설명의 `agent_routes` → 버전 문서. `PersonaProfile` 이 버전 문서에 있다 |
| 02 | 6절 저장 표 | `agent_pipeline_versions`·`agent_pipeline_counters` 행. `agent_routes` 행 삭제(3단계) |
| 03 | 5절 상수 표 | 머리말에 "값은 버전 문서의 `parameters`(09 문서 2.4절)" 를 적는다. `AGENT_MAIN_CATEGORIES`·`PUBLISH_POST_LEVEL` 행 삭제, `*_TIMEOUT_S`·`SCAN_INTERVAL_S` 행은 "`core/config.TIMEOUTS`·`CONCURRENCY` 상수" 로 |
| 03 | 3절 라운드 생성 가드 | "`AGENT_MAIN_CATEGORIES` 로 거른 뒤 서브가 하나 이상" → "활성 메인 아래 활성 서브가 하나 이상" |
| 04 | 8절 `CreatePostDto` 매핑표 | `level` 행 추가: 수준 평가가 있으면 항상, 사람 경로 글은 생략(D-83) |
| 03 | 1절 상태도, 2절 22a~22f | `POST_REVIEW` 하위 단계의 순서가 데이터라는 주석 |
| 04 | 1절 실행 모델 | `_step` 설명을 실행기(09 문서 3.1절)로. "라운드가 버전을 고정한다" |
| 04 | 3절 게이트 표 | 임계값의 출처를 `parameters` 로 |
| 04 | 5절 발행 전 단계 표 | "순서·실패 정책은 버전 문서의 단계 목록". 다듬기 안전장치 표의 정규식 행 삭제(D-81) |
| 04 | 7절 가드레일 | 톤 보존 목록·사실 확인 심각도가 조절 값 |
| 05 | 머리말 | "프롬프트 문구는 버전 문서의 `prompts`. 디렉터리는 없다. 문구를 바꾸면 새 버전(`edit-prompt`)" |
| 05 | 1절 페르소나 | "버전 문서의 `personas`" |
| 05 | 3절·4절 | `StructureRules`·`TonePreservationRule` 기본값의 출처가 `parameters` |
| 05 | 5절 머리말 템플릿 | 글 끝 짧은 표기(2.4절, D-80)로 바꾼다. 버전 수·차단 건수·기각 인용은 제작 기록(08 문서)에만. 문구는 `parameters.publish.note` |
| 06 | 2절 구조도 | 8절의 파일들. `agents/prompts/` 삭제, `repositories/model_route.py` 삭제 |
| 06 | 3절 CLI 표 | `agents pipeline …` 행(5절). `seed-routes`·`routes` 삭제. `start-round --pipeline-version`, `compare-prompts --version` 의미 변경 |
| 06 | 3절 라우터 표 | `RoundPublic.pipeline_version_id` |
| 06 | 3절 라우터 표 `POST /rounds/` | "`AGENT_MAIN_CATEGORIES` 에 든 메인만 남긴" 설명 삭제 |
| 06 | 4절 Settings | 2.4절 "남는 것" 으로 정리. `prompt_version`·`agent_main_categories`·`publish_post_level`·타임아웃·동시성 삭제, `TIMEOUTS`·`CONCURRENCY` 불변 객체 설명 |
| 06 | 6절 테스트 | 6절 표 |
| 07 | 1절 결정 | D-70~D-84(10절). D-16(요약 노트)·D-51(라우트 위치)·D-54(`PUBLISH_POST_LEVEL`)·D-58(다듬기)·D-59·D-62(버전 디렉터리)·D-60(카테고리 거르기)·D-69(두 자리 템플릿)에 "09 문서 D-7x·D-8x 로 대체" 주석 |
| 07 | 2절 미결 | Q-32~Q-42(11절). Q-18 해소(D-83: 수준을 항상 보낸다, Post BE 에 필드 추가). Q-19 에 "`fact_check.severity` 조절 값으로 바뀌어 데이터 변경이 됐다" |
| 07 | 3절 로드맵 | 10단계: 데이터 파이프라인 (1)~(3) |
| 08 | 2.3절 | 템플릿 위치 "v4/haiku/glossary.md 와 v3/glossary.md" → "버전 문서 `prompts.haiku.glossary`" (D-69 소멸) |
| 08 | 2.1절·3.2절 스키마 | `PostGlossary.prompt_template_version` 과 `post_provenance` 의 `prompt_template_version(s)` 옆에 `pipeline_version` 을 두고 옛 글만 옛 필드를 채운다. 3.1절 "머리말 노트는 그대로" 를 "글 끝 표기는 모델 이름만" 으로 |
| README | 표·상태 | 09 행(이번에 추가), 구현 때 상태 줄 |
| `routes.example.json` | – | `pipeline/baseline/pipeline.json` 의 `routes` 로 흡수. 파일 삭제 또는 포인터만 |
| `.env.example` | – | `PROMPT_VERSION`·`AGENT_MAIN_CATEGORIES`·`PUBLISH_POST_LEVEL`·`*_TIMEOUT_S`·`SCAN_INTERVAL_S`·`LLM_CONCURRENCY_PER_PROVIDER` 삭제, 상수 열두 개를 "버전 문서로 옮겼다" 주석과 함께 삭제. 남는 것은 비밀과 주소뿐 |
| vans_story_be_post | `CreatePostDto`·`Post` 스키마 | `level`(100·200·300·400, 선택) 필드와 검증 추가. 이 서비스보다 먼저 배포(D-83). 화면 없는 메인(`jest`·`cypress`·`junit5`·`git`) 비활성화(D-82) |
| vans_story | 글 화면 | `level` 표시(Q-18 의 프런트 몫). 메인을 다시 켜려면 화면을 먼저 더한다 |
| `.dockerignore` | – | `!app/agents/prompts/**/*.md` 삭제, `pipeline/` 제외 |
| CLAUDE.md | 에이전트 파이프라인 | 진행 줄과 운영 절차(7절 끝) |
| CONVENTIONS.md | 1절 | `pipeline/` 디렉터리 설명 한 줄 |

## 10. 결정

| ID | 결정 | 이유 | 대안 |
|---|---|---|---|
| D-70 | 경계: 조립(단계 목록·순서·조건·실패 정책·상한)·프롬프트·라우트·조절 값은 Mongo 버전 문서, 원시 동작(빌더·검사·처리기·선택기·조건·게이트·절차·변환·발행·잠금·재개)은 코드다. 데이터는 코드를 **이름**으로만 가리키고 이름은 `services/pipeline_registry.py` 의 명시적 표에 있다 | 2026-11-07 사용자 판단: 프롬프트 수정마다 재배포하지 않고, 흐름을 한 문서에서 읽는다. 상태 전이·병렬 실패·외부 호출은 틀리면 중복 게시·비용 유출이라 코드 리뷰를 거친다. 식 언어나 범용 해석기를 두면 테스트 대상이 둘(해석기와 데이터)이 된다 | 전부 DB 해석기(워크플로 엔진), 프롬프트만 DB 로(조립이 여전히 여러 파일에 흩어진다), 설정 파일(재배포가 남는다) |
| D-71 | 버전 문서 하나(`agent_pipeline_versions`)에 전부 넣는다. append-only, 부모 포인터, 순번 id `v1`·`v2`…(Q-36: 지금 개발 중인 것이 v1 이다). `content_hash` 는 같은 내용을 알아보는 정보이고 unique 가 아니다. 리포지토리에 갱신·삭제 메서드가 없다 | 프롬프트·라우트·조절 값은 함께 튜닝되고 함께 되돌린다. 라운드·영수증이 id 하나로 그때의 조건 전부를 가리킨다. 제자리 수정을 막아야 "이 글은 어떤 문구로 썼나" 가 흔들리지 않는다(D-26 과 같은 원칙). 되돌리기가 같은 내용의 새 버전을 만들므로 해시는 unique 일 수 없다 | 섹션별 컬렉션(프롬프트·라우트·조절 값 따로. 조합 폭발), 제자리 수정 + 이력 배열, git 해시를 id 로(Mongo 만 보고 만든 버전에 없다), `content_hash` unique(처음 안. 되돌리기와 충돌한다) |
| D-72 | 활성 버전은 순번이 가장 큰 버전이다. 포인터 문서도 환경 변수도 두지 않는다. 되돌리기는 이전 본문을 복사한 새 버전이다. 버전이 없으면 라운드 생성을 거절하고 저장소 파일로 대체하지 않는다 | 라운드가 생성 때 버전을 고정하므로(D-73) 정할 것은 "다음 라운드가 쓸 버전" 하나이고 그것은 최신이다. 러너도 Mongo 도 하나라 환경을 가를 대상이 없고, 로컬 CLI 가 만든 라운드도 CloudType 러너가 돌린다(2026-11-07 사용자 지적). 미리 넣어 두고 나중에 켜는 일은 `compare-prompts` 의 디렉터리 입력과 되돌리기로 대신한다. 빠진 설정이 숨지 않는다(D-51) | 환경별 활성 포인터 + `PIPELINE_ENV`(처음 안), 포인터 하나 + `activate` 명령, 버전 없으면 저장소 파일로 대체 |
| D-73 | 라운드는 생성 때 `pipeline_version_id` 를 적고 끝까지 그 버전으로 돈다. `routing` 은 그 버전의 `routes` 에서 키 없는 벤더를 빼고 A/B 를 얹은 유효 스냅샷으로 남는다. 영수증에는 새 필드 `pipeline_version` 을 적고 `prompt_template_version` 은 옛 영수증에만 남긴다. 옛 라운드(`None`)는 최신 버전으로 돈다. `start-round --pipeline-version` 으로 최신이 아닌 버전의 라운드를 만들 수 있다(Q-42: 차기 버전 프롬프트 시험용) | 호출마다 최신 버전을 읽으면 진행 중 글의 프롬프트·상한이 바뀐다(D-51 의 이유 그대로). 유효 스냅샷은 라운드가 실제로 쓴 것이라 버전과 역할이 다르다. 옛 영수증의 `v3`·`v4` 는 프롬프트 디렉터리 이름이라 파이프라인 버전 `v3`·`v4` 와 겹치므로 필드를 나눠야 제작 기록이 둘을 구분한다 | 라운드에 버전 문서 전체 복사(수백 KB 중복), `prompt_template_version` 값만 바꾸기(옛 값과 겹친다), 라운드는 전부 최신만 |
| D-74 | 상태 기계는 `PostDraft`·`PublishingRound` 메서드에 남고, 실행기는 처리기 이름만 부른다. 단계는 `at`(상태·하위 단계)과 `when`(조건 이름 AND 목록)으로 고르고, 0개나 2개 이상 맞으면 `FAILED(INTERNAL_ERROR)` 다. 단계 종류는 `transition`·`gate`·`llm_call`·`fan_out`·`procedure` 다섯이고 실행 방식은 코드다 | 03 문서 전이표의 불변식이 메서드의 `_require` 에 있으므로 데이터가 틀려도 잘못된 전이가 일어나지 않는다. 병렬 실패 의미(성공분 저장 뒤 FAILED)와 검색→변환 절차는 흐름 안에 데이터 의존이 있어 코드가 맞다 | 단계가 다음 상태를 적는 방식(전이표가 둘이 된다), 범용 DAG |
| D-75 | `POST_REVIEW` 하위 단계의 순서·포함·실패 정책은 `draft_stages` 배열이 정하고, 처리기는 `next` 를 받는다. 어휘(`PostReviewStage`)는 열거형으로 남고 `GLOSSARY` 는 `POLISH` 뒤여야 한다(R-03) | 지금 단계 넷의 순서와 "실패해도 건너뛸지" 가 `_advance_post_review`·`_post_review` 의 두 곳에 흩어져 있다. 새 하위 단계는 코드(처리기·열거형)가 필요하지만 순서·정책은 데이터로 끝난다 | 열거형 순서 고정, 하위 단계마다 상태 추가 |
| D-76 | 조절 값은 2.4절 표다. `Settings`(환경 변수)에는 비밀과 주소만 남는다. `prompt_version`·`agent_main_categories`·`publish_post_level` 은 없어지고(D-82·D-83), 시간·동시성은 `core/config.py` 의 불변 객체다(D-84). 새 환경 변수는 없다 | 조절 값은 글의 품질 조건이라 버전과 함께 되돌려야 한다. 환경 변수로 남길 것은 환경마다 실제로 다른 값(비밀·주소)뿐이다 | 전부 `Settings` 유지, 전부 버전 문서로, 배포 사실을 `Settings` 에 남기기(처음 안) |
| D-77 | CLI 는 5절 표의 넣는 명령뿐이다(`bootstrap`·`validate`·`export`·`import`·`edit-*`). 조회·되돌리기·diff 명령과 API 는 두지 않는다. 잦은 문구 조정은 `edit-prompt`(최신 버전의 자식, 템플릿 하나만 교체)로 끝낸다. git 에는 `pipeline/baseline/` 내보내기 하나를 두고 때때로 `export` 해 커밋한다. diff 는 git, 되돌리기는 옛 내보내기 `import`, 보는 것은 Mongo 직접 조회다 | 재배포 없는 프롬프트 수정이 목표다. 조회·되돌리기 도구는 지금 쓸모가 없다(2026-11-07 사용자 결정, Q-40). 파일로 내보내면 diff 리뷰와 보관은 git 이 맡고, 실행은 Mongo 가 맡는다. 두 곳이 어긋나는 것은 해시 비교(`import` 거절)로 보인다 | git 만(재배포 필요), Mongo 만(리뷰·보관이 없다), 조회·롤백·diff 명령과 API(처음 안), 관리자 UI |
| D-78 | 테스트는 픽스처 버전 문서로 한다. 하네스의 프롬프트는 단계 표식이 든 스텁이고 `ScriptedLLMClient` 는 표식으로 종류를 가린다. 템플릿 불변식 테스트는 검증 규칙(R-05·R-08)이 되고, 기준 버전이 검증을 통과하는지가 테스트다. 1·2단계 동안 `build_baseline_version()` 과 `pipeline/baseline/` 의 해시가 같아야 한다 | 오케스트레이션 테스트가 실제 프롬프트 문구에 묶이면 문구를 고칠 때마다 가짜 LLM 의 표가 깨진다(지금 `_KIND_MARKERS` 가 그렇다). 기준 생성기와 내보내기의 해시 비교가 "데이터가 코드를 재현한다" 를 보장한다 | 실제 템플릿을 그대로 픽스처로, 템플릿 불변식을 코드 테스트로 유지 |
| D-79 | 이행은 (1a) 프롬프트·조절 값·라우트·페르소나의 데이터 이행(동작 동일) → (1b) 이 저장소 안의 동작 변경(D-80·D-81·D-84) → (1c) 다른 저장소에 묶인 변경(D-82·D-83) → (2) 단계·실패 정책·실행기 → (3) 정리 순이다. 시드는 로컬 CLI 가 공유 Mongo 에 먼저 하고 배포는 뒤다. `agent_routes`·저장소 프롬프트·기준 생성기는 3단계에서 바로 지운다(Q-37) | 1a 만으로 사용자가 바란 "재배포 없는 프롬프트 수정" 이 생긴다. 데이터 이행에 동작 변경을 섞으면 "기준 버전이 지금 동작을 재현한다" 는 원칙이 깨지고 되돌리기 단위가 커진다(원 세션 검토). 옛 코드가 새 컬렉션을 모르고 새 코드가 옛 컬렉션을 1·2단계 동안 읽으므로 어느 단계에서든 이전 이미지로 돌아갈 수 있다. 지금 개발 중인 것이 v1 이고 성공한 라운드가 없어 보존할 옛것이 없다 | 한 번에 전부, 실행기 먼저, 옛 컬렉션 유예 기간, 동작 변경을 1단계에 포함(처음 안) |
| D-80 | 발행 본문 끝의 표기는 `---` 뒤 "작성 AI - 모델", "리뷰 AI - 모델(렌즈)…", 수정·중재·다듬기 줄(있을 때만)로 줄인다. 버전 수·차단 건수·기각 인용·중재 판정은 넣지 않는다. 줄 선택은 코드, 문구는 `parameters.publish.note` 다 | 2026-11-07 사용자 결정(Q-34): 글 끝에 짧게. 전체 이력은 제작 기록 블록(08 문서 D-67)이 이미 보이므로 머리말이 되풀이할 이유가 없다 | 05 문서 5절의 긴 머리말 유지(D-16 의 템플릿), 구조까지 템플릿 언어로 |
| D-81 | 다듬기에서 정규식 어미 치환(`하십시오`→`한다` 등)과 긴 문단 알림을 없앤다. 작성 모델이 프롬프트의 어미 규칙을 보고도 쓴 표현은 그대로 둔다. 문체 호출(`POLISH_TONE`)과 안전장치는 남고, 그 단계도 데이터라 단계 목록에서 빼면 없어진다 | 2026-11-07 사용자 결정(Q-35): 정규식을 쓰지 말고 AI 가 쓴 것은 둔다. 어미 규칙은 작성·수정 프롬프트(D-62)의 몫이고 사후 치환은 뜻을 보장하지 못한다 | 정규식 유지(D-58 의 첫 행), 정규식을 조절 값으로 |
| D-82 | `AGENT_MAIN_CATEGORIES` 거르기를 없앤다. 라운드 스냅샷은 Post BE 의 활성 메인 아래 활성 서브 전부다. 블로그에 화면이 없는 메인은 Post BE 에서 비활성화해 한 곳(Post BE)만 기준이 되게 한다 | 2026-11-07 사용자 결정(Q-33): Post BE 값으로 통일. 같은 사실을 두 저장소가 따로 들면 어긋난다(D-60 의 404 가 그 예다). D-60 의 대안이었던 "Post BE 에서 비활성화" 를 택한다. 사람 에디터의 목록도 함께 줄지만 어차피 화면이 없는 메인이다 | 이 서비스의 목록 유지(D-60), 프런트에 화면 추가, 조절 값 `parameters.topic.main_categories`(원 세션 제안: Post BE 의 `isActive` 는 관리 토글이고 "화면이 있다" 는 프런트의 사실이라 다시 어긋날 수 있다. 사용자가 설계대로 삭제를 택했다, 2026-11-07) |
| D-83 | 수준 평가가 있는 글은 `CreatePostDto.level` 을 항상 보낸다. `PUBLISH_POST_LEVEL` 토글을 없앤다. Post BE 가 `level` 필드를 받도록 먼저 배포한다(다른 저장소) | 2026-11-07 사용자 결정: 수준은 글 생성에서 매우 중요한 지표라 저장돼야 한다. 토글은 Post BE 배포 순서를 맞추려던 임시 장치였고(D-54), 순서는 이행 표가 정한다 | 토글 유지, 수준을 본문에 적기, 조절 값 `parameters.publish.send_level`(원 세션 제안: 다른 저장소 배포 순서에 묶이지 않는다. 사용자가 설계대로 삭제를 택했다, 2026-11-07) |
| D-84 | 타임아웃(작성·리뷰·발행)과 동시성(벤더당 동시 요청, 429 냉각, 스캔 주기)은 `core/config.py` 의 frozen dataclass `Timeouts`·`Concurrency` 인스턴스 `TIMEOUTS`·`CONCURRENCY` 다. 환경 변수도, 값마다 흩어진 모듈 상수도 아니다. 테스트는 `dataclasses.replace` 로 바꿔 넣는다 | 2026-11-07 사용자 결정: 타임아웃마다 전역 변수를 두지 말고 불변 객체로 모으거나 코드에 박는다. 지금은 `Settings` 네 개와 `agent_calls._RATE_LIMIT_COOLDOWN_S` 가 흩어져 있다. 환경마다 다를 값이 아니다 | 환경 변수 유지, 버전 문서의 조절 값으로(글의 조건이 아니다) |
| D-85 | 어미를 프롬프트로 고정하지 않는다. 게이트가 서술문 어미 분포를 재서 소수 어미가 임계(비율·최소 문장 수)를 넘거나 명령형·청유형이 있으면 예시 문장·섹션을 오류에 적어 수정 루프로 보내고, 12줄 이상 코드 블록에 25줄당 1줄 이상의 설명 주석(파일 경로 머리줄 제외)이 없어도 보낸다. 정규식 치환은 하지 않는다. 작성·수정 프롬프트의 "~다" 고정 규칙은 뺐고 코드 주석 요구를 넣었다. 임계는 `parameters.style`·`parameters.code_comments` | 2026-11-08 사용자 지적: "생성한 글에도 주석이 하나도 없음", "어미를 프롬프트로 고정하지 말고 생성된 어미가 이상한지를 검증해야지". 프롬프트 고정은 모델마다 다르게 새고(Claude 가 1차에 ~합니다 로 갔다) 검증이 없으면 새는 것을 모른다. 측정: 글 10편의 코드 블록 42개 중 파일 경로 머리줄 외 주석이 있는 블록이 드물었고 명령형이 섞인 글도 잡히지 않았다 | 프롬프트 고정 유지, 리뷰 렌즈에 문체 지적 허용(톤 보존 규칙과 충돌), 정규식 치환(D-81 로 뺐다), 경고만(수정되지 않는다) |
| D-87 | 글 끝 표기의 제목은 `## AI Information` 이고 줄은 "- 역할: 모델" 목록이다(작성·리뷰·수정·중재·다듬기). 같은 모델이 렌즈 둘을 맡으면 `모델(렌즈, 렌즈)` 로 한 번 적는다. D-80 의 줄 선택 규칙은 그대로이고 "작성 AI - 모델" 꼴만 바꿨다. 옛 "이 글은 어떻게 만들어졌나" 머리말의 필드는 버전 문서에 남아 있어도 무시된다(`extra` 무시). 이미 발행된 글의 본문은 자동으로 바뀌지 않는다 | 2026-11-08 사용자: "ai information으로 바꾸고, 누가 뭘 했는지 간단히". 발행된 글 끝에 버전 수·MINOR 건수·수용 0건 같은 숫자가 남아 독자에게 뜻이 없었다. 이력은 제작 기록이 보인다 | "작성 AI - 모델" 줄(D-80 의 처음 꼴, 제목 아래에서 "AI" 가 되풀이된다), 렌즈마다 한 줄 |
| D-88 | 용어 정의는 사전체 명사구다: 명사로 끝나고("…객체들의 묶음") "~이다"·"~한다" 같은 문장 어미와 끝 마침표가 없다. `models/glossary.dictionary_style_problem` 이 끝 글자 "다" 와 문장 안의 "다. " 를 잰다. 추출 계약 `SenseProposal.definition` 이 어기면 계약 위반이라 `AgentCallService` 가 직전 출력과 사유를 붙여 다시 부르고(코드는 끝 마침표만 뗀다), `approve_sense` 는 사전체가 아닌 정의를 승인하지 않으며, `agents glossary check` 가 규칙 전 정의를 알린다. 추출 프롬프트(v4 haiku·v3 `glossary.md`)의 정의 규칙도 이 꼴로 바꿨다 | 2026-11-08 사용자: "~이다 말고 사전체로 묶음 까지만". 툴팁의 정의가 본문 문장처럼 읽혔다. 사전 표제어 풀이는 명사로 끝나는 것이 관례다. 어미를 코드가 바꾸지 않는 것은 D-85 와 같은 이유다("~한다" 의 명사형을 코드가 정할 수 없다) | 정규식으로 "이다." 를 떼기("한다" 는 못 고친다), 항목 단위로 버리기(용어를 잃는다), 프롬프트만 바꾸기(새는 것을 모른다) |
| D-86 | 블로그 글의 기대 문체는 합쇼체(~습니다)다. 조절 값 `structure.prose_register`(운영 `polite`, 코드 기본 None)가 작성·수정 입력의 `structure_rules.prose_register`·`prose_register_description` 으로 모델에 전달되고, 게이트는 다수 어미가 기대 문체와 다르면 예시를 들어 수정 루프로 보낸다. 수준 100·200 기준은 사용자가 지정한 참조 글(데이터 모델의 이해)의 호흡(짧은 문장, 작은 제목, 일상 예시, 주석 달린 작은 코드 조각)으로 적고 `level_criteria.reference_posts` 에 그 글을 둔다 | 2026-11-08 사용자: "왜 반말을 하지?", "레벨 100~200 은 이런 느낌이어야 함". 블로그 사람 글 36편 전부 합쇼체, 파이프라인 글 2편만 해라체였다. 어미를 지시문에 박지 않는다는 D-85 원칙은 유지하고 데이터와 검증으로 푼다 | 프롬프트에 합쇼체 지시(D-85 와 충돌), 문체를 정하지 않음(블로그 안에서 반말·존댓말이 섞인다) |
| D-89 | D-77 개정: 넣는 쪽만 내부 API 로도 연다. `POST /api/v1/agents/pipeline/prompts/{family}/{name}/`(템플릿 하나 통째로 교체, 최신 버전에 없는 이름은 400)·`POST /api/v1/agents/pipeline/parameters/`(점 경로 → 값). CLI `edit-prompt`·`edit-param` 과 같은 서비스를 부르고 `created_by` 는 `api` 다. 응답에 검증 경고를 돌려준다. 조회·되돌리기·diff API 는 여전히 두지 않는다 | Mongo 접속 정보가 없는 PC 에서도 배포 없이 문구·조절 값을 고치기 위해서다(2026-11-09 사용자 결정). 같은 키로 이미 라운드 생성·발행 승인을 하므로 권한 범위는 크게 늘지 않는다 | Mongo URI 를 그 PC 의 `.env` 에 두기(운영 DB 자격 증명이 퍼지고 Atlas 허용 IP 가 필요), 조회 API 까지 열기 |
| D-90 | 다듬기 전에 코드가 AI 문체 패턴을 찾아 `PolishToneInput.findings`(문단·규칙 id·구절)로 준다. 패턴은 조절 값 `polish.tell_patterns`(id·label·정규식·`min_count`)이고 찾기만 하며 고치지 않는다. 글 전체 횟수가 `min_count` 이상인 규칙만 알린다(부정 대구 C-8 은 2). `polish_tone` 은 규칙 id 별 처방과 전후 예시를 갖고, 서법·격식 보존, 비유·상투구 주입 금지, 대구 전멸 금지, 단문 쪼개기 금지를 지킨다. 안전장치의 부정어 셈에서 대조 연결 "아니라"를 뺀다. `PolishRecord.tells_before/tells_after` 가 전후 횟수를 남기고 제작 기록의 다듬기 줄에 합계를 붙인다 | 두 글(6aefc1e0·6af10dbd)에서 다듬기가 빈 패치를 냈다: "심한 번역투만" 기준이라 문법이 맞는 번역투·비유를 건너뛰었다. 규칙 id 와 패턴 이름은 epoko77-ai/im-not-ai(MIT)의 분류를 따랐다(T-1 만 이 블로그 글에서 뽑음). 그쪽 실측에서 지시만으로는 분열문이 고쳐지지 않아 예시를 붙였다. 부정어 셈이 "아니라"를 세면 가장 강한 신호(C-8) 수정이 모두 버려진다. D-81 의 "정규식으로 고치지 않는다"는 그대로다(탐지만 한다) | 최소 변경률 강제(im-not-ai 자체가 5% 미만을 "이미 좋은 글"로 본다), 리뷰 단계 통합(문체와 별개, 근거 부족), 사람 글 기준선 z 점수 게이트(깨끗한 기준선이 없다) |
| D-90 개정 | `polish_tone` 예시를 사용자가 고른 문장으로 바꾼다. 예시는 효과 확인에 쓰지 않는 글(6af10dbd·6aefc1e0·6aeb9a72)에서만 뽑고, 그대로 둘 문장과 "하지 말 것" 예시(틀 명사 보존, "~적" 추상 명사, "아니라"→"대신/아닌" 바꿔 끼우기, 새 수식어)를 붙인다. 부정어 셈에 "아닌"·"아닙"을 넣고 "아니"와 같은 하나로 센다. 기본 `tell_patterns` 는 이 블로그 사람 글보다 AI 글에 더 많이 나오는 D-14·D-8·D-10·T-1 만 둔다. C-8·A-7·A-8·A-9·D-1 은 탐지와 `polish_tone` 규칙에서 빼고, "A가 아니라 B"와 그 셋은 자연스러우면 그대로 두라고 적는다 | v8 미리보기에서 모델이 예시 문장("이 글에서는 한 가지 결정만 다룹니다", 내가 쓴 어색한 문장)을 그대로 베꼈고, "구조적 경고"·"컨테이너 대신 도메인이 정합니다"·"단순한 취향 문제가 아닌, …"을 냈다. 예시를 뽑은 글로 효과를 재면 베끼기만 잰다. "아닌"·"아닙니다"는 한 음절이라 "아니" 셈에 걸리지 않아 극성 뒤집기와 대구 바꿔 끼우기가 안전장치를 지났다. 효과 확인 글 6aeceba2(Jest)에서 Gemini 가 금지 예시를 보고도 자연스러운 "아니라" 두 문장을 "~대신"·"~와 무관하게"로 바꿨다. 사용자 글 35편과 AI 글 5편을 세어 보니(1000어절당) C-8 은 2.69 대 3.07 로 비슷했고 A-7(0.18 대 0)·A-8(0.04 대 0)·A-9(0.13 대 0)·D-1(0.57 대 0)은 사람 글이 더 많았다. 이런 패턴을 지우게 하면 다듬기가 블로그 주인의 문체를 지운다. D-8 은 0.20 대 0.26 으로 비슷하지만 사용자가 어색하다고 확인한 문장이라 남긴다. AI 글이 5편이라 D-10·T-1 근거는 약하다 | 예시를 내가 확정(한국어 자연스러움 판단은 사용자 몫), "아닌"을 별도 토큰으로 세기("아니고"↔"아닌" 활용 바꿈까지 버려진다), C-8 만 빼기(A-7·A-8·A-9·D-1 도 같은 근거로 사람 글이 더 많다), C-8 `min_count` 를 사람 글 상위 10%(9회)로 올리기(사실상 꺼진다) |
| D-91 | 문체 다듬기를 둘로 나눈다. 작성 벤더가 아닌 모델(`POLISH_TONE`)은 문단 패치를 제안만 하고, 작성 벤더의 수정 모델이 `POLISH_REVISE`(템플릿 `common/polish_revise`, 입력 `PolishReviseInput`: 문단·제안·findings)로 제안을 받아들이거나 제 말로 고쳐 쓰거나 버린다. 안전장치·게이트를 거쳐 적용하는 것은 수정 모델의 패치뿐이다. 바뀐 문단이 없거나 없는 문단을 가리킨 제안은 보내지 않는다. 제안이 없으면 `POLISH_REVISE` 를 부르지 않는다. 글 끝 표기는 패치를 쓴 모델이 작성 모델이면 다듬기 줄을 쓰지 않고, 제안한 모델을 "- 다듬기 검토: 모델"(`publish.note.polish_reviewers`)로 적는다. `PolishRecord.proposer_model_ids` 가 제안한 모델이다. `polish_revise` 는 `REVISE_DRAFT` 라우트의 템플릿이라 R-05 가 그 후보 가족마다 확인한다 | 2026-11-09 사용자 지적: Grok 글 6af15468 의 문단을 Opus 가 직접 고쳐 발행본 일부가 다른 모델의 문장이었다. 리뷰 결과를 작성 모델이 받아 고치는 수정 루프와 같은 원칙으로, 다듬기 검토는 다른 모델이 하고 고치는 것은 작성 모델이 한다. D-52 의 "문체 다듬기는 작성 벤더 밖"은 제안에만 남는다 | 작성 모델이 혼자 다듬기(다른 눈이 없다), 제안을 리뷰 항목으로 수정 루프에 넣기(본문 전체를 다시 써 토큰·게이트 재시도가 든다) |
| D-92 | 웹 검색은 사실 확인 렌즈(`REVIEW:FACT_STRUCTURE`)의 첫 후보 한 모델이 한다. 운영 라우트는 Gemini 다. 이 렌즈는 작성 벤더를 피하지 않으므로 Gemini 글의 사실 확인도 Gemini 가 한다. 사실 확인 지적의 분쟁에서 근거를 모으는 검색(`arbitrate_search`)도 중재자가 아니라 이 모델이 하고, 판정은 지금처럼 작성·리뷰 벤더 밖의 중재자가 한다. 상수는 `models/routing.WEB_SEARCH_LENS` 다 | 2026-11-09 사용자 결정: Gemini 글은 Claude 가, Grok 글의 중재 검색은 Claude 가 맡아 두 글이 글 토큰 예산을 넘겼다(커밋 7bdc083). Claude 직접 검색은 결과 전체가 입력 토큰이고 이어 받기마다 다시 세며, Gemini 는 검색 결과가 사용량에 잡히지 않는다. 검색 엔진은 Google 을 믿는다. D-52 의 "리뷰는 작성 벤더 밖"은 이 렌즈에서만 풀린다. 나머지 두 렌즈·전체 읽기·수준 평가·중재 판정은 그대로 독립이다 | 검색만 Gemini 로 하고 판정은 다른 벤더가 하기(근거 수집 프롬프트와 판정 호출이 새로 든다), Claude 검색 횟수만 더 줄이기 |
| D-97 | 시간 초과(`LLMTransportError.timed_out`)는 처음 시도를 포함해 2번까지만 부른다(`agent_calls._MAX_TIMEOUT_ATTEMPTS`). 429·5xx·네트워크 오류는 `max_llm_retries` 그대로다 | 2026-11-11 토큰 조사: Grok 시간 초과 11건(시도당 600~1500초), 라운드 1C·4C 는 작성이 3번 다 시간 초과였다. 같은 입력이면 대개 또 넘고, 영수증은 0 토큰이어도 벤더가 생성을 끝까지 해 과금했을 수 있다 | 재시도 없음(일시 지연까지 잃는다), Grok 만 따로(다른 벤더도 같은 이유다) |
| D-99 | 설명문 파이프라인(`services/expository.py`, 9단계)의 프롬프트·단계별 모델도 같은 버전 문서에 둔다. 프롬프트는 `prompts.expository.{단계 key}`(본문은 JSON 지시문), 모델은 `routes["EXPOSITORY:{단계 key}"]` 다. 러너는 실행을 시작할 때 최신 버전에서 프롬프트·라우트·조절 값(`max_llm_retries`, 번역 게이트 기준)을 읽고, 호출은 `AgentCallService.call_text`(구조화 출력 없이 글로 받는 호출)로 보내 영수증에 `kind=EXPOSITORY`·`round_id={실행 id}`·`item_id={단계 key}`·`pipeline_version` 을 남긴다. 넣기는 `python -m cli.expository seed`(`PipelineVersionService.import_expository`), 한 단계 수정은 기존 `edit-prompt`·`edit-route`·내부 API 다. 검증은 R-13 이다. 라운드와 달리 실행은 버전을 고정하지 않는다(D-73 과 다르다): 같은 실행 폴더로 다시 부르면 그때의 최신 버전을 쓰고, 어느 버전의 호출인지는 영수증마다 남는다. 단계 결과는 D-100 | 2026-11-11 사용자: 프롬프트 버전 관리는 원래 Mongo 에서 하고 있었으니 그렇게 한다. 처음 러너(35d69e1)는 이미지 안의 `pipeline/expository/*.json` 을 읽어, 문구·모델을 바꾸려면 재배포해야 했고 어떤 프롬프트로 만든 글인지 남지 않았다. 버전을 고정하지 않는 이유는 프롬프트를 고친 단계부터 다시 돌리는 것이 이어 하기의 목적이라서다 | 별도 컬렉션(`edit-prompt`·API·내보내기를 다시 만들어야 한다), 파일 유지(재배포, 기록 없음), 실행마다 버전 고정(고친 프롬프트를 쓰려면 앞 단계부터 다시 불러야 한다) |
| D-100 | 설명문 파이프라인의 실행 결과는 Mongo `expository_runs` 문서다(`models/expository.ExpositoryRun`, `_id` 는 실행 id `expository-{UTC 시각}` 이고 영수증의 `round_id` 와 같다). 단계 결과(`stage_outputs`)와 그 결과를 만든 프롬프트(`stage_prompts`), 사실 확인 패치를 적용한 본문·버린 패치, 표를 넣은 본문, 완성된 글(`article`)을 담고 단계가 끝날 때마다 통째로 덮어쓴다. 결과와 프롬프트는 호출이 성공한 뒤에 같이 쓴다. 다시 부르기는 `run --redo-from N`, 결과 보기는 `show <실행 id>` 다. 단계 출력은 dict 로 둔다: 모양을 정하는 것이 버전 문서 프롬프트의 `expected_output_schema` 라서 코드 모델로 고정하면 프롬프트를 재배포 없이 고칠 수 없다. 러너가 읽는 필드는 `services/expository_checks.py` 가 검사한다 | 실행 폴더 파일은 컨테이너가 다시 뜨면 사라지고, 리포지토리가 컬렉션이 아닌 로컬 폴더를 다뤄 CONVENTIONS 2절과 달랐다. 프롬프트를 호출 전에 먼저 쓰던 순서는 호출이 실패하면 옛 결과가 새 프롬프트의 결과로 보이는 결함이었다 | 파일 유지(사라진다), 단계마다 문서 하나(이어 하기에 조인이 필요하다), 단계 출력을 Pydantic 계약으로(스키마가 코드와 데이터 두 곳에 생긴다) |
| D-101 | 설명문 파이프라인의 발행은 실행과 따로 `python -m cli.expository publish <실행 id>` 가 한다(`ExpositoryPipeline.publish`). 끝난 글(`article`)을 기존 `PostBEPublisher` 로 올린다: 작성(5단계) 모델 벤더의 발행 계정, 본문 뒤에 기존과 같은 모양의 AI Information(작성 줄, 사실 확인 모델의 리뷰 줄), `markdown_to_tiptap` 변환. 카테고리·키워드는 1단계 결과이고 명령 인자가 있으면 그 값이 이긴다(1단계를 사람이 준 실행은 카테고리가 없다. 쌍이 Post BE 에 없으면 고를 수 있는 쌍을 알려 준다). 한 실행은 한 번만 올린다: 결과는 실행 문서의 `publication`, 호출 전에 `publish_attempted_at` 을 적어 응답 전에 끊긴 발행을 다시 부르지 않게 하고(`--force` 로만 재시도), 확정 실패는 그 값을 지워 다시 올릴 수 있다. 단계 결과를 낸 모델은 `stage_models` 에 남긴다. 용어 사전·제작 기록·주제 레지스트리에는 쓰지 않는다 | 2026-11-11 사용자: 사람 승인을 뺐는데 글이 게시되지 않았다. 승인 토글(`publish.requires_approval`)은 라운드 파이프라인의 것이고 설명문 러너에는 발행이 없었다. 자동 발행이 아닌 명령인 이유는 첫 실제 글에서 고칠 점이 아직 나오기 때문이다 | `run` 끝에 자동 발행(품질이 안정된 뒤), 라운드의 `PublishingService` 재사용(`PostDraft`·라운드·슬롯 바인딩에 묶여 있다) |
| D-102 | 라우트 하나의 후보만 바꾸는 `edit-route` 를 둔다(`PipelineVersionService.edit_route`, CLI `agents pipeline edit-route`, 내부 API `POST /api/v1/agents/pipeline/routes/{route_id}/`). 최신 버전에 없는 라우트 id, 빈 후보, 지원표에 없는 조합(`routing.candidate_errors`)은 버전을 만들지 않고 거절한다. CLI 는 모든 후보에 같은 출력 한도·effort 를 주고, 후보마다 다르게 주려면 API 본문이나 `edit-routes` 를 쓴다 | 모델 하나를 바꾸려면 라우트 파일 전체를 다시 넣어야 했다(`edit-routes`). 설명문 파이프라인은 단계 모델이 버전 문서에 있는데도 바꾸는 길이 `routes.json` 수정 → 배포 → `seed` 뿐이라, 작성 모델을 Fable 로 바꾸는 데 배포가 필요했다(2026-11-12 사용자: 어떤 모델을 쓸지도 Mongo 에 있어야 한다) | `edit-routes` 만(모델 하나에 전체 파일), 없는 라우트도 만들기(오타가 아무도 읽지 않는 라우트를 만든다) |
| D-93 | 길이 상한을 둘로 나눈다. `draft_max_chars`(20000)는 작성·수정 프롬프트의 `structure_rules.max_chars` 로만 가고, 게이트는 `draft_reject_chars`(25000)를 넘을 때만 퇴짜 놓는다. R-09 는 `draft_max_chars ≤ draft_reject_chars` 를 본다. 이 필드가 없는 옛 버전 문서는 코드 기본값 25000 을 받는다 | 2026-11-10 라운드 edcf1b82 의 B 글은 초안 19,257자가 리뷰 반영으로 23,490자가 됐고, 줄이는 수정이 네 번 모두 20,000자 아래로 내려오지 못해 26만 토큰을 쓰고 FAILED 됐다. 프롬프트 목표를 올리면 초안이 더 길어지므로 목표는 두고 퇴짜 기준만 넓힌다 | 프롬프트 상한도 25000 으로 올리기 |

## 11. 결정 대기 항목

2026-11-07 사용자가 전부 정했다. 열린 항목은 없다. 결정은 10절 D-71·D-72·D-73·D-76·D-77·D-79·D-80~D-84 에 들어갔다.

| ID | 질문 | 영향 | 결정 |
|---|---|---|---|
| Q-32 | 환경별 활성 포인터와 `PIPELINE_ENV` 를 둘지 | 해소(2026-11-07, D-72): 두지 않는다. 최신이 활성이다 | – |
| Q-33 | `agent_main_categories`·`publish_post_level`·시간 상수·`llm_concurrency_per_provider` 를 어디에 둘지 | 환경 변수가 많을수록 배포 설정이 흩어진다 | 해소(D-82·D-83·D-84): `agent_main_categories` 삭제(Post BE 값으로 통일), `publish_post_level` 삭제(항상 보내고 Post BE 에 필드 추가), 타임아웃·동시성은 `core/config.py` 의 불변 객체 |
| Q-34 | 발행 머리말을 어디까지 데이터로 뺄지 | 긴 머리말은 제작 기록 블록과 겹친다 | 해소(D-80): 글 끝에 "작성 AI - 모델" 꼴로 짧게. 문구는 조절 값 |
| Q-35 | 다듬기 정규식·허용폭을 조절 값으로 둘지 | 정규식은 뜻을 보장하지 못한다 | 해소(D-81): 정규식을 쓰지 않는다. AI 가 프롬프트를 보고도 쓴 표현은 그대로 둔다. 허용폭만 조절 값 |
| Q-36 | 버전 id 형식 | 순번은 말하기 쉽고 순서가 보인다 | 해소(D-71): `v1`·`v2`…. 지금 개발 중인 것이 v1 이다. 영수증은 옛 프롬프트 디렉터리 이름과 겹치지 않게 `pipeline_version` 필드를 따로 쓴다(D-73) |
| Q-37 | 3단계 뒤 `app/agents/prompts/v1~v4` 디렉터리와 `agent_routes` 컬렉션을 지울지 | 디렉터리는 git 에 남는다 | 해소(D-79): 둘 다 3단계에서 바로 지운다. 지금 개발 중인 것이 v1 이라 보존할 옛것이 없다 |
| Q-38 | 작성·수정 템플릿의 "필수 구절" 검사를 `parameters.prompt_lint` 로 둘지 | 구절 검사는 문구를 바꾸면 lint 목록도 같이 고쳐야 한다 | 확정: `prompt_lint` 에 두고 위반은 경고 |
| Q-39 | 버전 문서 보존 정책 | 100 버전에 수십 MB | 해소: 정책 없음. 지우지 않는다. 아직 성공한 라운드가 없어 쌓인 것도 없다 |
| Q-40 | 버전 조회·되돌리기를 API 나 명령으로 열지 | 관리자 UI(Q-10) 전제 | 해소(D-77): 둘 다 두지 않는다. DB 에 넣어 두기만 한다. 보는 것은 Mongo 직접 조회·내보낸 파일 |
| Q-41 | 조건에 부정·OR 를 허용할지 | 식이 생기면 R-03 의 배타 판단이 어려워진다 | 확정: 이름 목록(AND)만. 부정은 레지스트리의 반대 조건 |
| Q-42 | `start-round --pipeline-version` 으로 최신이 아닌 버전의 라운드를 허용할지 | "라운드는 전부 최신" 규칙이 깨진다 | 해소(D-73): 허용. 차기 버전 프롬프트 개발 때 시험 라운드가 필요하다 |
