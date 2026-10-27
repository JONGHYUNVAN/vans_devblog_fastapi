# Golden responses

2026-10-28 운영 Django 서비스(`port-0-vans-devblog-django-…sel4.cloudtype.app`)에서 캡처한 응답이다.
FastAPI 전환 후 같은 요청의 응답 형식을 비교하는 기준으로 쓴다. 값(검색 점수, 건수, 시간)은 바뀔 수 있고, 키 구조와 상태 코드가 비교 대상이다.

| 파일 | 요청 | 상태 |
|---|---|---|
| health.json | GET /api/v1/search/health/ | 200 |
| search_empty.json | GET /api/v1/search/posts/?query=&language=all&page=1&page_size=5&sort=relevance | 200 |
| search_query.json | GET /api/v1/search/posts/?query=React&… | 200 |
| search_invalid_page.json | GET /api/v1/search/posts/?page=0 | 400 |
| autocomplete.json | GET /api/v1/search/autocomplete/?query=Re&language=all&limit=10 | 200 |
| autocomplete_missing.json | GET /api/v1/search/autocomplete/ | 400 |
| popular.json | GET /api/v1/search/popular/ | 200 |
| categories.json | GET /api/v1/search/categories/ | 200 |
| sync_status.json | GET /api/v1/search/sync/status/ | 200 |
| internal_index_nokey.json | POST /api/internal/search/index/ (키 없음) | 403 |
| internal_delete_nokey.json | DELETE /api/internal/search/index/doesnotexist/ (키 없음) | 403 |
| kis_snapshot.json | GET /api/kis/snapshot/005930/ | 200 |
| kis_snapshot_invalid.json | GET /api/kis/snapshot/12345/ | 400 |
| kis_stream.txt | GET /api/kis/stream/005930/ | 200, 본문 없음 |

관찰 사항:
- 슬래시 없는 경로(`/api/v1/search/popular`)는 301로 슬래시 경로에 리다이렉트된다.
- KIS 스트림은 헤더만 오고 25초간 본문이 없었다. 운영에서 이미 동작하지 않는 상태.
- sync_status의 `total_docs_in_elasticsearch`(39)는 모든 인덱스 합계다. posts 34 + vans_popular_searches 5.
- 날짜는 ES `_source` 그대로(`2025-03-04T11:12:09.369000`, 타임존 없음) 반환된다.
- 400 응답의 `details`는 필드명 → 메시지 배열이다. 프론트는 400 본문의 `message`만 읽으므로 메시지 문구는 계약이 아니다.
