"""app/main.py 테스트."""

import pytest
from fastapi.testclient import TestClient

from app.main import app

# 외부 소비자가 부르는 경로 (README "API 계약"). trailing slash 까지 계약이다.
CONTRACT_ROUTES = [
    ("GET", "/api/v1/search/health/"),
    ("GET", "/api/v1/search/posts/"),
    ("GET", "/api/v1/search/autocomplete/"),
    ("GET", "/api/v1/search/popular/"),
    ("GET", "/api/v1/search/categories/"),
    ("GET", "/api/v1/search/sync/status/"),
    ("POST", "/api/v1/search/sync/"),
    ("POST", "/api/v1/search/sync/all/"),
    ("POST", "/api/internal/search/index/"),
    ("DELETE", "/api/internal/search/index/x/"),
    ("POST", "/api/v1/search/internal/index/"),
    ("DELETE", "/api/v1/search/internal/index/x/"),
    ("GET", "/api/kis/snapshot/x/"),
    ("GET", "/api/kis/stream/x/"),
]


def test_root_redirects_to_docs(client):
    response = client.get("/", follow_redirects=False)
    assert response.status_code in (302, 307)
    assert response.headers["location"] == "/docs"


@pytest.mark.parametrize(("method", "path"), CONTRACT_ROUTES)
def test_contract_route_exists_without_redirect(method, path):
    # lifespan 없이 부르므로 연결이 필요한 경로는 500 이 될 수 있다. 경로가 있는지(404·405 아님)와 슬래시 리다이렉트(307)가 없는지만 본다.
    client = TestClient(app, raise_server_exceptions=False)

    response = client.request(method, path, follow_redirects=False)

    assert response.status_code not in (404, 405, 307)
