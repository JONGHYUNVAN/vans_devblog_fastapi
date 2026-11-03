# 코드 그래프 (code_graph) 기획

내 저장소들의 의존 그래프를 이 앱이 만들고, 블로그(vans_story) 프로젝트 페이지가 그 JSON 을 그린다. 손으로 쓰는 아키텍처 문서·그림을 대체하는 것이 목적이다(블로그 `src/constants/projects.ts` 는 검색 서비스를 아직 Django·Redis 로, 기술 스택에 Storybook 을 적고 있다). Understand-Anything 의 클론이 아니다. LLM 요약·멀티 에이전트·임의 URL 입력·webhook·큐는 넣지 않는다.

- 설계 문서 세트는 만들지 않는다. 기획은 이 문서 하나, 저장소 설정 예시는 [repos.example.json](repos.example.json), 사용법은 저장소 README 한 절, 나머지는 docstring 이다. 그래프 JSON 과 context 마크다운이 곧 문서다. 코드가 이 문서와 달라지면 같은 커밋에서 이 문서를 고친다.
- 이 문서의 수치·경로는 전부 2026-11-03 에 저장소·tarball·코드에서 확인한 것이다. 확인하지 않은 값은 "확인 필요" 라고 적는다.

## 확인된 전제 (2026-11-03)

- 의존성 3개, 네이티브 빌드 없음: `tree-sitter==0.26.0`, `tree-sitter-language-pack==1.23.1`(win_amd64·manylinux_2_34 wheel. `python:3.13-slim` 에 그대로 설치된다), `networkx==3.7`(Louvain 내장).
- 이미지에 git 이 없다. `https://codeload.github.com/JONGHYUNVAN/{repo}/tar.gz/{ref}` 를 httpx 로 받아 `tarfile`(`filter="data"`)로 임시 디렉터리에 풀고 끝나면 지운다. 네 저장소(vans_devblog_fastapi, vans_story, vans_story_be_post, vans_story_be)가 모두 public 이라 토큰이 없다. 소유자는 `JONGHYUNVAN` 이다(다른 표기는 404).
- tarball 구조: 최상위 디렉터리는 `{repo}-{ref}`(예: `vans_story-main`)이고, 커밋 해시는 pax 전역 헤더 `comment` 에 전체 SHA 로 들어 있다(네 저장소 모두 확인). 크기는 vans_story 30.4MB(`public/docs` 의 HTML 470개 포함), 나머지 셋은 300KB 이하다.
- 1회용 PoC(tree-sitter + networkx, 소스 루트 바로 아래 디렉터리를 레이어로) 결과:

| 저장소 | 소스 루트 | 파일 | 파싱 | 심볼 | 내부 import 엣지 |
|---|---|---|---|---|---|
| vans_devblog_fastapi | `app` | 85 | 0.19s | 613 | 252 |
| vans_story | `src` | 241 | 0.87s | 850 | 484 |
| vans_story_be_post | `src` | 55 | 1.44s | 106 | 90 |
| vans_story_be | `src/main/kotlin` | 44 | 0.12s | 217 | 59 |

  이 앱은 CONVENTIONS 1절 표에 없는 import 가 6건이고 전부 그 절이 선언한 예외다(`routers/agent_rounds.py → models/enums.py`, `schemas/agent_pipeline.py → models/{enums, model_binding, post_draft, publishing_round, topic}.py`). `cli/__main__.py → models/enums.py` 도 같은 예외다(PoC 는 `cli/` 를 훑지 않았다). 그 밖의 역행은 없다. 분석이 1~2초라 큐·워커는 두지 않는다.
- 실제 디렉터리: 이 앱 `app/{routers, services, repositories, clients, models, schemas, agents, core}` + 루트 `cli/`. vans_story `src/{app, components, constants, hooks, lib, messages, store, types}`(`features`·`services`·`utils` 는 없다. 그쪽 CONVENTIONS 1장이 `utils/` 등을 만들지 않는다고 정했다. `src/middleware.ts` 도 없다). vans_story_be_post `src/{auth, categories, common, config, posts, search-service, swagger, user-service}`. vans_story_be `src/main/kotlin/blog/vans_story_be/{config, domain, global}`(빌드 파일은 `build.gradle`, kts 아님).
- 레이어 간 import 전체 표(PoC): vans_story 는 `components → app` 17, `lib → store` 2, `lib → components` 1, `constants → lib` 1 이 방향이 이상하고 나머지는 위에서 아래로만 간다. vans_story_be_post 는 `posts → {auth 7, common 5, categories 4, user-service 3, search-service 3}`, `categories → {auth 5, common 1}` 이고 역행이 없다. vans_story_be 는 `config → {domain 7, global 1}`, `domain → global 9` 다.
- 서비스 간 호출은 코드에 경로가 문자열로 있다. vans_story BFF `src/app/api/**/route.ts` 27개가 환경변수 6개(POST_API_URL 7건, AUTH_API_URL 5, SEARCH_API_URL 3, DJANGO_API_URL 2, IMAGE_API_URL 1, AI_API_URL 1)로 상류를 가리킨다. 환경변수마다 기본값에 경로 접두가 붙어 있다: POST_API_URL `/api/v1`, AUTH_API_URL `/api`, IMAGE_API_URL `/api`, SEARCH_API_URL·DJANGO_API_URL 없음(`src/constants/apiConfig.ts`, 각 route.ts 기본값). SEARCH_API_URL·DJANGO_API_URL 만 이 앱이고, AI_API_URL(AI 채팅, 기본 :3003)·IMAGE_API_URL(이미지, 기본 :3002)은 네 저장소 밖의 서비스다.
- BFF 가 상류 URL 을 만드는 패턴은 세 가지다: ① `process.env.X` 를 `const` 에 받아 템플릿 리터럴에서 쓴다(`search/route.ts` 의 `searchApiUrl`), ② 인자 없는 함수로 감싼다(`stocks/kis/**` 의 `djangoApiUrl()`), ③ `lib/api/apiProxy.ts` 의 `proxyGet/proxyPost/proxyPatch/proxyDelete({baseUrl, path})` 헬퍼에 넘긴다(autocomplete·popular 등). 템플릿 안에 `process.env` 가 직접 들어가는 경우는 없다.
- 백엔드 간: NestJS → 이 앱 `POST /api/internal/search/index/`·`DELETE .../{post_id}/`(`src/search-service/search-service.client.ts`. `posts.service.ts` 가 생성·수정·삭제 때 fire-and-forget 으로 부른다), NestJS → Spring `GET /api/v1/users/email/{email}`(`src/user-service/user-service.client.ts`), 이 앱 → NestJS `GET /api/v1/categories`·`POST /api/v1/posts`, 이 앱 → Spring `POST /api/v1/auth/login`(`clients/publisher/post_be.py`). Spring 은 밖으로 부르지 않는다.
- 세 백엔드 모두 OpenAPI 를 낸다: 이 앱 `/openapi.json`(같은 프로세스라 `app.openapi()` 로 받을 수 있다), NestJS `GET /api/posts/swagger-json`(`SwaggerModule.setup("api/posts/swagger")`, spec 테스트로 확인), Spring springdoc 3.0.2 기본 `/v3/api-docs`. 호스트는 이 앱 `Settings` 의 `post_be_url`·`user_service_url` 을 그대로 쓴다. 코드에 적힌 운영 호스트는 `https://vansdevblog.online` 하나뿐이라 백엔드 호스트를 여기 하드코딩하지 않는다.
- vans_story 에는 mermaid 12 와 `MermaidDiagram` 컴포넌트가 있다. 뷰어는 서버가 만든 mermaid 텍스트로 시작한다.

## 결정

| # | 결정 | 이유 |
|---|---|---|
| G-01 | 이름은 `code_graph`. `analysis`·`analyzer` 는 쓰지 않는다 | `models/analyzers.py` 가 ES Nori 분석기다 |
| G-02 | 검색 경로와 같은 동기 쪽에 둔다: 라우터 `def`, 분석은 `BackgroundTasks`, 저장은 동기 `MongoClient` | CPU 작업(파싱·networkx)이 이벤트 루프를 막으면 안 된다(CONVENTIONS 3절). 러너·비동기 Mongo 는 필요 없다 |
| G-03 | 분석 트리거는 `POST .../analyze/` + `X-Internal-Key`(`INTERNAL_API_KEY`, `verify_internal_key`). 조회는 공개, `core/cache` TTL | 동기화 실행(`POST sync/`)과 같은 운영자 동작이다 |
| G-04 | 저장소 목록·규칙은 `Settings.code_graph_repos`(JSON, `AGENT_SLOT_BINDINGS` 와 같은 방식). 비어 있으면 기능이 꺼진다. 네 저장소 전체 예시는 [repos.example.json](repos.example.json) 이고 `.env.example` 은 그 파일을 가리킨다 | 임의 URL 을 받지 않는다. 100줄짜리 JSON 을 env 한 줄로 두면 읽을 수 없다 |
| G-05 | 레이어와 레이어별 허용 import 쌍, 파일 단위 예외는 추론하지 않고 저장소마다 선언한다 | 유일하게 남는 손 문서다. 순서 하나로는 이 앱의 표(services → schemas 허용, models ↔ schemas 금지)를 못 적는다. 틀리면 도구가 위반으로 보여 준다 |
| G-06 | orphan·hub 진단은 저장소별 토글. vans_story·이 앱만 켠다 | Spring·NestJS 는 DI 라 import 그래프가 의존을 반영하지 않는다 |
| G-07 | 순환은 `strongly_connected_components` 크기 2 이상 | `simple_cycles` 는 밀집 그래프에서 지수적이다 |
| G-08 | 서비스 그래프는 파일 그래프와 분리한다. OpenAPI 조회 실패는 서비스 그래프의 스키마 정보만 비운다 | 네 서비스에 네트워크를 타는 유일한 부분이다 |
| G-09 | 결과는 Mongo `code_graphs` 컬렉션, 문서 하나 = (repo, commit_sha). 같은 commit 재요청은 저장된 것을 돌려준다. 분석 실패는 경고 로그만 남기고 이전 결과를 유지한다 | 멱등. 문서가 수십 KB 라 한 번에 내려 준다 |
| G-10 | `commit_sha` 는 tarball pax 헤더 `comment` 에서만 읽는다 | git 도 GitHub API 도 필요 없다. 최상위 디렉터리명은 `{repo}-{ref}` 라 SHA 가 아니다 |
| G-11 | 분석 요청은 서비스 메서드 하나가 "tarball 받기 → SHA → 저장된 문서가 있으면 그것(200), 없으면 파싱·저장을 `BackgroundTasks` 로(202)" 를 한다. 라우터는 호출만 한다. 다운로드 타임아웃·크기 상한은 `Settings`(`CODE_GRAPH_DOWNLOAD_TIMEOUT_S`=30, `CODE_GRAPH_MAX_TARBALL_MB`=64) | 응답에 `commit_sha` 를 바로 담으려면 받아야 한다. vans_story 가 30MB 라 15초는 빡빡하다. 다운로드를 라우터에 두면 CONVENTIONS 2절 위반이다 |
| G-12 | `tech_stack` 은 매니페스트(`pyproject.toml`·`requirements.txt`, `package.json`, `build.gradle`)에 선언된 의존성 이름·버전만 적는다. "실제 import 된 것" 과의 교집합은 내지 않는다 | PyPI 이름과 import 이름이 다르다(pycryptodome → `Crypto`, pydantic-settings → `pydantic_settings`, markdown-it-py → `markdown_it`, tzdata 는 import 없음). Gradle 좌표와 Kotlin 패키지는 대응이 없다 |
| G-13 | 서비스 간 호출 지점(`outbound_calls`)은 파일 분석 때 뽑아 `CodeGraph` 문서에 저장한다. OpenAPI 매칭(엔드포인트 존재·스키마 이름)만 조회 때 하고 `core/cache` TTL 로 묶는다. 실패도 같은 TTL 로 캐시한다. 이 앱 자신의 OpenAPI 는 `dependencies.py` 가 `app.openapi` 를 넘겨 HTTP 없이 받고, NestJS·Spring 은 `post_be_url`·`user_service_url` + 고정 경로다 | OpenAPI 가 안 와도 호출 토폴로지는 남는다. 공개 GET 이 바깥을 매번 두드리지 않는다 |
| G-14 | 레이어는 경로 접두(`path`)로 선언한다. 소스 루트 아래에 있지만 어느 레이어에도 안 속한 파일(`app/main.py`, `app/dependencies.py`, `src/main.ts`, `src/app.module.ts`, `VansStoryBeApplication.kt`)은 `root` 레이어이고, `root` 가 source 든 target 이든 위반으로 세지 않는다 | 이 앱의 `cli/` 는 `app/` 밖이라 "소스 루트 바로 아래 디렉터리" 규칙으로는 못 적는다. `routers → dependencies.py` 6건은 정상이다 |
| G-15 | vans_story·vans_story_be_post 의 허용 표는 그쪽에 import 규칙 문서가 없어 여기서 처음 선언한 제안이다. 첫 분석의 위반 목록을 보고 코드를 고칠지 표를 넓힐지 정한다 | 그쪽 CONVENTIONS 는 파일을 어디에 둘지만 정한다 |

## 저장소 설정 (`CODE_GRAPH_REPOS`)

전문은 [repos.example.json](repos.example.json). 저장소 키 → `{owner, name, ref, source_roots, layers[{id, path, may_import[]}], allowed_edges[{source, target}], entry_points(glob), diagnostics{orphans, hubs}, openapi, service, client_env{환경변수: 경로 접두}}`.

- `layers[].path` 는 저장소 루트 기준 디렉터리 접두다. `may_import` 는 그 레이어가 import 해도 되는 레이어 id 목록이고 같은 레이어 안은 항상 허용이다. 위반 = `source.layer ≠ root`, `target.layer ≠ root`, `target.layer ∉ may_import(source.layer)`, `(source, target) ∉ allowed_edges`.
- 이 앱: CONVENTIONS 1절 표 그대로. `routers`·`cli → services, schemas, core`, `services → repositories, models, schemas, agents, clients, core`, `agents → models, core`, `repositories → models, clients, core`, `models`·`schemas`·`clients → core`, `core → 없음`. `allowed_edges` 는 위 예외 7건.
- vans_story(제안, G-15): `app → components, hooks, lib, store, constants, types`, `components → hooks, lib, store, constants, types`, `hooks → lib, store, constants, types`, `store → lib, constants, types`, `lib → constants, types`, `constants → types`, `types → 없음`. PoC 기준 위반 21건(`components → app` 17, `lib → store` 2, `lib → components` 1, `constants → lib` 1).
- vans_story_be_post(제안, G-15): `posts → categories, auth, common, config, search-service, user-service`, `categories → auth, common, config`, `auth`·`search-service`·`user-service`·`swagger → common, config`, `common → config`, `config → 없음`. PoC 기준 위반 0.
- vans_story_be: `config → domain, global`, `domain → global`, `global → 없음`. PoC 기준 위반 0.
- 엔트리포인트(orphan 제외): 이 앱 `app/main.py`·`app/dependencies.py`·`cli/__main__.py`. vans_story `src/app/**/{page,layout,loading,error,not-found,template}.tsx`·`src/app/**/route.ts`·`src/app/{robots,sitemap}.ts`. NestJS `src/main.ts`·`src/**/*.module.ts`. Spring `**/VansStoryBeApplication.kt`. Python `__init__.py` 와 TS 배럴(`index.ts(x)` 중 `export ... from` 만 있는 파일)도 orphan 에서 뺀다.
- `openapi`: 이 앱 `"self"`, NestJS `{"base_setting": "post_be_url", "path": "/api/posts/swagger-json"}`, Spring `{"base_setting": "user_service_url", "path": "/v3/api-docs"}`, vans_story `null`.
- `client_env` 에 없는 환경변수(AI_API_URL, IMAGE_API_URL)는 서비스 그래프에서 환경변수 이름을 가진 외부 노드로 그린다.

## 코드 배치 (기존 계층, 새 폴더 없음)

| 파일 | 역할 |
|---|---|
| `clients/github.py` | tarball 받기(타임아웃·크기 상한), pax `comment` 에서 SHA, 임시 디렉터리에 풀기·삭제 |
| `clients/openapi.py` | NestJS·Spring 의 OpenAPI 문서 받기 (B) |
| `models/code_graph.py` | `CodeGraph`(repo, commit_sha, analyzed_at, tech_stack, layers, nodes, edges, diagnostics, outbound_calls)와 노드·엣지·진단 값 객체. I/O 없는 계산(레이어 위반 판정, 순환, orphan, hub, context 마크다운) |
| `repositories/code_graph.py` | `code_graphs` 컬렉션 upsert·최신 조회·보관 정리 |
| `services/code_graph.py` | 분석 유스케이스(G-11), 파싱(언어별 정의·import 추출, 2-pass 경로 해석) → DiGraph → 진단 → 엔티티 |
| `services/service_graph.py` | 저장된 `outbound_calls` + OpenAPI 매칭 → 서비스 그래프·흐름·mermaid (B) |
| `schemas/code_graph.py` | 응답 스키마 |
| `routers/code_graph.py` | 아래 API |
| `cli/__main__.py` | `code-graph analyze <repo>`, `code-graph context <repo>` 추가 |
| `tests/` | 같은 구조. 파서 테스트는 `tests/golden/code_graph/` 에 작은 소스 트리를 두고 기대 그래프와 비교 |

## 파싱 규칙 (`services/code_graph.py`)

Pass 1 이 파일·심볼·패키지 인덱스를 만들고 Pass 2 가 import 를 해석해 엣지를 만든다. 제외: `node_modules`, `.git`, `.venv`, `__pycache__`, `.next`, `dist`, `build`, `coverage`, `public`, `tests`·`test`·`__tests__`·`e2e`, `*.spec.ts`, `*.test.ts(x)`, `*.d.ts`. 외부 패키지는 이름만 집계한다.

| 언어 | 정의 노드 | 의존 노드 | 내부 경로 해석 |
|---|---|---|---|
| Python | `function_definition`, `class_definition` | `import_statement`, `import_from_statement` | 상대 import 는 현재 패키지 기준 절대 모듈로 바꾼다. `from app.a.b import c` 는 `app/a/b/c.py` → `app/a/b/c/__init__.py` → `app/a/b.py` → `app/a/b/__init__.py` 순으로 찾는다 |
| TS/TSX | `function_declaration`, `class_declaration`, `interface_declaration`, `type_alias_declaration`, `enum_declaration`, `method_definition`, 최상위 `lexical_declaration` 의 `arrow_function` 바인딩(함수형 컴포넌트·훅) | `import_statement`, `source` 가 있는 `export_statement`(`export ... from`, `export * from`) | `@/` → `src/`, `src/` 는 그대로(NestJS tsconfig baseUrl, 35건), `./`·`../` 는 파일 기준. 그 다음 `{path}` → `{path}.ts` → `{path}.tsx` → `{path}/index.ts` → `{path}/index.tsx`. `posts.service` 처럼 점이 든 이름 때문에 `with_suffix` 가 아니라 이름 뒤에 붙인다 |
| Kotlin | `class_declaration`, `object_declaration`, `function_declaration` | `package_header`, `import_header` | Pass 1 에서 `패키지.심볼 → 파일`, `패키지 → 파일들` 맵을 만든다. 내부 접두(`blog.vans_story_be.`)인 import 만 해석하고, `.*` 는 패키지의 모든 파일로 잇는다. 같은 패키지 안의 암묵 참조는 보이지 않는다(context 마크다운에 명시) |

## 진단 계산식과 context 마크다운 (`models/code_graph.py`)

- 레이어 위반: 위 "저장소 설정" 의 정의. 해당 엣지는 `graph.edges[].status = "violation"`.
- 순환: `strongly_connected_components` 중 크기 2 이상인 집합마다 `find_cycle(G.subgraph(sorted(scc)))` 로 대표 경로 하나를 `[a, b, ..., a]` 로 적는다.
- orphan: `diagnostics.orphans` 가 켜진 저장소에서 `in_degree == 0` 이고 `entry_points` 에 안 걸리며 `__init__.py`·배럴이 아닌 파일. 오름차순.
- hub: `diagnostics.hubs` 가 켜진 저장소에서 `in_degree >= 5` 인 파일을 `in_degree` 내림차순(동률은 `out_degree` 내림차순)으로 최대 15개.
- `GET {repo}/context/` 템플릿:

```markdown
# Code Graph Context: {repo} (`{commit_sha[:7]}`)

- Analyzed At: {analyzed_at}
- Files: {node_count} | Internal Edges: {edge_count} | Symbols: {symbol_count}
- Tech Stack: {tech_stack 쉼표 구분}

## 1. Layers & Allowed Imports
| Layer | Files | May Import |
|---|---|---|
| {layer.id} | {layer.file_count} | {layer.may_import} |

## 2. Layer Violations (Do Not Add More)
- Violations ({violation_count}):
  - `{source}` ({from_layer}) -> `{target}` ({to_layer})
- Declared Exceptions ({allowed_edge_count}):
  - `{source}` -> `{target}`

## 3. Cycles & Orphans
- Cycles ({cycle_count}): `{a} -> {b} -> {a}`
- Orphans ({orphan_count}): `{path}`

## 4. Hub Files (Top In-Degree)
| File | Layer | In | Out | Key Symbols |
|---|---|---|---|---|
| `{path}` | {layer} | {in_degree} | {out_degree} | {정의 순서 앞 5개} |
```

## 서비스 그래프 (`services/service_graph.py`, B)

1. 호출 지점 추출(분석 때, `outbound_calls[{caller_file, caller_symbol, env_var, http_method, raw_path}]`):
   - vans_story `src/app/api/**/route.ts`: 파일이 export 하는 `GET`·`POST`·`PUT`·`PATCH`·`DELETE` 함수를 inbound 엔드포인트(`/api/...`, 파일 경로에서)로 등록한다. 상류는 세 패턴을 모두 본다. ① `process.env.X` 를 받은 `const`·② 인자 없는 함수를 한 단계 따라가 템플릿 리터럴 `${alias}{path}` 에서 `env_var`·`raw_path` 를 뽑고 `fetch` 옵션 `method`(기본 GET). ③ `proxyGet/proxyPost/proxyPatch/proxyDelete({baseUrl: process.env.X || ..., path: ...})` 는 객체 리터럴에서 뽑고 메서드는 헬퍼 이름이다.
   - vans_story_be_post `src/**/*-service.client.ts`: `fetch`·`HttpService` 호출의 경로 문자열과 메서드.
   - 이 앱 `app/clients/publisher/post_be.py`: `client.get/post(f"{self._x_url}/path")` 의 경로와 메서드.
2. 정규화: `raw_path` 의 `${...}`·`{...}` 를 `{}` 로, 끝 슬래시를 떼고 비교 키를 만든다. `env_var` 를 `client_env` 에서 찾아 대상 저장소와 **경로 접두**를 얻고(`POST_API_URL` 은 `/api/v1`), 접두 + 비교 키를 OpenAPI `paths`(같은 방식으로 `{param}` → `{}`)와 맞춘다.
3. 스키마: `requestBody.content["application/json"].schema` 와 `responses["200"|"201"|"202"].content["application/json"].schema` 의 `$ref` 마지막 이름. `type: array` 면 `items.$ref` 를 본다(이 앱의 목록 응답).
4. `GET flows/{flow}/`:

| flow | 시작 | hop |
|---|---|---|
| `search` | vans_story `src/app/api/search/route.ts` | 블로그 검색 → BFF `/api/search` → 이 앱 `GET /api/v1/search/posts/` |
| `publish` | 이 앱 `app/clients/publisher/post_be.py` | 에이전트 발행 → Spring `POST /api/v1/auth/login` → NestJS `POST /api/v1/posts` → 이 앱 `POST /api/internal/search/index/` |
| `posts` | vans_story `src/app/api/posts/route.ts` | 글 목록·작성 → BFF `/api/posts` → NestJS `/api/v1/posts` → Spring `GET /api/v1/users/email/{}` |
| `auth` | vans_story `src/app/api/auth/**/route.ts` | 로그인 → BFF `/api/auth/*` → Spring `/api/v1/auth/*` |

## 저장과 장애 격리 (`repositories/code_graph.py`, `clients/github.py`)

- `code_graphs` 인덱스: unique `(repo, commit_sha)`, 조회 `(repo, analyzed_at desc)`. 저장소마다 최신 5개만 남기고 오래된 문서는 지운다.
- 다운로드: 타임아웃·크기 상한은 G-11 의 `Settings`. 상한을 넘으면 중단하고 `ServiceUnavailableError`.
- 압축 해제: `tarfile.extractall(path, filter="data")`. 임시 디렉터리는 `finally` 에서 지운다.
- 백그라운드 분석 중 예외: `logger.warning` 만 남기고 기존 최신 문서는 건드리지 않는다(G-09).

## API (`/api/v1/code-graph/`, trailing slash)

| 메서드·경로 | 인증 | 응답 |
|---|---|---|
| `POST {repo}/analyze/` | `X-Internal-Key` | tarball 을 받아 SHA 를 뽑은 뒤, 저장된 문서가 있으면 200 `{repo, commit_sha, status: "stored"}`, 없으면 202 `{repo, commit_sha, status: "queued"}` 와 함께 백그라운드 분석(G-11) |
| `GET /` | 공개 | 저장소별 최신 commit_sha·analyzed_at·진단 건수 |
| `GET {repo}/` | 공개, 캐시 | 전체 그래프 + 진단(아래 구조) |
| `GET {repo}/context/` | 공개 | `text/markdown`(위 템플릿). CLAUDE.md 갱신·프롬프트 주입용 |
| `GET services/` | 공개, 캐시 | 서비스 그래프: 노드 = 엔드포인트·외부 서비스, 엣지 = 호출 (B) |
| `GET flows/{flow}/` | 공개 | 한 e2e 경로와 hop 마다의 요청·응답 스키마, mermaid sequenceDiagram 텍스트 (B) |

`GET {repo}/` 구조: `repo, commit_sha, analyzed_at, tech_stack[], layers[{id, may_import[], file_count}], diagnostics{layer_violations[{source, target, from_layer, to_layer}], cycles[[path, ...]], orphan_files[], hub_files[{path, in_degree, out_degree, symbol_count}]}, graph{nodes[{id, layer, symbols[], in_degree, out_degree}], edges[{source, target, status: valid | violation}]}, outbound_calls[...]`. 영향 반경(ancestors·descendants, N-hop)은 프론트가 이 JSON 으로 계산한다.

## 로드맵

| 단계 | 내용 | 완료 기준 |
|---|---|---|
| A | 파일 그래프 + 진단 + context 마크다운. 이 앱 자기 자신부터, 이어서 나머지 셋 | `code-graph analyze vans_devblog_fastapi` 가 Mongo 에 저장하고 `GET {repo}/context/` 가 CONVENTIONS 표와 같은 레이어·허용 쌍을 돌려주고, 선언된 예외 7건을 뺀 위반이 0 이다. vans_story 는 PoC 기준 위반 21건(`components → app` 17 포함), vans_story_be_post·vans_story_be 는 0 이 나온다 |
| B | 서비스 그래프: `outbound_calls` 추출(세 BFF 패턴 포함) → `client_env` 접두로 서비스 식별 → OpenAPI 경로 매칭. flows + mermaid | `search`·`publish` 두 흐름이 hop 마다 스키마 이름과 함께 나온다 |
| C | vans_story 뷰어(별도 저장소): 저장소 탭, 좌측 진단 목록·파일 검색, 중앙 mermaid(레이어 블록 / 선택 파일 ego-network / 흐름 시퀀스), 우측 파일 인스펙터(심볼·import·GitHub 링크), 관리자에게만 재분석 버튼. `projects.ts` 의 손 문서를 이 뷰로 바꾼다 | 블로그 프로젝트 페이지에서 보인다. 캔버스 라이브러리는 ego-network 가 안 읽힐 때만 |
| 뒤 | LLM 노드 요약(해시 캐시), commit 간 diff, e2e 테스트 커버리지 오버레이(`tests/e2e` 가 치는 경로를 엔드포인트에 표시) | 필요해지면 |
