# CLAUDE.md

## 에이전트 파이프라인

세 LLM 에이전트가 주제 선정·작성·교차 리뷰를 거쳐 블로그 글을 발행하는 파이프라인이 검색 서비스와 같은 앱에 들어간다(설계 07 문서 D-01, 2026-10-30 변경). 설계는 `agents_design/`이다.

- 시작점은 [agents_design/README.md](agents_design/README.md)다. 확정 결정·미결 사항·구현 로드맵은 `07_decisions_and_open_questions.md`에 있다.
- 진행(2026-11-02): 로드맵 0~7단계 코드를 마쳤다(7단계는 도구만. 운영 판단은 남았다). 1단계는 `app/models/`·`services/evaluation.py`, 2단계는 `repositories/`(agent_round·post_draft·agent_call·topic_registry, optimistic_lock)·`clients/llm/`(base·registry)·`services/agent_calls.py`·`tests/fakes.py`의 `FakeLLMClient`·`FakeAsyncCollection`이다. 3단계는 `agents/`(personas·contracts·builders·parsers·prompts/v1)·`models/sections.py`·`models/tiptap.py`·`services/validation.py`다. 4단계는 `services/`(round·topic_selection·draft_pipeline·runner)·`dependencies.build_pipeline_runner`·lifespan 러너·CLI `agents run`이고, 테스트 하네스는 `tests/fakes.py`의 `PipelineHarness`·`ScriptedLLMClient`·`FakePostBE`다. 5단계는 `clients/llm/`(anthropic·google·xai·http)·`clients/publisher/`(base·post_be)·`RoundService.start_round`·`Settings` 벤더 키·`AGENT_SLOT_BINDINGS`·`POST_BE_URL`·CLI `agents start-round/status/abort`·`tests/e2e/test_llm_vendors.py`·`test_post_be.py` 다. 6단계는 `clients/publisher/post_be.py`(로그인·발행)·`services/publishing.py`·`services/draft_decision.py`·`routers/agent_rounds.py`·`schemas/agent_pipeline.py`·`services/pipeline_views.py`(조회·엔티티→응답 변환)·`tests/golden/agents_*.json`·CLI `agents show/resolve/approve/retry/discard`·`Settings`(`USER_SERVICE_URL`·`PUBLISHER_EMAIL`·`PUBLISHER_PASSWORD`·`AGENTS_INTERNAL_API_KEY`)다. 5·6단계 완료 기준(실제 모델로 라운드 1회 완주, 승인 후 Post BE 발행)은 키와 발행 계정을 `.env` 에 넣고 `pytest -m e2e tests/e2e/` → `agents start-round --wait` + `agents run` → `agents approve` 로 사용자가 확인한다. 7단계 도구는 `services/metrics.py`·`AgentCallService.replay`·CLI `agents metrics/replay`·`GET /api/v1/agents/metrics/` 다. 남은 일은 운영이다: 실제 라운드를 3회 이상 돌려 `agents metrics` 로 글당 토큰을 보고 Q-07(`DRAFT_TOKEN_LIMIT`)을 정하고, 판정 불일치·게이트 실패 지표로 프롬프트(`agents/prompts/v1`)를 튜닝하며, 그 뒤 `PUBLISH_REQUIRES_APPROVAL` 해제를 검토한다.
- 남은 결정: Q-14(MAJOR 분쟁도 중재할지)·Q-15(중재자 독립성)는 기본 가정으로 구현했다. Q-17(Mongo 위치)은 같은 Mongo·같은 DB로 정했다(D-43, 2026-11-02). Q-16(에이전트 내부 API 키)은 별도 `AGENTS_INTERNAL_API_KEY` 로 정했다(D-44, 2026-11-02).
- 문서끼리 서로 참조한다. 규칙 하나를 바꾸면 02(모델)·03(전이표)·04(오케스트레이션)·06(구조)·07(결정)을 함께 맞추고, 코드가 설계와 달라지면 같은 커밋에서 문서를 고친다.

## 코드 그래프 (code_graph)

내 저장소들의 의존 그래프를 이 앱이 만들고, 블로그(vans_story) 프로젝트 페이지가 그 JSON 을 그린다. 손으로 쓰는 아키텍처 문서·그림을 대체하는 것이 목적이다(블로그 `src/constants/projects.ts` 는 검색 서비스를 아직 Django·Redis 로, 기술 스택에 Storybook 을 적고 있다). Understand-Anything 의 클론이 아니다. LLM 요약·멀티 에이전트·임의 URL 입력·webhook·큐는 넣지 않는다.

- 설계 문서 세트는 만들지 않는다. 기획은 이 절, 사용법은 README 한 절, 나머지는 docstring 이다. 그래프 JSON 과 context 마크다운이 곧 문서다. 코드가 이 절과 달라지면 같은 커밋에서 이 절을 고친다.
- 진행(2026-11-03): 기획만. 코드 없음. 다음은 A 단계.

### 확인된 전제 (2026-11-03)

- 의존성 3개, 네이티브 빌드 없음: `tree-sitter==0.26.0`, `tree-sitter-language-pack==1.23.1`(win_amd64·manylinux_2_34 wheel. `python:3.13-slim` 에 그대로 설치된다), `networkx==3.7`(Louvain 내장).
- 이미지에 git 이 없다. `https://codeload.github.com/{owner}/{repo}/tar.gz/{ref}` 를 httpx 로 받아 `tarfile`(`filter="data"`)로 임시 디렉터리에 풀고 끝나면 지운다. 네 저장소(vans_devblog_fastapi, vans_story, vans_story_be_post, vans_story_be)가 모두 public 이라 토큰이 없다.
- 1회용 PoC(tree-sitter + networkx, 소스 루트 바로 아래 디렉터리를 레이어로) 결과:

| 저장소 | 소스 루트 | 파일 | 파싱 | 심볼 | 내부 import 엣지 |
|---|---|---|---|---|---|
| vans_devblog_fastapi | `app` | 85 | 0.19s | 613 | 252 |
| vans_story | `src` | 241 | 0.87s | 850 | 484 |
| vans_story_be_post | `src` | 55 | 1.44s | 106 | 90 |
| vans_story_be | `src/main/kotlin` | 44 | 0.12s | 217 | 59 |

  이 앱은 CONVENTIONS 1절 표에 없는 import 가 6건이고 전부 그 절이 선언한 예외다(`routers/agent_rounds.py → models/enums.py`, `schemas/agent_pipeline.py → models/{enums, model_binding, post_draft, publishing_round, topic}.py`). 그 밖의 역행은 없다. vans_story 는 `components → app` import 가 17건이다. 분석이 1~2초라 큐·워커는 두지 않는다.
- 서비스 간 호출은 코드에 경로가 문자열로 있다. vans_story BFF `src/app/api/**/route.ts` 27개가 환경변수 6개(POST_API_URL 7건, AUTH_API_URL 5, SEARCH_API_URL 3, DJANGO_API_URL 2, IMAGE_API_URL 1, AI_API_URL 1)로 상류를 가리킨다. NestJS → 이 앱 `/api/internal/search/index/`(`src/search-service/search-service.client.ts`), NestJS → Spring `/api/v1/users/email/{email}`(`src/user-service/user-service.client.ts`), 이 앱 → NestJS `/api/v1/categories`·`/api/v1/posts`, 이 앱 → Spring `/api/v1/auth/login`(`clients/publisher/post_be.py`). Spring 은 밖으로 부르지 않는다.
- 세 백엔드 모두 OpenAPI 를 낸다: 이 앱 `/openapi.json`, NestJS `api/posts/swagger`(SwaggerModule), Spring springdoc 3.0.2 `/v3/api-docs`. 엔드포인트와 요청·응답 스키마는 파싱하지 않고 여기서 받는다.
- vans_story 에는 mermaid 12 와 `MermaidDiagram` 컴포넌트가 있다. 뷰어는 서버가 만든 mermaid 텍스트로 시작한다.

### 결정

| # | 결정 | 이유 |
|---|---|---|
| G-01 | 이름은 `code_graph`. `analysis`·`analyzer` 는 쓰지 않는다 | `models/analyzers.py` 가 ES Nori 분석기다 |
| G-02 | 검색 경로와 같은 동기 쪽에 둔다: 라우터 `def`, 분석은 `BackgroundTasks`, 저장은 동기 `MongoClient` | CPU 작업(파싱·networkx)이 이벤트 루프를 막으면 안 된다(CONVENTIONS 3절). 러너·비동기 Mongo 는 필요 없다 |
| G-03 | 분석 트리거는 `POST .../analyze/` + `X-Internal-Key`(`INTERNAL_API_KEY`, `verify_internal_key`). 조회는 공개, `core/cache` TTL | 동기화 실행(`POST sync/`)과 같은 운영자 동작이다 |
| G-04 | 저장소 목록·규칙은 `Settings.code_graph_repos`(JSON, `AGENT_SLOT_BINDINGS` 와 같은 방식). 비어 있으면 기능이 꺼진다 | 임의 URL 을 받지 않는다. `.env.example` 에 네 저장소 전체 예시를 둔다 |
| G-05 | 레이어와 레이어별 허용 import 쌍, 파일 단위 예외는 추론하지 않고 저장소마다 선언한다 | 유일하게 남는 손 문서다. 순서 하나로는 이 앱의 표(services → schemas 허용, models ↔ schemas 금지)를 못 적는다. 틀리면 도구가 위반으로 보여 준다 |
| G-06 | orphan·hub 진단은 저장소별 토글. vans_story·이 앱만 켠다 | Spring·NestJS 는 DI 라 import 그래프가 의존을 반영하지 않는다 |
| G-07 | 순환은 `strongly_connected_components` 크기 2 이상 | `simple_cycles` 는 밀집 그래프에서 지수적이다 |
| G-08 | 서비스 그래프는 파일 그래프와 분리한다. OpenAPI 조회 실패는 서비스 그래프만 비운다 | 네 서비스에 네트워크를 타는 유일한 부분이다 |
| G-09 | 결과는 Mongo `code_graphs` 컬렉션, 문서 하나 = (repo, commit_sha). 같은 commit 재요청은 저장된 것을 돌려준다. 분석 실패는 경고 로그만 남기고 이전 결과를 유지한다 | 멱등. 문서가 수십 KB 라 한 번에 내려 준다 |

### 저장소 설정 (`CODE_GRAPH_REPOS`)

저장소 키 → `{owner, name, ref, source_roots, layers[{id, may_import[]}], allowed_edges[{source, target}], entry_points(glob), diagnostics(orphans·hubs 토글), openapi_url, service(이름), client_env(BFF 가 이 서비스를 가리키는 환경변수 이름들)}`.

- 레이어는 소스 루트 바로 아래 디렉터리 이름이고, `may_import` 는 그 레이어가 import 해도 되는 레이어 목록이다. 같은 레이어 안은 항상 허용. 위반 = 표에 없는 쌍 중 `allowed_edges` 에 없는 것. 이 앱은 CONVENTIONS 1절 표를 그대로 옮기고 `allowed_edges` 에 위 예외 6건을 둔다. vans_story 는 그쪽 CONVENTIONS 1장, Kotlin 은 패키지 루트(`blog/vans_story_be`) 아래 `config, domain, global`.
- Next.js 의 `page.tsx`·`layout.tsx`·`route.ts`, 이 앱의 `main.py`·`cli/`, NestJS 의 `main.ts`·`*.module.ts` 는 엔트리포인트라 orphan 에서 뺀다.

### 코드 배치 (기존 계층, 새 폴더 없음)

| 파일 | 역할 |
|---|---|
| `clients/github.py` | tarball 받기(타임아웃·크기 상한), 임시 디렉터리에 풀기·삭제 |
| `clients/openapi.py` | 각 서비스의 OpenAPI 문서 받기 (B) |
| `models/code_graph.py` | `CodeGraph`(repo, commit_sha, analyzed_at, tech_stack, layers, nodes, edges, diagnostics)와 노드·엣지·진단 값 객체. I/O 없는 계산(레이어 위반 판정 등) |
| `repositories/code_graph.py` | `code_graphs` 컬렉션 upsert·최신 조회 |
| `services/code_graph.py` | 파싱(언어별 정의·import 추출, 내부 경로 해석) → DiGraph → 진단 → 엔티티. context 마크다운 생성 |
| `services/service_graph.py` | BFF·클라이언트의 호출 지점 추출 + OpenAPI 매칭 → 서비스 그래프·흐름·mermaid (B) |
| `schemas/code_graph.py` | 응답 스키마 |
| `routers/code_graph.py` | 아래 API |
| `cli/__main__.py` | `code-graph analyze <repo>`, `code-graph context <repo>` 추가 |
| `tests/` | 같은 구조. 파서 테스트는 `tests/golden/code_graph/` 에 작은 소스 트리를 두고 기대 그래프와 비교 |

언어별 추출 규칙(PoC 에서 확인): Python `import_statement`·`import_from_statement` → `app.x.y` 를 파일로. TS/TSX `import_statement` 의 `source` 가 `@/`·`src/`·상대경로면 파일로(`posts.service` 처럼 점이 든 이름은 `with_suffix` 가 아니라 이름 뒤에 확장자를 붙여 찾는다). Kotlin `import_header` 의 패키지 경로를 파일로. 외부 패키지는 집계만 한다. 정의 노드: Python `function_definition`·`class_definition`, TS `function_declaration`·`class_declaration`·`interface_declaration`·`type_alias_declaration`·`enum_declaration`·`method_definition`, Kotlin `function_declaration`·`class_declaration`·`object_declaration`.

### API (`/api/v1/code-graph/`, trailing slash)

| 메서드·경로 | 인증 | 응답 |
|---|---|---|
| `POST {repo}/analyze/` | `X-Internal-Key` | 202 `{repo, commit_sha, status}`. 백그라운드로 분석·저장. 같은 commit 이 이미 있으면 200 으로 저장된 요약 |
| `GET /` | 공개 | 저장소별 최신 commit_sha·analyzed_at·진단 건수 |
| `GET {repo}/` | 공개, 캐시 | 전체 그래프 + 진단(아래 구조) |
| `GET {repo}/context/` | 공개 | `text/markdown`. 레이어 순서·파일 수, 허브 파일, 현재 위반("더 만들지 말 것"), 순환, orphan. CLAUDE.md 갱신·프롬프트 주입용 |
| `GET services/` | 공개, 캐시 | 서비스 그래프: 노드 = 엔드포인트, 엣지 = 호출 (B) |
| `GET flows/{flow}/` | 공개 | 한 e2e 경로(블로그 페이지 → BFF route → 백엔드 → 백엔드)와 hop 마다의 요청·응답 스키마, mermaid sequenceDiagram 텍스트 (B) |

`GET {repo}/` 구조: `repo, commit_sha, analyzed_at, tech_stack[], layers[{id, may_import[], file_count}], diagnostics{layer_violations[{source, target, from_layer, to_layer}], cycles[[path, ...]], orphan_files[], hub_files[{path, in_degree, out_degree, symbol_count}]}, graph{nodes[{id, layer, symbols[], in_degree, out_degree}], edges[{source, target, status: valid | violation}]}`. 영향 반경(ancestors·descendants, N-hop)은 프론트가 이 JSON 으로 계산한다.

### 로드맵

| 단계 | 내용 | 완료 기준 |
|---|---|---|
| A | 파일 그래프 + 진단 + context 마크다운. 이 앱 자기 자신부터, 이어서 나머지 셋 | `code-graph analyze vans_devblog_fastapi` 가 Mongo 에 저장하고 `GET {repo}/context/` 가 CONVENTIONS 표와 같은 레이어·허용 쌍을 돌려주고, 선언된 예외 6건을 뺀 위반이 0 이다. vans_story 에서 `components → app` 17건이 위반으로 나온다 |
| B | 서비스 그래프: BFF `route.ts`·`search-service.client.ts`·`user-service.client.ts`·`clients/publisher/post_be.py` 의 호출 지점 → 환경변수 이름으로 서비스 식별 → OpenAPI 경로 매칭. flows + mermaid | 검색(블로그 검색 페이지 → `/api/search` → 이 앱 `/api/v1/search/posts/`)과 글 발행(에이전트 → NestJS `/api/v1/posts` → 이 앱 `/api/internal/search/index/`) 두 흐름이 hop 마다 스키마 이름과 함께 나온다 |
| C | vans_story 뷰어(별도 저장소): 저장소 탭, 좌측 진단 목록·파일 검색, 중앙 mermaid(레이어 블록 / 선택 파일 ego-network / 흐름 시퀀스), 우측 파일 인스펙터(심볼·import·GitHub 링크), 관리자에게만 재분석 버튼. `projects.ts` 의 손 문서를 이 뷰로 바꾼다 | 블로그 프로젝트 페이지에서 보인다. 캔버스 라이브러리는 ego-network 가 안 읽힐 때만 |
| 뒤 | LLM 노드 요약(해시 캐시), commit 간 diff, e2e 테스트 커버리지 오버레이(`tests/e2e` 가 치는 경로를 엔드포인트에 표시) | 필요해지면 |
