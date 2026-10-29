"""헬스체크 응답. 키와 값은 Django 서비스 응답(tests/golden/health.json)과 같다."""

from typing import Literal

from pydantic import BaseModel


class ComponentStatus(BaseModel):
    status: Literal["up", "down"]
    connected: bool


class HealthStatus(BaseModel):
    """구성요소 일부가 내려가도 200 으로 보내는 응답."""

    status: Literal["healthy", "degraded"]
    service: str
    version: str
    components: dict[str, ComponentStatus]
    cached: bool
    last_check: str


class UnhealthyStatus(BaseModel):
    """상태 확인 자체가 실패했을 때 500 으로 보내는 응답."""

    status: Literal["unhealthy"] = "unhealthy"
    service: str
    version: str
    error: str
    cached: bool = False
    last_check: str
