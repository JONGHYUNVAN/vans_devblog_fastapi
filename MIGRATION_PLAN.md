# Django → FastAPI 전환 계획 (vans_devblog_django)

작성일: 2026-10-28
상태: **Phase 0·1 완료 (2026-10-28)**. 원본 계획은 `vans_devblog_django/docs/FASTAPI_MIGRATION_PLAN.md`.

---

## 1. 현재 상태 검토 요약

### 1.1 실제로 쓰이는 것

| 영역 | 파일 | 비고 |
|---|---|---|
| 검색 API | `search/api/views.py` (631줄) | 7개 공개 + 2개 내부 엔드포인트. 절반 이상이 swagger 데코레이터·로깅 보일러플레이트 |
| 요청/응답 검증 | `search/api/serializers.py` (502줄) | DRF Serializer. 응답 serializer는 문서용으로만 존재, 실제 응답은 dict 직접 반환 |
| 검색 로직 | `search/services/search_service.py` | ES 쿼리 빌드 + 캐시 + 인기검색어 비동기 갱신(스레드) |
| 동기화 | `search/services/sync_service.py` | MongoDB → ES 전체/증분 동기화, 고스트 문서 정리 |
| ES 클라이언트 | `search/clients/elasticsearch_client.py` | `elasticsearch-py` 직접 사용 (dsl 미사용) |
| Mongo 클라이언트 | `search/clients/mongodb_client.py` | `pymongo` 직접 사용. **미커밋 변경 있음** |
| ES 문서/매핑 | `search/documents/post_document.py`, `analyzers.py`, `popular_search_document.py` | `PostDocument`만 `elasticsearch_dsl.Document` 의존. 인기검색어는 이미 raw client |
| KIS 실시간 | `kis/views.py`, `kis/services/kis_websocket.py` (431줄), `kis_auth.py`, `kis_parser.py` | 싱글톤 + 별도 스레드 + 자체 asyncio 루프. SSE는 `StreamingHttpResponse` 제너레이터 |
| 관리 명령 | `search/management/commands/*` 5개 | sync, migrate_search_index, setup_popular_searches, fetch_mongodb_data(중복), test_mongodb_connection |

### 1.2 Django를 쓰면서 실제로 얻는 것이 없는 부분

- `search/models.py`의 `SearchLog`, `PopularSearch` ORM 모델: **어디서도 사용 안 함** (grep 결과 0건). 인기검색어는 ES 인덱스 `vans_popular_searches`로 대체됨.
- SQLite DB: admin/session용으로만 존재. 운영에서도 SQLite. → 관계형 DB 자체가 불필요.
- `django.contrib.admin`, sessions, CSRF, messages, templates, staticfiles, whitenoise: admin과 swagger UI 정적파일 때문에만 존재.
- `drf_yasg`: FastAPI 내장 OpenAPI로 대체 가능.
- `SuggestionDocument`: 사용처 없음.
- `elasticsearch_client.get_popular_searches()`: 더미 데이터, 사용처 없음.
- `docs/build/*` (Sphinx 빌드 산출물), `staticfiles/`, `.idea/`, `db.sqlite3`가 저장소에 커밋되어 있음.

### 1.3 Django 의존 지점 (앱 코드 기준, settings 제외)

| 의존 | 위치 | 대체 |
|---|---|---|
| `django.core.cache` | `views.py`(health), `cache_service.py`, `sync_service.py`, `kis_auth.py` | `cachetools.TTLCache` 기반 인프로세스 캐시 |
| `django.conf.settings` | `cache_service.py`, `elasticsearch_client.py`, `popular_search_document.py`, `views.py`(INTERNAL_API_KEY) | `pydantic-settings` |
| `django.utils.timezone` | `sync_service.py`, `search_service.py`, `models.py` | `datetime.now(timezone.utc)` |
| DRF `api_view`/`Response`/serializers | `views.py`, `kis/views.py`, `serializers.py` | FastAPI 라우터 + pydantic |
| `elasticsearch_dsl` | `post_document.py`(Document), `analyzers.py`(analyzer 객체), `sync_service.py`(Search.count/delete/scan) | dict 매핑 + `es.index/get/delete/count/delete_by_query/helpers.scan` |
| `BaseCommand` | management commands 5개 | `typer` CLI |
| `StreamingHttpResponse` | `kis/views.py` | `StreamingResponse` 또는 `sse-starlette` |

### 1.4 발견한 문제 (전환과 무관하게 현재 존재)

1. **SSE + gunicorn sync 워커 2개**: SSE 연결 1개가 워커 1개를 점유. 동시 SSE 클라이언트 2개면 헬스체크·검색까지 전부 대기. FastAPI/uvicorn 비동기로 해결되는 가장 큰 실익.
2. **멀티 프로세스에서 KIS 싱글톤이 프로세스별로 따로 생김**: 워커 2개 → KIS WebSocket 연결 2개, 캐시도 분리. snapshot 요청이 어느 워커에 가느냐에 따라 데이터 유무가 달라짐.
3. `snapshot` 뷰의 `time.sleep(5)`: 워커 블로킹.
4. `cloudtype.yml`이 참조하는 `vans_search_service.settings.cloudtype` 모듈이 **존재하지 않음** (base/development/production/testing만 있음). 실제 CloudType 배포 설정은 대시보드에 별도로 있을 가능성 → 컷오버 전 확인 필요.
5. `elasticsearch==7.17.9` 클라이언트로 ES 8.15 서버 접속 중 (호환 모드로 동작). 전환 시 8.x 클라이언트로 올리는 것이 자연스러움.
6. `settings/base.py`가 import 시점에 `print()`로 ES 호스트·유저명을 stdout에 찍음.
7. 프론트엔드가 같은 서비스를 두 환경변수(`SEARCH_API_URL`, `DJANGO_API_URL`)로 가리킴.
8. 메모리 참조 파일(`ref_be_search.md`)이 Go 서비스 기준으로 작성되어 있어 현재 구조와 불일치.

### 1.5 외부 소비자 (반드시 유지해야 할 계약)

**vans_story (Next.js BFF)**
- `SEARCH_API_URL` → `GET /api/v1/search/posts/?query=&language=all&page=1&page_size=20&sort=relevance`
- `SEARCH_API_URL` → `GET /api/v1/search/autocomplete/?query=&language=&limit=`
- `SEARCH_API_URL` → `GET /api/v1/search/popular/`
- `DJANGO_API_URL` → `GET /api/kis/snapshot/{symbol}/`
- `DJANGO_API_URL` → `GET /api/kis/stream/{symbol}/` (SSE, Edge 런타임 passthrough)

**vans_story_be_post (NestJS)**
- `SEARCH_SERVICE_URL` → `POST /api/internal/search/index/` (헤더 `X-Internal-Key`, 3초 타임아웃, fire-and-forget)
- `SEARCH_SERVICE_URL` → `DELETE /api/internal/search/index/{post_id}/`

**CloudType 헬스체크**
- `GET /api/v1/search/health/`

> 모든 경로가 **trailing slash**로 끝남. FastAPI 기본 `redirect_slashes`에 의존하지 말고 경로를 슬래시 포함으로 명시해야 함 (NestJS POST가 307 리다이렉트를 타면 안 됨).

---

## 2. 전환 여부 판단

**전환 권장.** 근거:
- Django 고유 기능(ORM, admin, 세션, 템플릿) 사용처가 0.
- 가장 큰 실익은 KIS SSE/WebSocket을 단일 이벤트 루프에서 처리하는 것. 현재 구조(sync 워커 + 스레드 + 자체 루프)는 동시 접속에 구조적으로 취약.
- `pydantic`은 이미 requirements에 있음. 502줄 serializer를 절반 이하로 줄일 수 있음.
- ES 인덱스·데이터 변경이 전혀 없음 (`posts` alias → `posts_v2`, `vans_popular_searches` 그대로). 데이터 마이그레이션 리스크 0.

리스크:
- 응답 계약 유지 (특히 400 에러 형식, trailing slash).
- KIS 매니저를 스레드 기반에서 asyncio 네이티브로 재작성.
- CloudType 서비스를 Dockerfile 기반으로 새로 만들고 환경변수 옮기기.
- 테스트 ~100개가 모두 Django/DRF `APIClient` 의존 → 재작성 필요.

---

## 3. 목표 구조

새 저장소 `vans_devblog_fastapi`(로컬 `D:/blog/vans_devblog_fastapi`)에서 작업. Django 저장소는 컷오버 확인 후 보관(archive).

```
app/
  main.py              # FastAPI 인스턴스, lifespan(ES/Mongo 클라이언트, 인기검색어 인덱스 보장), CORS, 예외 핸들러
  config.py            # pydantic-settings (아래 환경변수 표)
  cache.py             # TTLCache 래퍼 (search 5분 / autocomplete 10분 / popular 1분 / health 30분 / last_sync 30일)
  deps.py              # get_es, get_mongo, verify_internal_key(Header X-Internal-Key)
  routers/
    health.py          # GET /api/v1/search/health/
    search.py          # posts/, autocomplete/, popular/, categories/
    sync.py            # sync/status/, sync/, sync/all/
    internal.py        # /api/internal/search/index/ (+ /api/v1/search/internal/index/ 별칭 유지)
    kis.py             # /api/kis/snapshot/{symbol}/, /api/kis/stream/{symbol}/
  schemas/
    search.py          # SearchRequest(Query 파라미터 모델), SearchResponse, AutocompleteRequest/Response, Popular, Categories
    sync.py            # SyncRequest, SyncResponse, SyncStatus
    kis.py             # SnapshotResponse
    errors.py          # {"error","message"} / {"error","details"} 형식
  services/
    search_service.py  # 포팅 (django 제거)
    sync_service.py    # 포팅 (Search → es.count/delete_by_query/helpers.scan)
    health_service.py  # 포팅
  clients/
    es_client.py       # elasticsearch 8.x 동기 클라이언트
    mongo_client.py    # pymongo (현재 코드 그대로)
  search_index/
    analyzers.py       # BASE_INDEX_SETTINGS dict (elasticsearch_dsl analyzer 객체 제거)
    post_index.py      # posts 매핑 dict + upsert/get/delete 함수 (PostDocument 대체), extract_tiptap_text
    popular_index.py   # 현재 popular_search_document.py 그대로
  kis/
    constants.py, parser.py        # 그대로
    auth.py                        # requests → httpx, 캐시 → TTLCache(23h)
    manager.py                     # asyncio 네이티브 싱글톤 (스레드/Lock/queue.Queue 제거)
cli/
  __main__.py          # typer: sync-posts, migrate-index, setup-popular, test-mongo
tests/
Dockerfile
requirements.txt (또는 pyproject.toml)
```

**동기/비동기 방침**
- 검색·동기화·내부 인덱싱: 기존 동기 코드 유지, 라우터를 `def`로 선언 → FastAPI가 스레드풀에서 실행. 포팅 비용 최소.
- KIS: `async def`. WebSocket 매니저는 앱 이벤트 루프에서 `asyncio.create_task`로 실행, 리스너는 `asyncio.Queue`.
- 인기검색어 갱신(현재 daemon 스레드): `BackgroundTasks`.

**프로세스 모델**
- uvicorn **워커 1개** 고정. KIS 싱글톤과 인프로세스 캐시 때문에 멀티 프로세스 불가 (현재 Django도 사실상 깨져 있던 전제). 512Mi/0.5 CPU 플랜에 충분.

---

## 4. 환경변수 (현재 → FastAPI)

| 현재 | FastAPI | 비고 |
|---|---|---|
| `ELASTICSEARCH_HOST` (스킴 없음) | `ES_HOST` | `https://{host}`로 조립, 그대로 |
| `ELASTICSEARCH_USERNAME` / `PASSWORD` | `ES_USERNAME` / `ES_PASSWORD` | |
| `MONGODB_URI`, `MONGODB_DATABASE` | 동일 | URI 경로에서 DB명 추출 로직 유지 |
| `INTERNAL_API_KEY` | 동일 | 기본값 하드코딩 제거 → 필수값 |
| `KIS_APP_KEY`, `KIS_APP_SECRET`, `KIS_IS_REAL` | 동일 | |
| `CORS_ALLOWED_ORIGINS` | 동일 | cloudtype.app 정규식도 유지 |
| `SEARCH_CACHE_TIMEOUT` 등 | 동일 | |
| `PORT` | 동일 | |
| `DJANGO_SETTINGS_MODULE`, `SECRET_KEY`, `ALLOWED_HOSTS`, `DEBUG`, `REDIS_URL`, `MARIADB_*` | **삭제** | |

> 기존 이름을 그대로 써도 무방. 이름 변경은 선택.

---

## 5. 단계별 계획

### Phase 0 — 준비 (코드 작성 전)
- [x] `search/clients/mongodb_client.py` 미커밋 변경(certifi CA 지정) 확인. 새 코드에 TLS 연결일 때만 적용하는 형태로 옮김. Django 저장소는 손대지 않음.
- [x] 배포 중인 Django 서비스 응답 13종을 `tests/golden/`에 저장. 관찰 사항은 `tests/golden/README.md`.
- [ ] CloudType 대시보드에서 현재 Django 서비스의 실제 시작 명령·환경변수 확인 (cloudtype.yml과 불일치). **사용자 확인 필요.**
- [x] 새 저장소 `vans_devblog_fastapi` 생성 (계획 변경: 같은 저장소 브랜치 대신 새 저장소).

### Phase 1 — 골격 + 헬스체크
- [x] `app/config.py`, `app/main.py`, `app/cache.py`, CORS, 예외 핸들러(`RequestValidationError` → 400 `{"error":"Invalid request parameters","details":...}`).
- [x] `GET /api/v1/search/health/` (30분 캐시, 응답 형식 유지). ES 연결 확인을 `ping()` 대신 `info()`로 바꿔 실패 원인이 로그에 남게 함.
- [x] `Dockerfile` (python 3.13-slim, 비루트 사용자, `uvicorn --workers 1`).
- [x] `/` → `/docs` 리다이렉트.
- [x] `app/clients/mongo_client.py` 포팅 (실사용 메서드만). 증분 동기화도 카테고리 조회를 거치도록 수정.

### Phase 2 — 검색 API
- [ ] pydantic 스키마: `SearchRequest`(query≤200, mainCategory, subCategory, theme/category 하위호환, tags CSV→list 최대 10개, language ko/en/all, date_from/to + 순서 검증, page≥1, page_size 1~100, sort relevance/date_desc/date_asc), `AutocompleteRequest`(query 1~100자, limit 1~20).
- [ ] `es_client.py` 8.x 문법으로 포팅 (`body=` → `query=`/`source=`/`highlight=`…). 쿼리 내용은 동일.
- [ ] `search_service.py`, `cache_service` 포팅. 응답 dict 형식 `{total,page,page_size,total_pages,post_ids,results,aggregations}` 유지.
- [ ] `popular/`, `categories/` (categories는 현재 Mongo 클라이언트 None → 항상 fallback 목록 반환. 동작 그대로 유지하되 주석으로 명시).
- [ ] golden 파일과 응답 diff.

### Phase 3 — 내부 인덱싱 + 동기화 + CLI
- [ ] `post_index.py`: `PostDocument` 매핑을 dict로 (이미 `migrate_search_index._build_mapping`에 dict 버전 존재 → 재사용). `upsert(post_id, doc)`, `delete(post_id)`는 `posts` alias에 대해 `es.index/es.delete`.
- [ ] `POST/DELETE /api/internal/search/index/` + `/api/v1/search/internal/index/` 별칭. 403 `{"error":"Authentication failed","message":...}` 유지. 404 형식 유지.
- [ ] `sync_service.py` 포팅: `Search().count()` → `es.count(index="posts")`, `Search().delete()` → `es.delete_by_query`, `s.scan()` → `helpers.scan`.
- [ ] `sync/status/`, `sync/`, `sync/all/`.
- [ ] `cli/` typer 명령 4개 (fetch_mongodb_data는 sync와 중복이라 제외).

### Phase 4 — KIS
- [ ] `kis/auth.py`: httpx + TTLCache(82800s).
- [ ] `kis/manager.py`: 스레드·`threading.Lock`·`queue.Queue` 제거. `subscribe()`가 `asyncio.Queue`를 반환, 매니저 태스크는 첫 구독 시 lazy 시작. 재연결(최대 10회/5초), PINGPONG, AES 복호화, 파서는 그대로.
- [ ] `snapshot`: `await asyncio.sleep(5)`. 응답 형식 `{"success","data":{symbol,name,trade,orderbook,updatedAt}}` 및 400/503 형식 유지.
- [ ] `stream`: `StreamingResponse(media_type="text/event-stream")`, 헤더 `Cache-Control: no-cache`, `X-Accel-Buffering: no`. 이벤트명 `connected/trade/orderbook/ping/error`, ping 15초 유지. `request.is_disconnected()`로 구독 해제. KIS 미설정 시 ping-only degraded 스트림 유지.
- [ ] `tests/test_kis*.py` 포팅 (현재 44개).

### Phase 5 — 테스트·문서
- [ ] `httpx.TestClient` 기반으로 `test_api`, `test_services`, `test_clients` 포팅. e2e는 마커로 분리.
- [ ] README 전면 교체, `docs/source`(Sphinx) 삭제 또는 FastAPI 기준으로 축소.
- [ ] `.gitignore`에 `docs/build/`, `staticfiles/`, `.idea/`, `db.sqlite3` 추가하고 추적 해제.

### Phase 6 — 배포·컷오버
- [ ] CloudType에 **Dockerfile 서비스 신규 생성** (Post BE가 2026-10-25에 한 방식과 동일). 환경변수 복사. 헬스체크 경로 `/api/v1/search/health/`.
- [ ] 새 서비스 URL로 수동 검증: 검색 3종, 내부 인덱싱 POST/DELETE, KIS snapshot/stream, `sync/all/` (MongoDB Atlas 허용 IP 문제로 로컬에서는 불가 → CloudType에서만).
- [ ] Vercel `SEARCH_API_URL`, `DJANGO_API_URL` → 새 URL. NestJS CloudType `SEARCH_SERVICE_URL` → 새 URL.
- [ ] 1~2일 관찰 후 Django CloudType 서비스 중지.
- [ ] 저장소에서 Django 코드 삭제 (`vans_search_service/`, `search/api`, `search/models.py`, `migrations/`, `management/`, `manage.py`, gunicorn 설정, `cloudtype.yml`, `cloudtype-deploy.sh`, `Procfile`).
- [ ] 메모리 참조 파일 갱신 (`ref_be_search.md`, `ref_services.md`, `MEMORY.md` 서비스 표).

---

## 6. 의존성 (예상 requirements)

```
fastapi
uvicorn[standard]
pydantic>=2
pydantic-settings
elasticsearch>=8.15,<9
pymongo
httpx
websockets
pycryptodome
cachetools
typer
python-dotenv
# dev
pytest, pytest-asyncio, pytest-timeout
```

삭제: Django, djangorestframework, django-cors-headers, whitenoise, dj-database-url, psycopg2-binary, django-redis, redis, django-elasticsearch-dsl, elasticsearch-dsl, PyMySQL, drf-yasg, gunicorn, requests, uvloop(uvicorn[standard]에 포함), setproctitle.

---

## 7. 결정이 필요한 항목

1. **저장소**: ~~같은 저장소~~ → **새 저장소 `vans_devblog_fastapi`로 결정 (2026-10-28).**
2. **KIS를 같은 서비스에 유지**할지 분리할지. → 권장: 유지 (단일 프로세스로 충분, 분리하면 서비스가 하나 더 늘어남).
3. **환경변수 이름** 유지 vs 정리(`ELASTICSEARCH_*` → `ES_*`). → 권장: 유지 (CloudType 복사만 하면 됨).
4. 프론트 `SEARCH_API_URL`/`DJANGO_API_URL` 통합 여부. → 전환과 별개, 선택.

## 8. 대략적 작업량

| Phase | 규모 |
|---|---|
| 0~1 | 반나절 |
| 2~3 | 1일 |
| 4 | 반나절~1일 (KIS 매니저 재작성 + 테스트) |
| 5 | 반나절 |
| 6 | 반나절 + 관찰 기간 |
