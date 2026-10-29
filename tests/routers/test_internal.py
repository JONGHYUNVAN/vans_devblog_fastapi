"""내부 색인 라우터 테스트. 인증(403 golden), 경로 별칭, 404 형식을 확인한다."""

import pytest

from app.core.config import Settings, get_settings
from app.dependencies import get_indexing_service
from app.main import app
from app.models.post import Post
from app.services.indexing import IndexingService
from tests.conftest import load_golden
from tests.fakes import FakePostIndexRepository

KEY = {"X-Internal-Key": "secret"}


@pytest.fixture
def index() -> FakePostIndexRepository:
    index = FakePostIndexRepository(docs={"p1": Post(post_id="p1")})
    app.dependency_overrides[get_settings] = lambda: Settings(internal_api_key="secret")
    app.dependency_overrides[get_indexing_service] = lambda: IndexingService(index)
    return index


def test_index_post_403_when_key_missing(client, index):
    response = client.post("/api/internal/search/index/", json={"post_id": "p2"})

    assert response.status_code == 403
    assert response.json() == load_golden("internal_index_nokey")
    assert "p2" not in index.docs


def test_remove_post_403_when_key_wrong(client, index):
    response = client.delete("/api/internal/search/index/p1/", headers={"X-Internal-Key": "wrong"})

    assert response.status_code == 403
    assert response.json() == load_golden("internal_delete_nokey")


def test_index_post_403_when_server_key_not_configured(client, index):
    app.dependency_overrides[get_settings] = lambda: Settings(internal_api_key="")

    response = client.post("/api/internal/search/index/", json={"post_id": "p2"}, headers={"X-Internal-Key": ""})

    assert response.status_code == 403


@pytest.mark.parametrize("path", ["/api/internal/search/index/", "/api/v1/search/internal/index/"])
def test_index_post_indexes_on_both_paths(client, index, path):
    response = client.post(path, json={"post_id": "p2", "title": "제목"}, headers=KEY)

    assert response.status_code == 200
    assert response.json() == {"status": "indexed", "post_id": "p2"}
    assert index.docs["p2"].title == "제목"


def test_index_post_400_when_post_id_missing(client, index):
    response = client.post("/api/internal/search/index/", json={"title": "제목"}, headers=KEY)

    assert response.status_code == 400
    assert set(response.json()["details"]) == {"post_id"}


def test_remove_post_deletes(client, index):
    response = client.delete("/api/v1/search/internal/index/p1/", headers=KEY)

    assert response.status_code == 200
    assert response.json() == {"status": "deleted", "post_id": "p1"}


def test_remove_post_404_when_not_indexed(client, index):
    response = client.delete("/api/internal/search/index/missing/", headers=KEY)

    assert response.status_code == 404
    assert response.json()["error"] == "Not found"
