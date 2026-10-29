"""배포된 서비스에 실제 요청을 보내 응답 계약(golden)을 확인한다. 기본 실행에서 빠진다.

사용: E2E_BASE_URL=https://<서비스 주소> pytest -m e2e
내부 색인 API 의 색인·삭제까지 보려면 E2E_INTERNAL_KEY 도 준다. 테스트 문서를 색인했다가 바로 지운다
(그 사이 빈 검색어 결과에 잡히면 검색 캐시 5분 동안 보일 수 있다).

운영에 돌려도 되게 읽기 위주로 짰다. 검색어가 있는 검색은 인기 검색어 횟수를 올리므로 보내지 않는다.
"""

import os
from collections.abc import Iterator

import httpx2
import pytest

from tests.conftest import load_golden

pytestmark = pytest.mark.e2e

BASE_URL = os.environ.get("E2E_BASE_URL", "").rstrip("/")
INTERNAL_KEY = os.environ.get("E2E_INTERNAL_KEY", "")
_TIMEOUT = 30
_STREAM_FIRST_EVENT_TIMEOUT = 10
_TEST_POST_ID = "e2e-test-post"


@pytest.fixture(scope="module")
def http() -> Iterator[httpx2.Client]:
    if not BASE_URL:
        pytest.skip("E2E_BASE_URL 이 없다")
    with httpx2.Client(base_url=BASE_URL, timeout=_TIMEOUT) as client:
        yield client


def test_health_matches_golden_keys(http):
    golden = load_golden("health")

    response = http.get("/api/v1/search/health/")

    assert response.status_code == 200
    assert set(response.json()) == set(golden)
    assert set(response.json()["components"]) == set(golden["components"])


def test_search_without_query_matches_golden_keys(http):
    golden = load_golden("search_empty")

    response = http.get("/api/v1/search/posts/?query=&language=all&page=1&page_size=5&sort=relevance")

    assert response.status_code == 200
    body = response.json()
    assert set(body) == set(golden)
    assert body["total"] > 0
    assert set(body["results"][0]) == set(golden["results"][0])


def test_search_400_when_page_zero(http):
    golden = load_golden("search_invalid_page")

    response = http.get("/api/v1/search/posts/?page=0")

    assert response.status_code == 400
    assert response.json()["error"] == golden["error"]
    assert set(response.json()["details"]) == set(golden["details"])


def test_autocomplete_matches_golden_keys(http):
    response = http.get("/api/v1/search/autocomplete/?query=Re&language=all&limit=10")

    assert response.status_code == 200
    assert set(response.json()) == set(load_golden("autocomplete"))


def test_autocomplete_400_when_query_missing(http):
    golden = load_golden("autocomplete_missing")

    response = http.get("/api/v1/search/autocomplete/")

    assert response.status_code == 400
    assert set(response.json()["details"]) == set(golden["details"])


def test_popular_matches_golden_keys(http):
    response = http.get("/api/v1/search/popular/")

    assert response.status_code == 200
    popular = response.json()["popular_searches"]
    assert all(set(item) == {"query", "count"} for item in popular)


def test_categories_match_golden(http):
    response = http.get("/api/v1/search/categories/")

    assert response.status_code == 200
    assert response.json() == load_golden("categories")


def test_sync_status_matches_golden_keys(http):
    response = http.get("/api/v1/search/sync/status/")

    assert response.status_code == 200
    assert set(response.json()) == set(load_golden("sync_status"))


def test_internal_index_403_without_key(http):
    index = http.post("/api/internal/search/index/", json={"post_id": _TEST_POST_ID})
    delete = http.delete(f"/api/internal/search/index/{_TEST_POST_ID}/")

    assert (index.status_code, index.json()) == (403, load_golden("internal_index_nokey"))
    assert (delete.status_code, delete.json()) == (403, load_golden("internal_delete_nokey"))


def test_internal_index_then_delete_with_key(http):
    if not INTERNAL_KEY:
        pytest.skip("E2E_INTERNAL_KEY 가 없다")
    headers = {"X-Internal-Key": INTERNAL_KEY}

    index = http.post(
        "/api/internal/search/index/",
        json={"post_id": _TEST_POST_ID, "title": "E2E 테스트 문서", "content": {"type": "doc", "content": []}},
        headers=headers,
    )
    delete = http.delete(f"/api/internal/search/index/{_TEST_POST_ID}/", headers=headers)

    assert (index.status_code, index.json()) == (200, {"status": "indexed", "post_id": _TEST_POST_ID})
    assert (delete.status_code, delete.json()) == (200, {"status": "deleted", "post_id": _TEST_POST_ID})


def test_kis_snapshot_matches_golden_keys(http):
    golden = load_golden("kis_snapshot")

    response = http.get("/api/kis/snapshot/005930/")

    assert response.status_code == 200
    assert set(response.json()["data"]) == set(golden["data"])


def test_kis_snapshot_400_when_symbol_invalid(http):
    response = http.get("/api/kis/snapshot/12345/")

    assert (response.status_code, response.json()) == (400, load_golden("kis_snapshot_invalid"))


def test_kis_stream_sends_first_event_promptly(http):
    # 운영 Django 는 25초 동안 아무 이벤트도 보내지 않았다 (golden/kis_stream.txt). KIS 미설정이면 첫 이벤트가 ping 이다.
    timeout = httpx2.Timeout(_TIMEOUT, read=_STREAM_FIRST_EVENT_TIMEOUT)
    with http.stream("GET", "/api/kis/stream/005930/", timeout=timeout) as response:
        assert response.status_code == 200
        assert response.headers["content-type"].startswith("text/event-stream")
        first_event = next(line for line in response.iter_lines() if line.startswith("event:"))

    assert first_event in ("event: connected", "event: ping")
