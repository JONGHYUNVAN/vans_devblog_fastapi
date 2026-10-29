"""헬스체크 라우터 테스트. 응답 계약(golden)과 500 unhealthy 형식을 확인한다."""

from app.dependencies import get_health_service
from app.main import app
from app.services.health import HealthService
from tests.conftest import load_golden


class FakeHealthService:
    def check_health(self):
        raise RuntimeError("boom")


def test_health_response_keys_match_golden(client):
    app.dependency_overrides[get_health_service] = lambda: HealthService(
        is_es_reachable=lambda _: True, is_mongo_reachable=lambda _: True
    )
    golden = load_golden("health")

    response = client.get("/api/v1/search/health/")

    assert response.status_code == 200
    body = response.json()
    assert set(body) == set(golden)
    assert set(body["components"]) == set(golden["components"])
    assert body["components"]["mongodb"] == golden["components"]["mongodb"]


def test_health_returns_500_unhealthy_when_unexpected_error(client):
    app.dependency_overrides[get_health_service] = FakeHealthService

    response = client.get("/api/v1/search/health/")

    assert response.status_code == 500
    body = response.json()
    assert body["status"] == "unhealthy"
    assert body["error"] == "boom"
    assert set(body) == {"status", "service", "version", "error", "cached", "last_check"}
