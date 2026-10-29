"""헬스체크 엔드포인트. CloudType 헬스체크가 호출한다."""

import logging
from typing import Annotated

from fastapi import APIRouter, Depends
from fastapi.responses import JSONResponse

from app.dependencies import get_health_service
from app.schemas.health import HealthStatus, UnhealthyStatus
from app.services.health import HealthService, build_unhealthy_status

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1/search", tags=["Health Check"])


@router.get(
    "/health/",
    summary="서비스 상태 확인",
    response_model=HealthStatus,
    responses={500: {"model": UnhealthyStatus}},
)
def check_health(health_service: Annotated[HealthService, Depends(get_health_service)]) -> HealthStatus | JSONResponse:
    """Elasticsearch·MongoDB 연결 상태. 결과는 30분 캐시한다.

    일부 구성요소가 내려가도 200 + `degraded`. 상태 확인 자체가 실패하면 500 + `unhealthy`.
    """
    # 계약: 예상하지 못한 오류도 전역 핸들러 형식이 아니라 Django 서비스와 같은 unhealthy 본문으로 응답한다.
    try:
        return health_service.check_health()
    except Exception as exc:
        logger.exception("헬스체크 에러: %s", exc)
        return JSONResponse(build_unhealthy_status(exc).model_dump(), status_code=500)
