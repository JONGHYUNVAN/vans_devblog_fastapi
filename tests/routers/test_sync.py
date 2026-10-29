"""동기화 라우터 테스트. 상태 응답 계약(golden), 실행 엔드포인트 인증, 실패 시 500 을 확인한다."""

import pytest

from app.core.config import Settings, get_settings
from app.dependencies import get_sync_service
from app.main import app
from app.models.post import Post
from app.services.sync import SyncService
from tests.conftest import load_golden
from tests.fakes import FakePostIndexRepository, FakePostRepository

KEY = {"X-Internal-Key": "secret"}


def _use_service(is_mongo_up: bool = True) -> None:
    service = SyncService(
        post_repository=FakePostRepository([Post(post_id="p1", title="글1")]),
        post_index_repository=FakePostIndexRepository(),
        is_es_reachable=lambda _: True,
        is_mongo_reachable=lambda _: is_mongo_up,
    )
    app.dependency_overrides[get_settings] = lambda: Settings(internal_api_key="secret")
    app.dependency_overrides[get_sync_service] = lambda: service


def test_sync_status_response_keys_match_golden(client):
    golden = load_golden("sync_status")
    _use_service()

    response = client.get("/api/v1/search/sync/status/")

    assert response.status_code == 200
    body = response.json()
    assert set(body) == set(golden)
    assert body["last_sync_time"] is None


@pytest.mark.parametrize("path", ["/api/v1/search/sync/", "/api/v1/search/sync/all/"])
def test_sync_403_when_key_missing(client, path):
    _use_service()

    response = client.post(path)

    assert response.status_code == 403


def test_sync_posts_full_by_default(client):
    _use_service()

    response = client.post("/api/v1/search/sync/", headers=KEY)

    assert response.status_code == 200
    body = response.json()
    assert (body["status"], body["type"], body["synced"], body["ghost_deleted"]) == ("completed", "full", 1, 0)


def test_sync_posts_incremental_omits_ghost_deleted(client):
    _use_service()

    response = client.post("/api/v1/search/sync/", json={"incremental": True}, headers=KEY)

    assert response.status_code == 200
    assert "ghost_deleted" not in response.json()


def test_sync_all_posts_500_with_result_when_failed(client):
    _use_service(is_mongo_up=False)

    response = client.post("/api/v1/search/sync/all/", headers=KEY)

    assert response.status_code == 500
    body = response.json()
    assert (body["status"], body["type"]) == ("failed", "full")
    assert "ghost_deleted" not in body


def test_sync_posts_400_when_incremental_with_force_all(client):
    _use_service()

    response = client.post("/api/v1/search/sync/", json={"incremental": True, "force_all": True}, headers=KEY)

    assert response.status_code == 400
    assert set(response.json()["details"]) == {"non_field_errors"}
