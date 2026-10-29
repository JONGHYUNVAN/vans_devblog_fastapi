"""헬스체크. ES·MongoDB 연결을 2초 타임아웃으로 확인하고 결과를 30분 캐시한다."""

from collections.abc import Callable
from datetime import datetime
from zoneinfo import ZoneInfo

from app.core.cache import HEALTH_TTL, cache
from app.schemas.health import ComponentStatus, HealthStatus, UnhealthyStatus

SERVICE_NAME = "VansDevBlog Search Service"
VERSION = "1.0.0"  # Django 서비스 응답과 같은 값을 유지한다 (계약). OpenAPI 의 앱 버전과는 별개다.
CHECK_TIMEOUT = 2
KST = ZoneInfo("Asia/Seoul")

_CACHE_KEY = "health_status"

ReachabilityCheck = Callable[[float], bool]


class HealthService:
    """ES·MongoDB 연결 상태 확인.

    연결 확인 함수를 생성자로 받는다. 헬스체크는 연결 자체를 확인하는 기능이라 clients 의 확인 함수를 쓰고,
    서비스가 elasticsearch·pymongo 를 직접 import 하지 않도록 dependencies.py 에서 묶어 넘긴다.
    """

    def __init__(self, is_es_reachable: ReachabilityCheck, is_mongo_reachable: ReachabilityCheck) -> None:
        self._is_es_reachable = is_es_reachable
        self._is_mongo_reachable = is_mongo_reachable

    def check_health(self) -> HealthStatus:
        """연결 상태. 30분 안에 확인한 결과가 있으면 다시 확인하지 않고 cached=True 로 돌려준다."""
        cached: HealthStatus | None = cache.get(_CACHE_KEY)
        if cached is not None:
            return cached.model_copy(update={"cached": True})

        health_status = self._check()
        cache.set(_CACHE_KEY, health_status, HEALTH_TTL)
        return health_status

    def _check(self) -> HealthStatus:
        is_es_up = self._is_es_reachable(CHECK_TIMEOUT)
        is_mongo_up = self._is_mongo_reachable(CHECK_TIMEOUT)
        return HealthStatus(
            status="healthy" if is_es_up and is_mongo_up else "degraded",
            service=SERVICE_NAME,
            version=VERSION,
            components={"elasticsearch": _component(is_es_up), "mongodb": _component(is_mongo_up)},
            cached=False,
            last_check=_now_kst(),
        )


def build_unhealthy_status(exc: Exception) -> UnhealthyStatus:
    """상태 확인 자체가 실패했을 때의 응답 본문. error 에 예외 메시지를 넣는 것은 Django 응답과 같다 (계약)."""
    return UnhealthyStatus(service=SERVICE_NAME, version=VERSION, error=str(exc), last_check=_now_kst())


def _component(is_up: bool) -> ComponentStatus:
    return ComponentStatus(status="up" if is_up else "down", connected=is_up)


def _now_kst() -> str:
    return datetime.now(KST).strftime("%Y-%m-%d %H:%M:%S")
