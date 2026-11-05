# 코드 그래프 (code_graph) 기획

내 저장소들의 의존 그래프를 이 앱이 만들고, 블로그(vans_story) 프로젝트 페이지가 그 JSON 을 그린다. 손으로 쓰는 아키텍처 문서·그림을 대체하는 것이 목적이다(블로그 `src/constants/projects.ts` 는 검색 서비스를 아직 Django·Redis 로, 기술 스택에 Storybook 을 적고 있다). Understand-Anything 의 클론이 아니다. LLM 요약·멀티 에이전트·임의 URL 입력·webhook·큐는 넣지 않는다.

- 설계 문서 세트는 만들지 않는다. 기획은 이 문서 하나, 저장소 설정 예시는 [repos.example.json](repos.example.json), PoC 참고 코드는 [poc_codegraph.py](poc_codegraph.py)(앱 코드가 아니다), 사용법은 저장소 README 한 절, 나머지는 docstring 이다. 그래프 JSON 과 context 마크다운이 곧 문서다. 코드가 이 문서와 달라지면 같은 커밋에서 이 문서를 고친다.
- 이 문서의 수치·경로는 2026-11-03 과 2026-11-05 에 저장소·tarball·코드에서 확인한 것이다. 확인하지 않은 값은 "확인 필요" 라고 적는다. 분석기를 믿을 수 있는지는 아래 "감사" 절이 기준이다.

## 확인된 전제 (2026-11-03)

- 의존성 3개, 네이티브 빌드 없음: `tree-sitter==0.26.0`, `tree-sitter-language-pack==1.23.1`(win_amd64·manylinux_2_34 wheel), `networkx==3.7`. **문법은 wheel 에 없다.** wheel 은 2.5MB 이고 첫 사용 때 GitHub Releases 에서 약 23MB 번들을 받아 캐시에 푼다(처음 기획의 "바로 쓴다" 는 틀렸다, G-24).
- 이미지에 git 이 없다. `https://codeload.github.com/JONGHYUNVAN/{repo}/tar.gz/{ref}` 를 httpx 로 받아 `tarfile`(`filter="data"`)로 임시 디렉터리에 풀고 끝나면 지운다. 네 저장소(vans_devblog_fastapi, vans_story, vans_story_be_post, vans_story_be)가 모두 public 이라 토큰이 없다. 소유자는 `JONGHYUNVAN` 이다(다른 표기는 404).
- tarball 구조: 최상위 디렉터리는 `{repo}-{ref}`(예: `vans_story-main`)이고, 커밋 해시는 pax 전역 헤더 `comment` 에 전체 SHA 로 들어 있다(네 저장소 모두 확인). 크기는 vans_story 30.4MB(`public/docs` 의 HTML 470개 포함), 나머지 셋은 300KB 이하다.
- 1회용 PoC([poc_codegraph.py](poc_codegraph.py). tree-sitter + networkx, 소스 루트 바로 아래 디렉터리를 레이어로) 결과:

| 저장소 | 소스 루트 | 파일 | 파싱 | 심볼 | 내부 import 엣지 |
|---|---|---|---|---|---|
| vans_devblog_fastapi | `app` | 85 | 0.19s | 613 | 252 |
| vans_story | `src` | 241 | 0.87s | 850 | 484 |
| vans_story_be_post | `src` | 55 | 1.44s | 106 | 90 |
| vans_story_be | `src/main/kotlin` | 44 | 0.12s | 217 | 59 |

  이 앱은 CONVENTIONS 1절 표에 없는 import 가 6건이고 전부 그 절이 선언한 예외다(`routers/agent_rounds.py → models/enums.py`, `schemas/agent_pipeline.py → models/{enums, model_binding, post_draft, publishing_round, topic}.py`). `cli/__main__.py → models/enums.py` 도 같은 예외다(PoC 는 `cli/` 를 훑지 않았다). 그 밖의 역행은 없다. 분석이 1~2초라 큐·워커는 두지 않는다.
- 실제 디렉터리: 이 앱 `app/{routers, services, repositories, clients, models, schemas, agents, core}` + 루트 `cli/`. vans_story `src/{app, components, constants, hooks, lib, messages, store, types}`(`features`·`services`·`utils` 는 없다. 그쪽 CONVENTIONS 1장이 `utils/` 등을 만들지 않는다고 정했다. `src/middleware.ts` 도 없다). vans_story_be_post `src/{auth, categories, common, config, posts, search-service, swagger, user-service}`. vans_story_be `src/main/kotlin/blog/vans_story_be/{config, domain, global}`(빌드 파일은 `build.gradle`, kts 아님).
- 레이어 간 import 전체 표(PoC): vans_story 는 `components → app` 17, `lib → store` 2, `lib → components` 1, `constants → lib` 1 이 방향이 이상하고 나머지는 위에서 아래로만 간다. vans_story_be_post 는 `posts → {auth 7, common 5, categories 4, user-service 3, search-service 3}`, `categories → {auth 5, common 1}` 이고 역행이 없다. vans_story_be 는 `config → {domain 7, global 1}`, `domain → global 9` 다.
- 서비스 간 호출은 코드에 경로가 문자열로 있다. vans_story BFF `src/app/api/**/route.ts` 27개가 환경변수 6개(POST_API_URL 7건, AUTH_API_URL 5, SEARCH_API_URL 3, DJANGO_API_URL 2, IMAGE_API_URL 1, AI_API_URL 1)로 상류를 가리킨다. 환경변수마다 값에 경로 접두가 붙어 있다: POST_API_URL `/api/v1`, AUTH_API_URL `/api/v1`(route.ts 가 `${AUTH_API_URL}/auth/login` 으로 부르고 Spring 경로는 `/api/v1/auth/login` 이다. `src/constants/apiConfig.ts` 의 기본값 `http://localhost:3000/api` 는 낡았고 로컬 `.env` 는 `/api/v1` 이다), IMAGE_API_URL `/api`, SEARCH_API_URL·DJANGO_API_URL 없음. route.ts 밖에서도 `src/lib/posts/clientActions.ts`·`src/app/sitemap.ts` 가 POST_API_URL 로 NestJS 를 부른다. SEARCH_API_URL·DJANGO_API_URL 만 이 앱이고, AI_API_URL(AI 채팅, 기본 :3003)·IMAGE_API_URL(이미지, 기본 :3002)은 네 저장소 밖의 서비스다.
- BFF 가 상류 URL 을 만드는 패턴은 네 가지다: ① `process.env.X` 를 `const` 에 받아 템플릿 리터럴에서 쓴다(`search/route.ts` 의 `searchApiUrl`. 템플릿을 다시 `const fullUrl` 에 담거나 삼항식·`new URL(...)` 을 거쳐 `fetch` 에 넘기기도 한다), ② 인자 없는 함수로 감싼다(`stocks/kis/**` 의 `djangoApiUrl()`), ③ `lib/api/apiProxy.ts` 의 `proxyGet/proxyPost/proxyPatch/proxyDelete(request, {baseUrl, path})` 헬퍼에 넘긴다(autocomplete·popular·categories), ④ 템플릿 안에 `process.env.X` 를 직접 쓴다(`auth/**` 5개. 2026-11-03 B 구현 때 확인했다. 처음 기획의 "없다" 는 틀렸다). NestJS 는 `configService.get('X')` 를 `const` 에, `configService.getOrThrow('X')` 를 `this.필드` 에 받는다.
- 백엔드 간: NestJS → 이 앱 `POST /api/internal/search/index/`·`DELETE .../{post_id}/`(`src/search-service/search-service.client.ts`. `posts.service.ts` 가 생성·수정·삭제 때 fire-and-forget 으로 부른다), NestJS → Spring `GET /api/v1/users/email/{email}`(`src/user-service/user-service.client.ts`), 이 앱 → NestJS `GET /api/v1/categories`·`POST /api/v1/posts`, 이 앱 → Spring `POST /api/v1/auth/login`(`clients/publisher/post_be.py`). Spring 은 밖으로 부르지 않는다.
- 세 백엔드 모두 OpenAPI 를 낸다: 이 앱 `/openapi.json`(같은 프로세스라 `app.openapi()` 로 받을 수 있다), NestJS `GET /api/posts/swagger-json`(`SwaggerModule.setup("api/posts/swagger")`. 단 `SwaggerAuthMiddleware` 가 `/api/posts/swagger` 로 시작하는 모든 경로를 비밀번호 세션 뒤에 둬 운영에서 401 이다(2026-11-03 확인). `POST /api/posts/swagger/auth {password}` 로 받은 세션 쿠키가 있어야 한다, G-20), Spring springdoc 3.0.2 기본 `/v3/api-docs`(`SecurityConfig` 가 prod 프로필에서 공개하지 않으므로 운영에서는 받지 못할 수 있다. 엔드포인트 존재는 소스로 대조한다, G-29). 호스트는 이 앱 `Settings` 의 `post_be_url`·`user_service_url` 을 그대로 쓴다. 코드에 적힌 운영 호스트는 `https://vansdevblog.online` 하나뿐이라 백엔드 호스트를 여기 하드코딩하지 않는다.
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
| G-11 | (G-25 가 대체) 분석 요청은 서비스 메서드 하나가 "tarball 받기 → SHA → 저장된 문서가 있으면 그것(200), 없으면 파싱·저장을 `BackgroundTasks` 로(202)" 를 한다. 라우터는 호출만 한다. 다운로드 타임아웃·크기 상한은 `Settings`(`CODE_GRAPH_DOWNLOAD_TIMEOUT_S`=30, `CODE_GRAPH_MAX_TARBALL_MB`=64) | 응답에 `commit_sha` 를 바로 담으려면 받아야 한다. vans_story 가 30MB 라 15초는 빡빡하다. 다운로드를 라우터에 두면 CONVENTIONS 2절 위반이다 |
| G-12 | `tech_stack` 은 매니페스트(`pyproject.toml`·`requirements.txt`, `package.json`, `build.gradle`)에 선언된 의존성 이름·버전만 적는다. "실제 import 된 것" 과의 교집합은 내지 않는다 | PyPI 이름과 import 이름이 다르다(pycryptodome → `Crypto`, pydantic-settings → `pydantic_settings`, markdown-it-py → `markdown_it`, tzdata 는 import 없음). Gradle 좌표와 Kotlin 패키지는 대응이 없다 |
| G-13 | 서비스 간 호출 지점(`outbound_calls`)은 파일 분석 때 뽑아 `CodeGraph` 문서에 저장한다. OpenAPI 매칭(엔드포인트 존재·스키마 이름)만 조회 때 하고 `core/cache` TTL 로 묶는다. 실패도 같은 TTL 로 캐시한다. 이 앱 자신의 OpenAPI 는 `dependencies.py` 가 `app.openapi` 를 넘겨 HTTP 없이 받고, NestJS·Spring 은 `post_be_url`·`user_service_url` + 고정 경로다 | OpenAPI 가 안 와도 호출 토폴로지는 남는다. 공개 GET 이 바깥을 매번 두드리지 않는다 |
| G-14 | 레이어는 경로 접두(`path`)로 선언한다. 소스 루트 아래에 있지만 어느 레이어에도 안 속한 파일(`app/main.py`, `app/dependencies.py`, `src/main.ts`, `src/app.module.ts`, `VansStoryBeApplication.kt`)은 `root` 레이어이고, `root` 가 source 든 target 이든 위반으로 세지 않는다 | 이 앱의 `cli/` 는 `app/` 밖이라 "소스 루트 바로 아래 디렉터리" 규칙으로는 못 적는다. `routers → dependencies.py` 6건은 정상이다 |
| G-15 | vans_story·vans_story_be_post 의 허용 표는 그쪽에 import 규칙 문서가 없어 여기서 처음 선언한 제안이다. 첫 분석의 위반 목록을 보고 코드를 고칠지 표를 넓힐지 정한다 | 그쪽 CONVENTIONS 는 파일을 어디에 둘지만 정한다 |
| G-16 | 순환 대표 경로는 SCC 의 엣지를 정렬해 새로 만든 그래프에서 가장 작은 경로를 `source` 로 `find_cycle` 한다 | `G.subgraph(...)` 뷰는 노드·이웃을 set 순서로 돌 수 있어 해시 시드마다 경로가 달랐다(A 구현 때 확인). 같은 commit 은 같은 결과여야 한다 |
| G-17 | `CodeGraph` 문서에 분석 때의 `allowed_edges` 를 같이 저장한다. API 응답에는 넣지 않는다 | context 의 Declared Exceptions 가 위반 목록과 같은 시점의 설정을 보인다. 같은 commit 은 다시 분석하지 않아(G-09) 설정만 바꾸면 둘이 어긋난다 |
| G-18 | `tech_stack` 은 `이름 버전` 문자열. `package.json` 은 `dependencies` 만(devDependencies 제외), `build.gradle` 은 `plugins` 의 `id`·`version` 과 `test*` 가 아닌 구성의 좌표, Python 은 `pyproject.toml` `[project].dependencies` 와 `requirements.txt`(`-r` 등 옵션 줄 제외, extras 뗌) | 런타임 스택만 보인다. Spring Boot·Kotlin 버전은 Gradle plugins 에만 있다 |
| G-19 | 호출 지점을 찾을 파일(`call_sites`, glob)과 Next.js App Router 루트(`route_root`)는 저장소마다 선언한다. 그 파일에서만 기준 URL(환경변수) 뒤에 경로를 붙인 문자열을 찾는다. TS 메서드는 그 URL 을 받는 호출에서 읽고(`fetch` 는 옵션 `method`, 기본 GET. 그 밖은 함수 이름 끝의 get·post 등), 호출을 못 찾은 URL(로그 문자열 등)은 버린다. Python 은 `f"{self._x_url}/..."` 를 `client.get/post` 에 넘긴 것만 보고 환경변수는 필드 이름을 대문자로 바꾼 것이다 | 저장소 전체를 훑으면 KIS·LLM 벤더 클라이언트의 `self._base_url` 같은 비환경변수 URL 이 외부 노드로 섞인다. G-05 와 같이 추론보다 선언 |
| G-20 | NestJS OpenAPI 는 `openapi.login`(`path`, `password_setting`)이 있으면 그 경로로 비밀번호를 POST 하고 같은 클라이언트(쿠키)로 문서를 받는다. 비밀번호는 `POST_BE_SWAGGER_PASSWORD`(그쪽 `SWAGGER_ADMIN_PASSWORD` 와 같은 값). 비어 있으면 받지 않는다 | swagger-json 이 세션 게이트 뒤에 있다. 그쪽 보안 설정은 바꾸지 않는다. 없으면 G-08 대로 스키마만 빈다 |
| G-21 | 흐름은 `models/service_graph.FLOWS` 고정 표(아래 4절)이고 hop 은 (호출자 서비스, 대상 서비스, 메서드, 경로) 다. hop 의 `verified` 는 호출자 서비스 코드에서 그 엔드포인트로 가는 호출 지점을 찾았다는 뜻이다. 엔드포인트 → 그 안에서 부르는 클라이언트(NestJS 컨트롤러 → `SearchServiceClient`)는 잇지 않는다 | DI 라 import 그래프로는 어느 엔드포인트가 어느 클라이언트를 부르는지 모른다(G-06 과 같은 이유) |
| G-22 | 엣지 출발은 호출 지점이 Next.js route 핸들러(`GET`·`POST` 함수)면 그 inbound 엔드포인트, 아니면 코드 노드(`서비스:파일#심볼`)다. 엣지 상태는 matched(대상 OpenAPI·route 에 있다)·missing(없다)·unknown(대상 문서를 못 받았다)·external(`client_env` 에 없는 환경변수) 넷이고, (출발, 대상, 파일, 심볼)이 같은 호출은 엣지 하나다 | missing 이 죽은 호출·접두 오류를 드러낸다. `/posts` 와 `/posts?${query}` 가 두 줄로 보이지 않게 |
| G-23 | TS 의 `import('x')` 도 import 로 잇는다. 값을 모르는 템플릿 리터럴(`` import(`./${x}`) ``)은 잇지 않는다 | 빠뜨리면 지연 로딩 파일이 orphan 으로 나온다. vans_story 의 orphan 19건 중 14건이 살아 있는 파일(게임 전부)이었고, 고친 뒤 5건·엣지 516·순환 2다. 지워도 된다는 신호라 거짓 양성이 가장 위험하다 |
| G-24 | 분석기 실패가 앱 기동을 막지 않는다. 문법 패키지는 분석 함수 안에서 import 하고, `CODE_GRAPH_REPOS` 가 깨지면 기능만 끄고 오류 로그를 남긴다. 문법은 Docker 빌드 때 `prefetch` 로 이미지에 넣는다(`TREE_SITTER_LANGUAGE_PACK_CACHE_DIR`) | 재현했다: 둘 다 고치기 전에는 검색·KIS·에이전트까지 기동에 실패했다. 문법이 이미지에 없으면 배포마다 첫 분석이 GitHub 에 의존하고 실패가 조용히 묻힌다 |
| G-25 | `POST analyze` 는 tarball 앞부분만 받아 SHA 를 확인한다(pax 헤더가 스트림 맨 앞이다). 전체 다운로드와 분석은 백그라운드에서 프로세스당 한 번에 하나만 돌고, 같은 저장소가 돌고 있으면 `running` 을 돌려준다. 분석에 쓰는 파일만 푼다. G-11 을 대체한다 | 전에는 호출마다 tarball 전체(vans_story 30MB)를 메모리에 올린 뒤 판단해서, 반복 호출이 단일 워커의 메모리를 눌러 검색·KIS 까지 위험했다 |
| G-26 | `CodeGraph.fingerprint` = 분석기 버전(`ANALYZER_VERSION`)과 저장소 설정의 해시. 다르면 같은 commit 도 다시 분석한다. 추출·진단 규칙을 바꾸면 버전을 올린다. G-09 의 멱등 조건을 좁힌다 | 전에는 파서를 고쳐도 commit 이 바뀌기 전까지 틀린 결과가 남았다(G-23 의 버그가 그랬다) |
| G-27 | 엣지에 `kind` 를 둔다: `static`·`dynamic`(`import()`)·`type`(실행 때 사라짐). TS 는 `import type`, 전부 `type` 인 지정자, 타입 위치에서만 쓴 이름을 type 으로 본다. 데코레이터가 있는 파일은 `type` 키워드만 믿는다. Python 은 `if TYPE_CHECKING:`. 순환은 type 을 뺀 그래프로 세고(`cycles`·`cycle_groups`), 타입까지 넣어야 생기는 묶음은 `type_only_cycle_groups` 로 나눈다. 위반은 `type_only` 를 단다 | vans_story 위반 21건 중 14건이 타입 전용이고, 순환으로 보이던 14개 파일 묶음이 실행에는 없다. 구분 없이 보이면 고칠 곳을 잘못 고른다. NestJS 는 `emitDecoratorMetadata` 가 생성자 타입 import 를 실행 때 남겨 이름 쓰임으로 판단할 수 없다 |
| G-28 | 주소가 코드에 박힌 호출(`fetch('https://...')`)은 `external_host` 로 저장하고 `host:호스트` 외부 노드로 그린다. 호스트를 코드에서 못 정하면 `unresolved` 다 | BFF route 27개 중 10개(주식 외부 API)가 서비스 그래프에 없었다. 환경변수 호출만 보면 외부 의존이 통째로 가려진다 |
| G-29 | NestJS·Spring 엔드포인트를 소스에서 읽는다(`route_framework`). `@Controller`·`@Get`, `@RequestMapping`·`@GetMapping`. OpenAPI 는 있으면 스키마까지 주는 우선 출처이고, 없어도 호출 대조가 된다. G-13 을 보강한다 | 대조가 운영 OpenAPI 에 묶여 있었다. Spring 문서는 prod 에서 비공개이고 NestJS 는 비밀번호가 필요해, 받지 못하면 전부 unknown 이었다 |
| G-30 | Kotlin 같은 패키지 참조를 엣지로 잇는다. import 가 없어도 파일이 쓰는 이름이 같은 패키지의 최상위 선언이면 잇는다 | 엣지의 약 17%(81건 중 14건)가 빠져 있었다. 근사 기준과 81건 일치 |
| G-31 | hop 의 `verified` 는 호출 코드를 찾았고 대상 엔드포인트도 있다는 뜻이다(`status: matched`). `FLOWS` 는 손으로 쓴 표이고 이 값으로 코드와 대조한다. 어긋나면 mermaid 에서 점선이다. G-21 을 좁힌다 | 전에는 호출 코드만 있으면 대상에 없는 경로도 verified 였다 |
| G-32 | 영향 반경을 분석기가 준다: `GET {repo}/impact/?file=&hops=&runtime_only=`, CLI `code-graph impact`. 뷰어 없이도 쓴다 | 고치기 전에 수정 범위를 보는 것이 이 도구의 핵심 쓰임이다. 프론트 계산에 묶이면 뷰어 전에는 못 쓴다 |
| G-33 | 분석 실패가 드러난다: `GET {repo}/analysis/` 가 idle·running·done·failed 와 예외 종류를 준다(프로세스 안 상태). 소스 파일이 0개면 빈 그래프를 저장하지 않고 실패다. 하위 폴더인데 어느 레이어에도 안 걸린 파일은 `unassigned_files` 로 보인다 | `queued` 만 돌려주고 실패하면 로그뿐이었다. 설정이 낡으면 "위반 0" 으로 보이고, 새 폴더는 조용히 규칙 검사에서 빠졌다 |
| G-34 | 뷰어는 읽기 전용이다. 재분석은 CLI 나 `X-Internal-Key` 직접 호출로 하고, BFF 는 `INTERNAL_API_KEY` 를 갖지 않는다 | vans_story_be 에 `@EnableMethodSecurity` 가 없어 Spring `GET /users` 로 관리자를 확인할 수 없어 보인다(코드로만 확인). 내부 키는 색인 삭제 API 도 연다 |

## 저장소 설정 (`CODE_GRAPH_REPOS`)

전문은 [repos.example.json](repos.example.json). 저장소 키 → `{owner, name, ref, source_roots, layers[{id, path, may_import[]}], allowed_edges[{source, target}], entry_points(glob), diagnostics{orphans, hubs}, service, openapi, client_env{환경변수: 경로 접두}, call_sites(glob), route_root, route_framework}`. `service` 가 비면 저장소 키를 서비스 이름으로 쓴다.

- `layers[].path` 는 저장소 루트 기준 디렉터리 접두다. `may_import` 는 그 레이어가 import 해도 되는 레이어 id 목록이고 같은 레이어 안은 항상 허용이다. 위반 = `source.layer ≠ root`, `target.layer ≠ root`, `target.layer ∉ may_import(source.layer)`, `(source, target) ∉ allowed_edges`.
- 이 앱: CONVENTIONS 1절 표 그대로. `routers`·`cli → services, schemas, core`, `services → repositories, models, schemas, agents, clients, core`, `agents → models, core`, `repositories → models, clients, core`, `models`·`schemas`·`clients → core`, `core → 없음`. `allowed_edges` 는 위 예외 7건.
- vans_story(제안, G-15): `app → components, hooks, lib, store, constants, types`, `components → hooks, lib, store, constants, types`, `hooks → lib, store, constants, types`, `store → lib, constants, types`, `lib → constants, types`, `constants → types`, `types → 없음`. PoC 기준 위반 21건(`components → app` 17, `lib → store` 2, `lib → components` 1, `constants → lib` 1).
- vans_story_be_post(제안, G-15): `posts → categories, auth, common, config, search-service, user-service`, `categories → auth, common, config`, `auth`·`search-service`·`user-service`·`swagger → common, config`, `common → config`, `config → 없음`. PoC 기준 위반 0.
- vans_story_be: `config → domain, global`, `domain → global`, `global → 없음`. PoC 기준 위반 0.
- 엔트리포인트(orphan 제외): 이 앱 `app/main.py`·`app/dependencies.py`·`cli/__main__.py`. vans_story `src/app/**/{page,layout,loading,error,not-found,template}.tsx`·`src/app/**/route.ts`·`src/app/{robots,sitemap}.ts`. NestJS `src/main.ts`·`src/**/*.module.ts`. Spring `**/VansStoryBeApplication.kt`. Python `__init__.py` 와 TS 배럴(`index.ts(x)` 중 `export ... from` 만 있는 파일)도 orphan 에서 뺀다.
- `openapi`: 이 앱 `"self"`, NestJS `{"base_setting": "post_be_url", "path": "/api/posts/swagger-json", "login": {"path": "/api/posts/swagger/auth", "password_setting": "post_be_swagger_password"}}`(G-20), Spring `{"base_setting": "user_service_url", "path": "/v3/api-docs"}`, vans_story `null`(엔드포인트는 route.ts 에서).
- `client_env`(그 서비스를 가리키는 환경변수 → 값에 붙은 경로 접두. 다른 저장소의 호출을 이 서비스로 잇는다): 이 앱 `SEARCH_API_URL`·`DJANGO_API_URL`(BFF)·`SEARCH_SERVICE_URL`(NestJS) 모두 `""`, NestJS `POST_API_URL: "/api/v1"`(BFF)·`POST_BE_URL: ""`(이 앱), Spring `AUTH_API_URL: "/api/v1"`(BFF)·`USER_SERVICE_URL: ""`(NestJS·이 앱).
- `call_sites`(G-19): 이 앱 `app/clients/publisher/*.py`, vans_story `src/app/**/route.ts`·`src/app/sitemap.ts`·`src/lib/**/*.ts`(`route_root: "src/app"`), NestJS `src/**/*.client.ts`, Spring 없음(밖으로 부르지 않는다).
- `client_env` 에 없는 환경변수(AI_API_URL, IMAGE_API_URL)는 서비스 그래프에서 환경변수 이름을 가진 외부 노드로 그린다.
- 검증은 `services/code_graph.py` 가 `models/code_graph.py` 의 `CodeGraphRepo` 로 저장소 하나씩 한다. `openapi`·`client_env` 는 서비스 그래프 조회 때, 나머지는 분석 때 읽는다. 설정을 바꾸면 fingerprint 가 달라져 다음 분석 요청이 같은 commit 도 다시 분석한다(G-26). `CODE_GRAPH_REPOS` 가 비었거나 깨졌으면 기능이 꺼지고, 깨졌으면 오류 로그가 남는다(G-24).
- `route_framework`(G-29): NestJS `"nest"`, Spring `"spring"`. Next.js 는 `route_root` 로 정한다.

## 코드 배치 (기존 계층, 새 폴더 없음)

| 파일 | 역할 |
|---|---|
| `clients/github.py` | 앞부분만 받아 SHA 확인(G-25), tarball 받기(타임아웃·크기 상한), pax `comment` 에서 SHA, 필요한 파일만 임시 디렉터리에 풀기·삭제 |
| `clients/openapi.py` | NestJS·Spring 의 OpenAPI 문서 받기. 세션 로그인(G-20) |
| `models/code_graph.py` | `CodeGraph`(repo, commit_sha, fingerprint(G-26), analyzed_at, tech_stack, layers, allowed_edges(G-17), nodes, edges, diagnostics, outbound_calls, inbound_routes)와 노드·엣지·진단 값 객체, 저장소 설정 `CodeGraphRepo`. I/O 없는 계산(레이어 위반 판정, 순환, orphan, hub, 영향 반경, context 마크다운) |
| `repositories/code_graph.py` | `code_graphs` 컬렉션 upsert·최신 조회·보관 정리 |
| `services/code_graph.py` | 분석 유스케이스(G-25), `AnalysisTracker`(상태·동시 실행 제한, lifespan 이 만든다), 파싱(언어별 정의·import·엣지 종류 추출, 2-pass 경로 해석) → 진단 → 엔티티 |
| `services/code_graph_calls.py` | tree-sitter 트리에서 호출 지점(`outbound_calls`)·inbound 엔드포인트(Next.js·NestJS·Spring) 뽑기. `services/code_graph.py` 의 Pass 1 이 부른다 |
| `models/service_graph.py` | `ServiceGraph.build`(호출 지점 + OpenAPI → 노드·엣지), 흐름 표 `FLOWS`, `trace`(hop·mermaid), `normalize_path`. I/O 없음 |
| `services/service_graph.py` | 최신 `CodeGraph` 들과 OpenAPI(캐시)를 모아 `ServiceGraph` 를 만들고 응답 스키마로 바꾼다 |
| `schemas/code_graph.py` | 응답 스키마 |
| `routers/code_graph.py` | 아래 API |
| `cli/__main__.py` | `code-graph analyze <repo>`, `code-graph context <repo>`, `code-graph impact <repo> <file> [--hops] [--runtime-only]`, `code-graph flow <flow> [--mermaid]` |
| `code_graph_design/audit/` | 분석기를 쓰지 않는 독립 기준과 비교하는 재측정 도구. 앱 코드가 아니다 |
| `tests/` | 같은 구조. 파서 테스트는 `tests/golden/code_graph/` 에 작은 소스 트리(python_app·ts_app·kotlin_app, 엣지 종류는 ts_kinds·py_kinds·ts_dynamic)와 설정(`repos.json`)을 두고 기대 그래프(`{트리}.json`)와 비교. 이 트리는 ruff 대상에서 뺀다. 실제 tarball 로 A 완료 기준을 보는 `tests/e2e/test_code_graph.py` |

## 파싱 규칙 (`services/code_graph.py`)

Pass 1 이 파일·심볼·패키지 인덱스를 만들고 Pass 2 가 import 를 해석해 엣지를 만든다. 제외: `node_modules`, `.git`, `.venv`, `__pycache__`, `.next`, `dist`, `build`, `coverage`, `public`, `tests`·`test`·`__tests__`·`e2e`, `*.spec.ts`, `*.test.ts(x)`, `*.d.ts`. 외부 패키지는 이름만 집계한다.

| 언어 | 정의 노드 | 의존 노드 | 내부 경로 해석 |
|---|---|---|---|
| Python | `function_definition`, `class_definition` | `import_statement`, `import_from_statement` | 상대 import 는 현재 패키지 기준 절대 모듈로 바꾼다. `from app.a.b import c` 는 `app/a/b/c.py` → `app/a/b/c/__init__.py` → `app/a/b.py` → `app/a/b/__init__.py` 순으로 찾는다 |
| TS/TSX | `function_declaration`, `class_declaration`, `interface_declaration`, `type_alias_declaration`, `enum_declaration`, `method_definition`, 최상위 `lexical_declaration` 의 `arrow_function` 바인딩(함수형 컴포넌트·훅) | `import_statement`, `source` 가 있는 `export_statement`(`export ... from`, `export * from`), 문자열 인자의 `import('x')`(`call_expression`. `next/dynamic` 포함) | `@/` → `src/`, `src/` 는 그대로(NestJS tsconfig baseUrl, 35건), `./`·`../` 는 파일 기준. 그 다음 `{path}` → `{path}.ts` → `{path}.tsx` → `{path}/index.ts` → `{path}/index.tsx`. `posts.service` 처럼 점이 든 이름 때문에 `with_suffix` 가 아니라 이름 뒤에 붙인다 |
| Kotlin | `class_declaration`, `object_declaration`, `function_declaration` | `package_header`, `import_header` | Pass 1 에서 최상위 선언으로 `패키지.심볼 → 파일`, `패키지 → 파일들` 맵을 만든다. 이 맵에 있는 이름(곧 내부 접두 `blog.vans_story_be.`)만 해석하고, `.*` 는 패키지의 모든 파일로, `패키지.클래스.안쪽`(중첩·enum 항목)은 바깥 선언 파일로 잇는다. 같은 패키지 참조는 파일이 쓰는 이름(`type_identifier`, `simple_identifier`)이 그 패키지의 최상위 선언이면 잇는다(G-30). 이름 토큰 기준이라 같은 이름의 지역 변수도 잇는 근사다 |

엣지 종류(G-27): 같은 파일 쌍에 import 가 여럿이면 `static` > `dynamic` > `type` 순으로 센다.

## 진단 계산식과 context 마크다운 (`models/code_graph.py`)

- 레이어 위반: 위 "저장소 설정" 의 정의. 해당 엣지는 `graph.edges[].status = "violation"`. 타입으로만 쓰는 import 는 `type_only` 다(구조 규칙 위반이지만 실행 시 의존은 아니다).
- 순환: `type` 을 뺀 그래프의 `strongly_connected_components` 중 크기 2 이상인 집합마다 `find_cycle(DiGraph(sorted(G.subgraph(scc).edges)), source=min(scc))` 로 대표 경로 하나를 `[a, b, ..., a]` 로 적고(G-16), 파일 전부는 같은 순서의 `cycle_groups` 에 둔다. 타입까지 넣어야 생기는 묶음은 `type_only_cycle_groups` 다(G-27).
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
- Violations, type-only {type_only_count} ({violation_count}):   ← 타입 전용이 없으면 `Violations ({violation_count})`
  - `{source}` ({from_layer}) -> `{target}` ({to_layer}) [type-only]
- Declared Exceptions ({allowed_edge_count}):
  - `{source}` -> `{target}`

## 3. Cycles & Orphans
- Runtime Cycles ({cycle_count}): `{a} -> {b} -> {a} ({파일 수} files)`
- Type-only Cycle Groups ({n}): `{파일들}`   ← 있을 때만
- Orphans ({orphan_count}): `{path}`
- Unassigned to Any Layer (not rule-checked) ({n}): `{path}`   ← 있을 때만

## 4. Hub Files (Top In-Degree)
| File | Layer | In | Out | Key Symbols |
|---|---|---|---|---|
| `{path}` | {layer} | {in_degree} | {out_degree} | {정의 순서 앞 5개} |
```

  건수가 0 이면 `- Violations (0)` 처럼 콜론과 항목 없이 쓰고, 순환·Orphans 의 여러 항목은 한 줄에 쉼표로 잇는다. May Import 가 비면 `-` 다. orphan·hub 를 끈 저장소는 0 건이다.

## 서비스 그래프 (`services/service_graph.py`, B)

1. 호출 지점 추출(분석 때, `call_sites` 파일만, G-19. `outbound_calls[{caller_file, caller_symbol, env_var, http_method, raw_path}]`, `inbound_routes[{file, http_method, path}]`):
   - `route_root` 아래 `route.ts`: export 하는 `GET`·`POST`·`PUT`·`PATCH`·`DELETE`(함수 선언 또는 `export const`)를 inbound 엔드포인트로 등록한다. URL 은 파일 경로에서 만든다(`(group)` 디렉터리는 빼고 `[id]`·`[...slug]` 는 `{id}`·`{slug}`).
   - TS 기준 URL: `process.env.X`·`configService.get(OrThrow)('X')`(그리고 그 `|| 기본값`), 그것을 받은 `const`(감싼 함수 스코프)·인자 없는 화살표 함수(`이름()`)·`this.필드`. 템플릿 리터럴의 첫 치환이 기준 URL 이면 나머지가 `raw_path` 다(위 ①②④). `{baseUrl: 기준, path: '...'}` 객체 리터럴은 `path` 가 `raw_path` 다(③. 헬퍼의 몇 번째 인자든 된다).
   - 메서드: 템플릿은 그것을 첫 인자로 받는 호출에서 읽는다(삼항식·괄호·`await`·`new URL(...)` 을 지나고, `const` 에 담겼으면 같은 스코프에서 그 이름·`이름.toString()` 을 첫 인자로 받는 호출). `fetch` 는 옵션 `method`(기본 GET), 그 밖은 함수 이름 끝(`get`, `proxyPost`, `basicGet`). 호출을 못 찾으면 버린다.
   - Python: `client.get/post/...(f"{self._x_url}/path")`. 환경변수는 `X_URL`(pydantic-settings 필드 이름 규칙).
   - 주소가 박힌 호출(G-28): 문자열·템플릿이 `https?://` 로 시작하고 위와 같은 규칙으로 호출을 찾으면 `external_host` 로 저장한다. 호출을 못 찾은 주소(Referer 헤더, 링크 문자열)는 버린다.
   - `route_framework`(G-29): NestJS 는 `@Controller` 클래스의 `@Get`·`@Post` 등, Spring 은 `@RestController` 클래스의 `@RequestMapping` + `@GetMapping` 등을 `inbound_routes` 로 저장한다. `:id` 는 `{id}` 다.
   - `caller_symbol` 은 감싼 함수(메서드는 `클래스.메서드`, const 화살표 함수는 그 이름)다.
2. 정규화(`normalize_path`): 쿼리·조각을 떼고 `${...}`·`{...}` 를 `{}` 로, 겹 슬래시를 하나로, 끝 슬래시를 뗀다. `env_var` 를 모든 저장소의 `client_env` 에서 찾아 대상 서비스와 **경로 접두**를 얻고(`POST_API_URL` 은 `/api/v1`), 접두 + `raw_path` 의 비교 키를 대상의 엔드포인트(OpenAPI `paths`, 문서를 못 받으면 소스에서 읽은 inbound route)와 (메서드, 비교 키)로 맞춘다. 상태는 G-22.
3. 스키마: `requestBody.content` 와 `responses["200"|"201"|"202"].content` 의 `application/json`(없으면 첫 미디어 타입. springdoc 은 `*/*` 다) 스키마 `$ref` 마지막 이름(`allOf` 하나짜리도). `type: array` 면 `items.$ref` 를 `이름[]` 로 적는다(이 앱의 목록 응답).
4. `GET flows/{flow}/`(G-21):

| flow | 시작 | hop |
|---|---|---|
| `search` | vans_story `src/app/api/search/route.ts` | 블로그 검색 → BFF `/api/search` → 이 앱 `GET /api/v1/search/posts/` |
| `publish` | 이 앱 `app/clients/publisher/post_be.py` | 에이전트 발행 → Spring `POST /api/v1/auth/login` → NestJS `POST /api/v1/posts` → 이 앱 `POST /api/internal/search/index/` |
| `posts` | vans_story `src/app/api/posts/route.ts` | 글 목록·작성 → BFF `/api/posts` → NestJS `/api/v1/posts` → Spring `GET /api/v1/users/email/{}` |
| `auth` | vans_story `src/app/api/auth/**/route.ts` | 로그인 → BFF `/api/auth/*` → Spring `/api/v1/auth/*` |

   응답은 `{flow, title, hops[{caller, callee, http_method, path, request_schema, response_schema, call_sites[], status, verified}], mermaid}`. `verified` 는 G-31 이다. `path` 는 맞춘 엔드포인트의 경로 템플릿(OpenAPI 를 못 받았으면 비교 키), `call_sites` 는 `저장소:파일#심볼`(browser 가 부르는 hop 은 route 파일)이다. mermaid 는 `sequenceDiagram` 이고 participant 는 `서비스 (저장소)`, 메시지는 `메서드 경로 (요청 → 응답)`, `verified` 가 아닌 hop 은 점선 `-->>` 이다.

## 저장과 장애 격리 (`repositories/code_graph.py`, `clients/github.py`)

- `code_graphs` 인덱스: unique `(repo, commit_sha)`, 조회 `(repo, analyzed_at desc)`. 저장소마다 최신 5개만 남기고 오래된 문서는 지운다.
- 다운로드: 타임아웃·크기 상한은 `Settings`(`CODE_GRAPH_DOWNLOAD_TIMEOUT_S`=30, `CODE_GRAPH_MAX_TARBALL_MB`=64). 상한을 넘으면 그 분석이 실패한다(상태 `failed`).
- 압축 해제: 매니페스트와 소스 루트 아래 언어 파일만 `tarfile.extractall(path, members=..., filter="data")`. 임시 디렉터리는 끝나면 지운다.
- 백그라운드 분석 중 예외: `logger.warning` 과 `failed` 상태를 남기고 기존 최신 문서는 건드리지 않는다(G-09, G-33). 네이티브 파서의 panic 도 `failed` 로 남긴다. CLI `code-graph analyze` 는 분석을 그 자리에서 돌리고 실패하면 종료 코드 1 이다.
- 인덱스는 lifespan 이 만든다. `CODE_GRAPH_REPOS` 가 비어 있으면 건너뛰고, Mongo 가 내려가 있으면 경고만 남긴다.

## API (`/api/v1/code-graph/`, trailing slash)

| 메서드·경로 | 인증 | 응답 |
|---|---|---|
| `POST {repo}/analyze/` | `X-Internal-Key` | SHA 만 확인한 뒤(G-25) 저장된 결과가 유효하면 200 `{repo, commit_sha, status: "stored"}`, 이미 돌고 있으면 202 `running`, 아니면 202 `queued` 와 함께 백그라운드 분석 |
| `GET {repo}/analysis/` | 공개 | 이 프로세스의 마지막 분석 `{repo, state: idle/running/done/failed, commit_sha, updated_at, error}`. error 는 예외 종류 이름뿐이다(G-33) |
| `GET {repo}/impact/?file=&hops=1~5&runtime_only=` | 공개 | `{file, hops, upstream[], downstream[], edges[]}`. 없는 파일은 404(G-32) |
| `GET /` | 공개 | 분석 결과가 있는 저장소만, 설정 순서의 배열 `[{repo, commit_sha, analyzed_at, violation_count, type_only_violation_count, cycle_count, orphan_count, hub_count}]` |
| `GET {repo}/` | 공개, 캐시 10분(`CODE_GRAPH_TTL`. 같은 프로세스의 저장이 지운다) | 전체 그래프 + 진단(아래 구조) |
| `GET {repo}/context/` | 공개 | `text/markdown`(위 템플릿). CLAUDE.md 갱신·프롬프트 주입용 |
| `GET services/` | 공개, 캐시 10분(분석 저장이 지운다). OpenAPI 는 실패까지 `OPENAPI_TTL` 10분 | 서비스 그래프 `{nodes[{id, kind: endpoint/code/external, service, repo, http_method, path, file, symbol, request_schema, response_schema}], edges[{source, target, caller_repo, caller_file, caller_symbol, env_var, http_method, path, status}], openapi{서비스: ok/unavailable/none}, repos{서비스: 저장소}}`. 엔드포인트 노드는 OpenAPI 의 모든 경로와 BFF route 전부다 |
| `GET flows/{flow}/` | 공개, 서비스 그래프 캐시를 쓴다 | 위 4절. 없는 흐름은 404 |

`GET {repo}/` 구조: `repo, commit_sha, analyzed_at, tech_stack[], layers[{id, may_import[], file_count}], diagnostics{layer_violations[{source, target, from_layer, to_layer, type_only}], cycles[[path, ...]], cycle_groups[[path, ...]], type_only_cycle_groups[[path, ...]], orphan_files[], hub_files[{path, in_degree, out_degree, symbol_count}], unassigned_files[]}, graph{nodes[{id, layer, symbols[], in_degree, out_degree}], edges[{source, target, status: valid | violation, kind: static | dynamic | type}]}, outbound_calls[...], inbound_routes[...]`.

## 감사 (분석기를 믿을 수 있는가)

테스트 통과는 근거가 못 된다. 기대값이 분석기 자신의 출력에서 나왔고, G-23 의 버그는 모든 테스트를 통과했다. 그래서 분석기를 쓰지 않는 기준과 비교한다. 다시 재려면 `python code_graph_design/audit/audit.py <저장소들이 있는 폴더>`.

2026-11-05 측정, 각 저장소 HEAD:

| 저장소 | 분석기 엣지 | 기준 엣지 | 누락 | 초과 | 기준 |
|---|---|---|---|---|---|
| vans_story | 516 | 516 | 0 | 0 | TypeScript 컴파일러(모듈 해석 포함) |
| vans_story_be_post | 72 | 72 | 0 | 0 | TypeScript 컴파일러 |
| vans_devblog_fastapi | 296 | 296 | 0 | 0 | Python ast. 해석 순서를 같은 규칙으로 짠 기준이라 독립성은 낮다 |
| vans_story_be | 81 | 81 | 0 | 0 | 정규식 근사. 컴파일러 기준이 아니다 |

- **엣지 종류:** vans_story 516건이 컴파일러 결과와 모두 일치한다. be_post 는 10건을 일부러 실행 시 의존으로 둔다(G-27).
- **고립 파일:** vans_story 5건 모두 import 하는 파일이 없고, 진입점에서 도달할 수 없는 파일과 정확히 같다. 동적 경로·문자열로만 쓰는 파일은 이 판정이 못 본다.
- **결정성:** 해시 시드를 바꿔도 결과가 같다.
- **서비스 그래프:** OpenAPI 없이 호출 46건이 matched 26, external 20 이고 missing·unknown 은 없다. 흐름 4개의 hop 10개가 모두 verified 다. 라우트 어노테이션은 NestJS 15개, Spring 15개 모두 추출한다(줄 머리 기준).
- **분석 중 이벤트 루프:** vans_story 한 번 분석할 때 지연 p50 35ms, p99 176ms, 최대 445ms 다. 같은 조건의 유휴 최대가 327ms 라 멈춤은 아니다. 다른 세션이 돌던 Windows 측정이라 운영 수치가 아니다. 메모리 최고치는 121MB 다.
- **장애 격리:** 문법 패키지를 못 읽어도, `CODE_GRAPH_REPOS` 가 깨져도 앱이 뜬다(테스트). 빈 캐시 + 네트워크 차단이면 분석만 `failed` 다. `prefetch` 뒤에는 네트워크를 막아도 파서 4개가 뜬다. 캐시는 32.7MB 다.

**아직 믿으면 안 되는 것**

| 항목 | 이유 |
|---|---|
| 레이어 위반 판정 | vans_story·be_post 규칙 표는 제안이다(G-15). 위반은 코드 사실이 아니라 표와의 차이다. `may_import` 가 레이어 단위라 이 앱의 `services → clients/es` 같은 파일 단위 금지는 못 잡고, `fastapi` 같은 외부 패키지 금지도 안 본다 |
| 흐름 | 고정 표다(G-21, G-31). 표에 없는 흐름은 안 보이고 `services/` 로 봐야 한다. hop 순서와 엔드포인트 안에서 무엇을 부르는지는 대조하지 않는다 |
| 서비스 식별 | 환경변수 이름 표다. `DJANGO_API_URL` 을 이 앱으로 잇지만 컷오버 전 운영은 Django 일 수 있다. 코드 기준 그래프이지 배포 기준이 아니다 |
| NestJS·Spring 스키마 | 실제 문서로 확인하지 못했다. `.env` 가 있는 곳에서 `code-graph flow publish` 로 본다 |
| Mongo 저장 | 실제 Mongo 로 돌린 적이 없다(이 PC 는 Atlas 허용 IP 가 아니다). `code-graph analyze` 로 본다 |
| Docker 이미지 | Docker 로 빌드하지 않았다. `prefetch`·환경변수는 Windows 에서만 확인했다 |
| 동적 경로 | `` import(`./${x}`) `` 와 호스트가 변수인 호출(`unresolved` 5건)은 대상을 모른다 |

## 로드맵

C 단계 코드는 2026-11-05 에 vans_story 에 들어갔다(읽기 전용, G-34). 배포는 FastAPI 컷오버 뒤다. 분석 결과를 보고 기존 화면을 고치는 일은 포함하지 않았고 다른 세션에서 한다. 남은 일은 vans_story 의 `.claude/code-graph-viewer.md` 에 있다.

B 단계 코드도 2026-11-03 에 들어갔다. `tests/e2e/test_code_graph.py::test_search_and_publish_flows_match_roadmap_b` 가 실제 tarball 네 개로 search·publish 의 모든 hop 에서 호출 지점을 찾고(verified), 이 앱 hop 의 스키마(`GET /api/v1/search/posts/` → `SearchResult`, `POST /api/internal/search/index/` `SearchIndexPayload → IndexingResult`)를 확인했다. NestJS·Spring hop 의 스키마는 `.env` 에 `POST_BE_URL`·`POST_BE_SWAGGER_PASSWORD`·`USER_SERVICE_URL` 을 넣고 같은 e2e 또는 `code-graph flow publish` 로 사용자가 확인한다(이 기기 `.env` 에 없다). 운영 반영은 `CODE_GRAPH_REPOS` 를 새 `repos.example.json` 으로 바꾸고 네 저장소를 다시 분석해야 한다(B 이전에 저장한 문서는 `outbound_calls` 가 비어 있다).

A 단계 코드는 2026-11-03 에 들어갔다. `tests/e2e/test_code_graph.py` 가 실제 tarball 로 위반 수(이 앱 0, vans_story 21: `components → app` 17·`lib → store` 2·`lib → components` 1·`constants → lib` 1, 나머지 둘 0)를 확인했다. Mongo 저장(`code-graph analyze`)은 Atlas 허용 IP 에서 확인한다.

| 단계 | 내용 | 완료 기준 |
|---|---|---|
| A | 파일 그래프 + 진단 + context 마크다운. 이 앱 자기 자신부터, 이어서 나머지 셋 | `code-graph analyze vans_devblog_fastapi` 가 Mongo 에 저장하고 `GET {repo}/context/` 가 CONVENTIONS 표와 같은 레이어·허용 쌍을 돌려주고, 선언된 예외 7건을 뺀 위반이 0 이다. vans_story 는 PoC 기준 위반 21건(`components → app` 17 포함), vans_story_be_post·vans_story_be 는 0 이 나온다 |
| B | 서비스 그래프: `outbound_calls` 추출(세 BFF 패턴 포함) → `client_env` 접두로 서비스 식별 → OpenAPI 경로 매칭. flows + mermaid | `search`·`publish` 두 흐름이 hop 마다 스키마 이름과 함께 나온다 |
| C | vans_story 뷰어(별도 저장소): 저장소 탭, 좌측 진단 목록·파일 검색, 중앙 mermaid(레이어 블록 / 선택 파일 ego-network / 흐름 시퀀스), 우측 파일 인스펙터(심볼·import·GitHub 링크). 재분석 버튼은 없다(G-34). 프로젝트 페이지의 구조도 자리를 이 뷰로 바꾼다 | 블로그 프로젝트 페이지에서 보인다. 캔버스 라이브러리는 ego-network 가 안 읽힐 때만 |
| A·B 보강 | 2026-11-05 감사에서 나온 G-24~G-33 | 위 감사 표. 2026-11-05 코드 완료. 남은 확인은 Mongo·Docker·실제 OpenAPI 다 |
| 뒤 | LLM 노드 요약(해시 캐시), commit 간 diff, e2e 테스트 커버리지 오버레이(`tests/e2e` 가 치는 경로를 엔드포인트에 표시) | 필요해지면 |
