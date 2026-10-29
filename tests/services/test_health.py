"""HealthService 테스트."""

from app.services.health import CHECK_TIMEOUT, HealthService


def test_check_health_degraded_when_mongo_down():
    health_service = HealthService(is_es_reachable=lambda _: True, is_mongo_reachable=lambda _: False)

    health_status = health_service.check_health()

    assert health_status.status == "degraded"
    assert health_status.components["elasticsearch"].model_dump() == {"status": "up", "connected": True}
    assert health_status.components["mongodb"].model_dump() == {"status": "down", "connected": False}
    assert health_status.cached is False


def test_check_health_cached_on_second_call():
    timeouts = []

    def is_es_reachable(timeout: float) -> bool:
        timeouts.append(timeout)
        return True

    health_service = HealthService(is_es_reachable=is_es_reachable, is_mongo_reachable=lambda _: True)

    first = health_service.check_health()
    second = health_service.check_health()

    assert first.status == "healthy" and first.cached is False
    assert second.cached is True
    assert second.last_check == first.last_check
    assert timeouts == [CHECK_TIMEOUT]
