# vans_devblog_fastapi

VansDevBlog 검색·실시간 시세 서비스. `vans_devblog_django`(Django + DRF)를 FastAPI로 옮기는 중이다.
전환 계획과 진행 상태는 [MIGRATION_PLAN.md](MIGRATION_PLAN.md)에 있다.

- Elasticsearch 8.15 + Nori 게시물 검색 (`posts` alias → `posts_v2`)
- MongoDB(Post BE) → ES 동기화, NestJS 내부 인덱싱 API
- KIS OpenAPI 실시간 체결가·호가 SSE

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

## 테스트

```bash
.venv/Scripts/python -m pytest -q
```

`tests/golden/`은 운영 Django 서비스 응답을 캡처한 것이다. 키 구조와 상태 코드를 비교하는 기준으로 쓴다.

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
