# vans_devblog_fastapi

VansDevBlog 검색·실시간 시세 서비스. `vans_devblog_django`(Django + DRF)를 FastAPI로 옮기는 중이다.
전환 계획과 진행 상태는 [MIGRATION_PLAN.md](MIGRATION_PLAN.md)에 있다.

- Elasticsearch 8.15 + Nori 게시물 검색 (`posts` alias → `posts_v2`)
- MongoDB(Post BE) → ES 동기화, NestJS 내부 인덱싱 API
- KIS OpenAPI 실시간 체결가·호가 SSE

코드는 계층형 구조(`routers` → `services` → `repositories` → `models`, 연결은 `clients`)다.
계층별 책임, 이름·주석 규칙은 [CONVENTIONS.md](CONVENTIONS.md)에 있다.

## 로컬 실행

```bash
python -m venv .venv
.venv/Scripts/python -m pip install -r requirements-dev.txt   # Windows
cp .env.example .env                                          # 값 채우기
.venv/Scripts/python -m uvicorn app.main:app --port 8002
```

- API 문서: http://localhost:8002/docs
- 헬스체크: http://localhost:8002/api/v1/search/health/
- 이 PC는 MongoDB Atlas 허용 IP에 없어서 로컬 헬스체크의 mongodb는 down으로 나온다.

## 운영 명령

```bash
.venv/Scripts/python -m cli --help
.venv/Scripts/python -m cli sync-status                 # MongoDB·ES 연결과 게시글 수
.venv/Scripts/python -m cli sync-posts --dry-run        # MongoDB → ES 동기화 (건수만 확인)
.venv/Scripts/python -m cli setup-popular               # 인기 검색어 인덱스 생성
```

컨테이너에서는 `python -m cli ...`. HTTP로는 `POST /api/v1/search/sync/`(`X-Internal-Key` 필요)가 같은 동기화를 한다.

## 테스트

```bash
.venv/Scripts/ruff format --check . && .venv/Scripts/ruff check . && .venv/Scripts/python -m pytest -q
```

커밋 전에 위 세 가지가 통과해야 한다. 기본 테스트는 네트워크·실제 DB 없이 돈다(KIS 매니저 테스트는 로컬 WebSocket 서버를 띄운다).
`tests/golden/`은 운영 Django 서비스 응답을 캡처한 것이다. 키 구조와 상태 코드를 비교하는 기준으로 쓴다.

배포된 서비스는 e2e 테스트로 확인한다. 기본 실행에서 빠지고, 운영에 돌려도 되게 읽기 요청 위주다.

```bash
E2E_BASE_URL=https://<서비스 주소> .venv/Scripts/python -m pytest -m e2e
```

`E2E_INTERNAL_KEY`를 함께 주면 내부 색인 API로 테스트 문서를 색인했다가 지우는 것까지 확인한다.

## 배포

CloudType Dockerfile 서비스로 배포한다. 컨테이너는 `PORT` 환경변수(기본 8000)로 뜨고, uvicorn 워커는 1개로 고정한다.
KIS WebSocket 매니저와 인프로세스 캐시가 단일 프로세스를 전제로 하기 때문이다.

환경변수는 Django 서비스와 같은 이름을 쓴다. 목록은 [.env.example](.env.example)에 있다.
Django 전용 변수(`DJANGO_SETTINGS_MODULE`, `SECRET_KEY`, `ALLOWED_HOSTS`, `DEBUG`, `REDIS_URL`, `MARIADB_*`)는 필요 없다.

## API 계약

외부 소비자가 호출하는 경로는 모두 trailing slash로 끝나며 Django 서비스와 같다.

| 소비자 | 경로 |
|---|---|
| vans_story BFF | `GET /api/v1/search/posts/`, `autocomplete/`, `popular/` |
| vans_story BFF | `GET /api/kis/snapshot/{symbol}/`, `GET /api/kis/stream/{symbol}/` (SSE) |
| vans_story_be_post | `POST /api/internal/search/index/`, `DELETE /api/internal/search/index/{post_id}/` (`X-Internal-Key`) |
| CloudType | `GET /api/v1/search/health/` |
| 운영자 | `GET /api/v1/search/sync/status/`, `POST /api/v1/search/sync/`·`sync/all/` (`X-Internal-Key`) |

그 밖에 `GET /api/v1/search/categories/`와 Django에 있던 별칭 `/api/v1/search/internal/index/`도 그대로 받는다.
응답 형식은 `tests/golden/`과 같고, Django와 달라진 곳은 [MIGRATION_PLAN.md](MIGRATION_PLAN.md) 각 Phase에 적었다.
