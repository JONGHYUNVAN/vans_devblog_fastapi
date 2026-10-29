# 코드 컨벤션

이 서비스의 코드가 **어디에 있어야 하는지, 각 계층이 무엇을 하고 무엇을 하면 안 되는지**, 이름·코드·주석을 어떻게 쓰는지 정한다.

- **구조**(계층과 각 계층의 책임)는 언어와 무관한 백엔드 표준인 계층형 아키텍처(Controller → Service → Repository → Entity)를 따른다.
- **표기**(모듈·클래스·함수·모델 이름, docstring 형식)는 Python·FastAPI 관례를 따른다. 기준은 PEP 8, PEP 257, FastAPI 공식 문서(Bigger Applications, Query Parameter Models)와 공식 템플릿(full-stack-fastapi-template)이다. Spring·NestJS 표기(`posts.service.ts`식 파일 접미사, `findById`식 메서드, `*Request`/`*Response` DTO)는 옮겨 오지 않는다.
- 자매 저장소 `vans_story_be_post`와 공유하는 것은 계층 책임의 원칙, 한국어 주석, `type: 한국어` 커밋뿐이다.

- 새 코드는 이 문서를 따른다. 기존 코드 중 이 문서와 다른 곳은 9절 표에 있고 `MIGRATION_PLAN.md` Phase 1.5에서 옮긴다.
- 도구가 잡을 수 있는 것은 도구에 맡긴다. 서식·lint·import 정렬은 ruff 하나이고, 설정은 `pyproject.toml`의 `[tool.ruff]`다(Phase 1.5에서 도입).
- **API 계약 이름**(경로, 쿼리 파라미터, 헤더, 요청·응답 JSON 필드, 에러 형식, 환경 변수)은 이 문서의 이름 규칙과 달라도 바꾸지 않는다. 기준은 `tests/golden/`이다.

---

## 1. 계층과 폴더 구조

계층 우선으로 나눈다. 기능이 5개(health, search, sync, internal, kis)뿐이고 search·sync·internal이 같은 posts 인덱스를 쓰므로, 기능별 폴더보다 계층별 폴더가 import 방향이 단순하다.

```
app/
├─ main.py                  앱 생성, lifespan(클라이언트 생성·종료), CORS, 예외 핸들러·라우터 등록
├─ dependencies.py          Depends 조립(클라이언트 → 리포지토리 → 서비스), 내부 API 키 검증
├─ core/                    어느 계층에도 속하지 않는 공통 기반
│  ├─ config.py             Settings(pydantic-settings), get_settings()
│  ├─ cache.py              인프로세스 TTL 캐시
│  └─ exceptions.py         AppError 계열 예외 + 예외 → 응답 변환 핸들러
├─ routers/                 [Controller] HTTP 요청을 서비스 호출로 번역
├─ schemas/                 [DTO] API 요청·응답 Pydantic 모델
├─ services/                [Service] 비즈니스 로직
├─ repositories/            [Repository] Elasticsearch·MongoDB 조회와 저장
├─ models/                  [Entity] 저장된 문서의 형태 + ES 인덱스 매핑
└─ clients/                 [Infrastructure] 연결 생성, 외부 API(KIS)
   └─ kis/                  외부 시스템 하나 = 하위 패키지 하나
cli/                        [Controller] typer 명령. 라우터와 같은 규칙(서비스만 호출). 서비스는 dependencies.py 의 open_clients·get_*_service 로 만든다
tests/                      app/ 구조를 그대로 따른다(7절)
```

### 의존 방향

```
routers, cli ─→ services ─→ repositories ─→ clients
                  │   │           │
                  │   └─→ models ←┘
                  └─→ schemas
(모두 core 를 쓸 수 있다)
```

| 계층 | import 해도 되는 것 | import 하면 안 되는 것 |
|---|---|---|
| `routers/`, `cli/` | `services`, `schemas`, `dependencies`, `core` | `repositories`, `models`, `clients`, `elasticsearch`, `pymongo` |
| `services/` | `repositories`, `models`, `schemas`, `clients/kis`, `core` | `fastapi`, `starlette`(Request·Response·HTTPException), `elasticsearch`, `pymongo` |
| `repositories/` | `models`, `clients`, `core`, `elasticsearch`, `pymongo` | `services`, `schemas`, `fastapi` |
| `models/`, `schemas/` | `core` | 그 외 `app` 전부. 서로도 import하지 않는다(변환은 서비스가 한다) |
| `clients/` | `core` | 그 외 `app` 전부 |
| `core/` | 표준 라이브러리, 서드파티 | `app`의 다른 패키지 전부 |

역방향 import가 필요해 보이면 구조가 잘못된 것이다. 지연 import(함수 안 import)로 순환을 덮지 않는다.

| 무엇 | 어디 |
|---|---|
| 한 계층 안에서만 쓰는 도우미 | 그 파일의 `_` 접두 함수. 두 파일 이상이 쓰면 같은 계층의 별도 모듈 |
| 여러 계층이 쓰는 기반(설정, 캐시, 예외) | `core/` — 도메인 이름이 파일에 들어가면 `core`가 아니다 |
| 외부 시스템 연동 | `clients/<시스템>/` |
| 테스트 픽스처·가짜 구현 | `tests/` (`app/`에 두지 않는다) |

`utils/`, `helpers/`, `common/` 같은 잡동사니 폴더는 만들지 않는다.

## 2. 계층별 책임

### 라우터 (`routers/<그룹>.py`)

HTTP를 서비스 호출로 번역만 한다.

| 한다 | 하지 않는다 |
|---|---|
| 경로·메서드·상태 코드·`response_model`·`summary`·`tags` 선언. 경로는 **trailing slash까지** 적는다(`"/posts/"`) | `redirect_slashes`에 기대기 (NestJS의 POST가 307을 타면 안 된다) |
| 입력을 타입으로 받기: 경로 파라미터, 쿼리 파라미터 모델(`Annotated[SearchParams, Query()]`), 바디 모델, `Depends(verify_internal_key)` | `Request`를 통째로 받아 직접 파싱 (SSE의 연결 끊김 확인처럼 꼭 필요한 경우만) |
| `Depends(get_<x>_service)`로 서비스를 받아 메서드 하나를 호출하고 결과를 그대로 반환 | ES·Mongo 접근, 비즈니스 판단, 응답 dict 조립 |
| | try/except로 예외를 응답으로 바꾸기 — `core/exceptions.py`의 핸들러가 한다. 응답 계약이 요구하는 경우(헬스체크 500 형식)만 예외이고 주석으로 이유를 적는다 |

### 서비스 (`services/<기능>.py`)

비즈니스 로직이 있는 유일한 곳.

| 한다 | 하지 않는다 |
|---|---|
| 규칙 판단, 캐시 조회·저장, 여러 리포지토리 조합 | `fastapi`·`starlette` import, 상태 코드·헤더 다루기 |
| 리포지토리가 돌려준 엔티티를 응답 스키마로 변환해 반환 | 엔티티나 ES 원본 dict를 그대로 반환 |
| 실패는 `core/exceptions.py`의 `AppError` 하위 예외로 던진다(`NotFoundError` 등) | `HTTPException` 던지기, 예외를 잡아 `None`으로 바꾸기 |
| 의존(리포지토리, 설정값)은 생성자로 받는다 | `get_settings()`·클라이언트 생성을 서비스 안에서 직접 호출 |
| KIS처럼 외부 API가 곧 데이터 원천이면 `clients/kis`를 생성자로 받아 쓴다 | ES 쿼리 DSL 조립(→ 리포지토리) |

예외를 삼켜도 되는 경우는 계약이 요구할 때뿐이다(헬스체크의 degraded, 인기검색어 갱신 실패, 자동완성·인기검색어 조회 실패의 빈 목록, 동기화 결과를 `SyncResult(status=failed)`·건수 0으로 보고하는 동기화). 그때도 `logger.warning`을 남기고 주석으로 이유를 적는다. 헬스체크와 동기화 상태는 연결 자체를 보고하는 기능이라 예외적으로 `clients`의 연결 확인 함수를 쓴다(`dependencies.py`가 묶어 넘긴다).

### 리포지토리 (`repositories/<저장소>.py`)

저장소 하나(ES 인덱스 하나 또는 Mongo 컬렉션 하나)에 대한 조회와 저장.

| 한다 | 하지 않는다 |
|---|---|
| ES 쿼리 DSL 조립·실행, 색인·삭제, Mongo 집계 파이프라인 | 캐시, 비즈니스 판단, 여러 저장소를 엮는 흐름(→ 서비스) |
| 결과를 엔티티(`models/`)로 바꿔 반환. 없으면 `None` | 응답 스키마 반환, HTTP 예외 |
| 연결 객체(`Elasticsearch`, `MongoClient`)는 생성자로 받는다 | 연결 생성, 설정 읽기(→ `clients/`, `dependencies.py`) |
| 입력은 키워드 전용 인자(`def search(self, *, query: str, page: int, ...)`). 너무 많으면 `models/`의 `@dataclass`(`PostSearchCriteria`) | 스키마(`SearchParams`)를 인자로 받기 |
| 인덱스·컬렉션 이름은 파일 상단 상수(`POSTS_INDEX = "posts"`) | |

`ObjectId` 변환은 리포지토리 안에서만 한다. 리포지토리 밖에서 게시글 id는 항상 `str`이다.

### 엔티티 (`models/*.py`)

| 한다 | 하지 않는다 |
|---|---|
| 저장된 문서 형태를 Pydantic 모델로 선언. 저장소 필드 이름이 snake_case가 아니면 `alias`(`main_category: str = Field(alias="mainCategory")`) | I/O, 캐시, 설정 읽기 |
| ES 매핑·분석기 설정을 `UPPER_SNAKE` 상수 dict로 (엔티티와 같은 파일에 둬서 필드와 매핑을 함께 고친다) | 스키마(DTO) 상속, API 응답으로 직접 사용 |
| I/O 없는 순수 계산(Tiptap JSON → 평문 추출 등) | |

### 스키마 (`schemas/*.py`)

| 한다 | 하지 않는다 |
|---|---|
| 요청 검증 규칙(`Field` 제약, `field_validator`, `model_validator`) — 400 응답 형식은 핸들러가 맞춘다 | I/O, 비즈니스 로직 |
| 응답은 실제로 나가는 형태 그대로. JSON 이름이 snake_case가 아니면 `alias` + `serialize_by_alias` | 엔티티 상속, 선언과 다른 형태로 응답 |
| 고정 값 집합은 `Literal["relevance", "date_desc", "date_asc"]` | 문자열로 받고 서비스에서 비교 |

### 클라이언트 (`clients/`)

| 한다 | 하지 않는다 |
|---|---|
| 연결 생성(`create_es`, `create_mongo_client`)과 연결 확인. 설정은 여기서 `get_settings()`로 읽어도 된다 | 도메인 쿼리(posts 검색 등 → 리포지토리) |
| 외부 API 호출(KIS 토큰, WebSocket). 타임아웃을 항상 건다 | 비즈니스 판단, 응답 스키마 사용 |
| 실패 계약을 docstring에 적는다(던지는지, `None`인지) | 실패를 로그 없이 삼키기, 키·토큰·접속 문자열을 로그에 남기기 |

### `dependencies.py`

| 한다 | 하지 않는다 |
|---|---|
| `get_<x>_service` / `get_<x>_repository`: lifespan이 `app.state`에 만들어 둔 연결을 꺼내 리포지토리·서비스를 조립 | 요청마다 새 연결 생성, 로직 |
| `verify_internal_key`: `X-Internal-Key` 검사, 실패는 `AuthenticationError` | 키 비교에 `==` (→ `secrets.compare_digest`) |

### `core/`

- `config.py`: 환경 변수는 `Settings` 필드로만 읽는다. `os.environ`·`os.getenv` 직접 사용 금지. 새 변수는 `Settings`와 `.env.example`에 같이 추가한다.
- `exceptions.py`: `AppError(status_code, error, message)`와 하위 클래스(`NotFoundError`, `AuthenticationError`, `ServiceUnavailableError` …). 핸들러가 `{"error", "message"}`로, 요청 검증 오류는 400 `{"error": "Invalid request parameters", "details": {...}}`로 바꾼다(Django 응답 형식).
- `cache.py`: TTL 상수는 이 파일에 모은다.

## 3. 동기·비동기

- ES·Mongo(동기 드라이버)를 쓰는 경로는 `def`로 선언한다. FastAPI가 스레드풀에서 돌린다.
- `async def`는 그 안에서 부르는 I/O가 전부 비동기일 때만 쓴다(KIS: `httpx2.AsyncClient`, `websockets`, `asyncio.Queue`).
- `async def` 안에서 동기 I/O(`time.sleep`, `pymongo`, 동기 `elasticsearch`, 동기 `httpx2.Client`)를 호출하지 않는다. 이벤트 루프 전체가 멈춘다.
- 새 스레드(`threading`)를 만들지 않는다. 백그라운드 작업은 `BackgroundTasks` 또는 lifespan이 소유한 `asyncio` 태스크로 한다.
- uvicorn 워커 1개가 전제다(인프로세스 캐시, KIS 매니저). 이 전제에 기대는 코드에는 주석을 단다.

## 4. 이름

PEP 8과 FastAPI 공식 문서의 관례를 따른다. 계층은 패키지 이름(`services/`, `repositories/`)이 이미 말하므로 모듈 이름에 되풀이하지 않는다.

| 대상 | 규칙 | 예 |
|---|---|---|
| 패키지·모듈 | snake_case 소문자, 계층 접미사 없음 (FastAPI 문서의 `app/routers/items.py`) | `routers/search.py`, `services/search.py`, `repositories/post_index.py`, `clients/kis/auth.py` |
| 서드파티와 이름이 겹치는 모듈 | 만들지 않는다 | `clients/es.py` (`elasticsearch.py` 아님) |
| 라우터 등록 | 모듈째 import해서 `.router`를 등록 (FastAPI 문서 방식) | `from app.routers import search` → `app.include_router(search.router)` |
| 라우터 객체 | 모듈마다 `router` 하나 | `router = APIRouter(prefix="/api/v1/search", tags=["Search"])` |
| 클래스 | CapWords. 서비스·리포지토리·클라이언트는 역할을 이름 끝에 둔다 | `SearchService`, `PostIndexRepository`, `KISAuthClient` |
| 약어 | 클래스 이름에서는 모두 대문자(PEP 8의 `HTTPServerError`), 함수·변수·모듈에서는 모두 소문자 | `KISAuthClient`, `kis_auth_client` |
| 엔티티 | 단수 명사, 접미사 없음 | `Post`, `PopularSearch` |
| 스키마 | 데이터가 무엇인지 나타내는 명사. 쿼리 파라미터 묶음은 `<명사>Params`(FastAPI 문서의 `FilterParams`). 같은 리소스의 입출력 형태가 다르면 `<명사>Create` / `<명사>Update` / `<명사>Public`(공식 템플릿). `Request`·`Response`·`Dto` 접미사는 쓰지 않는다 — 방향은 라우터의 매개변수 위치와 `response_model`이 이미 말한다 | `SearchParams`, `SearchResult`, `HealthStatus`, `Snapshot` |
| 예외 | `Error`로 끝난다(PEP 8), `AppError` 상속 | `NotFoundError`, `AuthenticationError` |
| 함수·메서드·변수 | snake_case. 함수는 동사로 시작 | `search_posts`, `build_highlight` |
| 리포지토리 메서드 | 단건은 `get` / `get_by_<조건>`, 없으면 `None`(`dict.get` 관례). 목록은 `list_<조건>` → `list`, 대량 순회는 `iter_<조건>` → `Iterator`. 저장 `upsert` / `add`, 삭제 `delete`, 개수 `count` | `get(post_id)`, `iter_updated_since(since)`, `upsert(post)` |
| 서비스 메서드 | 유스케이스 동사 + 대상 | `search_posts`, `sync_incremental`, `index_post`, `remove_post` |
| 라우터 함수 | 호출하는 서비스 메서드와 같은 이름 (OpenAPI operationId가 된다) | `search_posts` |
| 의존성 함수 | 주입은 `get_<대상>`, 검사만 하면 `verify_<대상>` (FastAPI 문서의 `get_db`, `verify_token`) | `get_search_service`, `verify_internal_key` |
| boolean | `is_` / `has_` / `can_` / `should_` | `is_configured`, `has_next` |
| 상수 | 모듈 수준 `UPPER_SNAKE_CASE` | `POSTS_INDEX`, `SEARCH_TTL` |
| 모듈 밖에서 쓰지 않는 것 | `_` 접두 (PEP 8 비공개 관례) | `_build_query`, `_CACHE_KEY` |

### 통일 규칙

같은 개념은 같은 이름으로 쓴다.

| 대상 | 규칙 |
|---|---|
| 게시글 id | `post_id: str`. `ObjectId`는 리포지토리 안에서만 |
| 시각 | `datetime` 변수·필드는 `*_at`(`updated_at`, `synced_at`), 항상 timezone-aware |
| 설정 | `Settings` 인스턴스는 `settings`. `config`·`cfg` 쓰지 않는다 |
| 예외 변수 | `except ... as exc` (`e`·`err`·`error` 쓰지 않는다) |
| 연결 객체 | `es: Elasticsearch`, `mongo_client: MongoClient` |
| 축약 | `res`·`req`·`resp`·`cat`·`tmp`·`val`·한 글자 대신 도메인 이름. 예외는 반복 변수 `i`와 내포 안의 짧은 이름 |

## 5. 코드 작성

### 도구와 서식

| 항목 | 값 | 근거 |
|---|---|---|
| Python | 실행 3.13(Docker). 문법은 3.12 범위(ruff `target-version = "py312"`) | 로컬 개발 환경이 3.12 |
| 서식 | `ruff format` — 4칸 들여쓰기, 큰따옴표, 줄 길이 120 | 기존 코드가 큰따옴표, 100자를 넘는 줄이 많다 |
| lint | `ruff check` — 규칙 `E, W, F, I, N, B, UP, SIM, G`, `E501` 제외 | pycodestyle·pyflakes·isort·pep8-naming·bugbear·pyupgrade·simplify·로깅 포맷. 코드 줄 길이는 formatter가 맞추고, 주석은 한글이 폭 2로 계산돼 E501이 한국어 주석마다 걸린다 |
| 줄바꿈 | LF | `.gitattributes` |

커밋 전 확인: `ruff format --check . && ruff check . && pytest -q` (venv 안에서. Windows는 `.venv/Scripts/` 접두)

### 규칙

- **타입 힌트**: 공개 함수·메서드는 모든 매개변수와 반환형을 적는다. `X | None`(`Optional` 쓰지 않음), 내장 제네릭(`list[str]`, `dict[str, Any]`), 반복자는 `collections.abc.Iterator`. `Any`는 ES·Mongo 원본 문서를 다룰 때만.
- **import**: 항상 절대경로(`from app.services.search import SearchService`). 상대 import, `import *` 금지. `__init__.py`는 비워 둔다(재노출로 import 경로를 줄이지 않는다). 순서는 ruff(isort)가 정한다.
- **의존성 주입**: 서비스·리포지토리는 클래스이고 의존은 생성자로 받는다. 모듈 전역 인스턴스를 만들지 않는다 — 예외는 `core/cache.py`의 `cache`뿐이다. 앱 수명과 같은 객체(ES·Mongo 클라이언트, KIS 스트림 매니저)는 lifespan이 만들어 `app.state`에 둔다.
- **Pydantic v2 API만**: `model_dump`, `model_validate`, `field_validator`, `ConfigDict`. v1 API(`.dict()`, `parse_obj`, `@validator`, `class Config`) 금지.
- **시간**: `datetime.now(UTC)`. naive `datetime` 금지. KST는 표시용 문자열(`last_check`)에만.
- **로깅**: 모듈마다 `logger = logging.getLogger(__name__)`. `print` 금지. 인자는 `%s` 지연 포맷(`logger.warning("동기화 실패: %s", exc)`) — f-string 금지(ruff G004). 비밀번호·토큰·API 키·접속 URI는 어떤 레벨에도 남기지 않는다. 스택이 필요하면 `logger.exception`.
- **예외 처리**: bare `except:` 금지. `except Exception`은 경계(헬스체크, fire-and-forget, 백그라운드 태스크, 전역 핸들러)에서만, 로그와 함께. 바꿔 던질 때는 `raise NewError(...) from exc`.
- **매직 값**: 타임아웃·TTL·인덱스 이름·크기 제한은 이름 있는 상수로 둔다.
- **응답 에러에** 스택·내부 URI·접속 문자열을 넣지 않는다.

## 6. 주석과 docstring

- **언어·문체**: 한국어, "~한다 / ~이다" 평서문. 식별자·라이브러리 이름은 원문 그대로 쓴다.
- **주석은 "왜"를 쓴다.** 코드가 무엇을 하는지 되풀이하지 않는다. 계약·호환·외부 제약·버그 회피처럼 코드만 봐서는 알 수 없는 이유를 적는다.
- **모듈 docstring**: 모든 모듈 첫 줄에 이 파일의 역할을 한 줄로 쓴다(빈 `__init__.py` 제외).
- **클래스·공개 함수 docstring**(PEP 257): 첫 줄은 한 줄 요약. 필요하면 빈 줄 다음에 주의사항과 **실패 계약**(무엇을 던지는지, `None`을 돌려주는지, 부수 효과)을 쓴다. Google·NumPy 스타일의 `Args:`·`Returns:` 섹션은 쓰지 않는다 — 이름과 타입 힌트가 대신한다(PEP 257은 섹션 형식을 정하지 않는다). `_` 함수는 이름만으로 부족할 때만 쓴다.
- **라우터 함수 docstring은 `/docs`에 그대로 노출된다.** API를 부르는 쪽 관점(캐시 시간, 실패 시 응답)으로 쓰고, 내부 구현 이야기는 `#` 주석으로 쓴다. `summary=`는 짧은 한국어 명사구.
- **Django 원본과 동작이 다른 곳**(버그 수정, 의도적 변경)은 그 자리에 원본 동작과 이유를 주석으로 남긴다. 컷오버(Phase 6) 후 정리한다.
- **계약 때문에 어색한 코드**(trailing slash, 헬스체크 500 형식, categories fallback 목록)는 계약이라는 사실을 주석으로 밝힌다.
- **TODO**: `# TODO(Phase N): 내용`. 단계가 정해지지 않은 일은 코드가 아니라 `MIGRATION_PLAN.md`에 적는다.
- 주석 처리한 코드를 남기지 않는다(git이 기억한다). 쓰지 않는 파일은 지운다.
- `# noqa`·`# type: ignore`는 규칙 코드와 이유를 같이 쓴다(`# noqa: N815 — ES 필드 이름 그대로`).

```python
"""ES posts 인덱스 검색·색인 리포지토리."""

POSTS_INDEX = "posts"  # posts_v2 를 가리키는 alias. 인덱스 버전이 바뀌어도 이 이름으로 접근한다.


class PostIndexRepository:
    """posts alias 에 대한 검색·색인·삭제."""

    def get(self, post_id: str) -> Post | None:
        """게시글 하나를 조회한다. 없으면 None (NotFoundError 판단은 서비스가 한다)."""
```

## 7. 테스트

- `tests/`는 `app/` 구조를 따른다: `tests/routers/test_search.py`, `tests/services/test_search.py`, `tests/repositories/test_post_index.py`. 하위 폴더마다 빈 `__init__.py`를 둔다 — 없으면 같은 파일 이름(`test_search.py`)이 여러 폴더에 있을 때 pytest가 모듈 이름 충돌로 실패한다. 공용 픽스처는 `tests/conftest.py`. 가짜 구현은 한 파일에서만 쓰면 그 파일에, 여러 파일이 쓰면 `tests/fakes.py`에 둔다.
- 이름: 파일 `test_<대상 파일명>.py`, 함수 `test_<대상>_<기대 결과>[_when_<조건>]`(예: `test_health_degraded_when_mongo_down`). 테스트 더블은 손으로 짠 구현이 `Fake<클래스>`, `MagicMock`이 `mock_<대상>`.
- 단위 테스트는 네트워크·실제 DB 없이 돈다. 서비스는 가짜 리포지토리를 생성자로 넘기고, 라우터는 `app.dependency_overrides`로 서비스를 바꾼다. `monkeypatch`로 모듈 내부 함수를 바꾸지 않는다 — DI로 해결한다.
- 응답 계약은 `tests/golden/`과 키 구조·상태 코드를 비교한다. 계약이 걸린 엔드포인트는 golden 테스트를 꼭 둔다.
- 비동기 테스트는 `@pytest.mark.anyio`(anyio pytest 플러그인, `tests/conftest.py`가 asyncio로 고정). pytest-asyncio는 쓰지 않는다.
- 실제 ES·Mongo·KIS에 붙는 테스트는 `@pytest.mark.e2e`를 달고 기본 실행에서 뺀다. 로컬에 띄운 가짜 서버(WebSocket 등)는 외부 네트워크가 아니므로 기본 실행에 둔다.
- 서비스 메서드마다 정상 1건 + 던지는 예외 종류마다 1건. 버그 수정 커밋에는 그 버그를 재현하는 테스트를 같이 넣는다.

## 8. Git

- 커밋 제목: `type: 한국어 요약` — `feat` 기능, `fix` 버그, `refactor` 동작 변화 없는 구조 변경, `test`, `docs`, `style` 서식만, `chore` 의존성·설정, `build`. 72자 이내, 마침표 없음. 본문에 이유와 영향 범위.
- 한 커밋에 한 주제. ruff 일괄 서식 적용 같은 `style:` 커밋과 로직 커밋을 섞지 않는다.
- `main`에 올리기 전 5절의 커밋 전 확인이 통과해야 한다.

## 9. 현행 코드와 다른 곳

구조·이름 차이는 2026-10-29 Phase 1.5에서, 마지막 차이(`PostRepository`가 Mongo 원본 `dict`를 돌려주던 것)는 Phase 3에서 정리했다. 지금은 없다.
새로 생기면 아래 표에 적고, 옮기면 행을 지운다.

| 현행 | 이 문서 | 옮기는 단계 |
|---|---|---|
