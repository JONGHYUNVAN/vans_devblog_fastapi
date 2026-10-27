import logging

from fastapi import APIRouter
from fastapi.responses import JSONResponse

from app.services import health_service

logger = logging.getLogger("search")

router = APIRouter(prefix="/api/v1/search", tags=["Health Check"])


@router.get("/health/", summary="서비스 상태 확인")
def health_check():
    """ES·MongoDB 연결 상태. 결과는 30분 캐시한다. 일부 구성요소가 내려가도 200 + degraded."""
    try:
        return health_service.get_cached_health()
    except Exception as e:
        logger.error("헬스체크 에러: %s", e)
        return JSONResponse(health_service.unhealthy(e), status_code=500)
