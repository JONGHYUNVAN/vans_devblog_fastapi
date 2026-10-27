from app.services import health_service
from tests.conftest import load_golden


def test_health_degraded_when_mongo_down(client, monkeypatch):
    monkeypatch.setattr(health_service, "_es_ok", lambda: True)
    monkeypatch.setattr(health_service, "_mongo_ok", lambda: False)

    res = client.get("/api/v1/search/health/")

    assert res.status_code == 200
    body = res.json()
    assert body["status"] == "degraded"
    assert body["components"]["elasticsearch"] == {"status": "up", "connected": True}
    assert body["components"]["mongodb"] == {"status": "down", "connected": False}
    assert body["cached"] is False


def test_health_is_cached(client, monkeypatch):
    calls = []
    monkeypatch.setattr(health_service, "_es_ok", lambda: calls.append(1) or True)
    monkeypatch.setattr(health_service, "_mongo_ok", lambda: True)

    first = client.get("/api/v1/search/health/").json()
    second = client.get("/api/v1/search/health/").json()

    assert first["status"] == "healthy" and first["cached"] is False
    assert second["cached"] is True
    assert second["last_check"] == first["last_check"]
    assert len(calls) == 1


def test_health_response_keys_match_golden(client, monkeypatch):
    monkeypatch.setattr(health_service, "_es_ok", lambda: True)
    monkeypatch.setattr(health_service, "_mongo_ok", lambda: True)
    golden = load_golden("health")

    body = client.get("/api/v1/search/health/").json()

    assert set(body) == set(golden)
    assert set(body["components"]) == set(golden["components"])


def test_health_returns_500_on_unexpected_error(client, monkeypatch):
    def boom():
        raise RuntimeError("boom")

    monkeypatch.setattr(health_service, "get_cached_health", boom)

    res = client.get("/api/v1/search/health/")

    assert res.status_code == 500
    assert res.json()["status"] == "unhealthy"


def test_root_redirects_to_docs(client):
    res = client.get("/", follow_redirects=False)
    assert res.status_code in (302, 307)
    assert res.headers["location"] == "/docs"
