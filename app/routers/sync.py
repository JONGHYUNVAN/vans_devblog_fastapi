"""MongoDB → ES 동기화 엔드포인트. 운영자가 수동으로 호출한다."""

from typing import Annotated

from fastapi import APIRouter, Depends, Response

from app.dependencies import get_sync_service, verify_internal_key
from app.schemas.sync import SyncOptions, SyncResult, SyncStatus
from app.services.sync import SyncService

router = APIRouter(prefix="/api/v1/search", tags=["Data Sync"])

# 실행 엔드포인트는 X-Internal-Key 를 요구한다. Django 버전은 인증이 없어 누구나 전체 동기화와
# clear_existing(전체 삭제)을 부를 수 있었다. 호출하는 코드가 없어 계약을 바꿨다 (2026-10-29 결정).
_INTERNAL_ONLY = [Depends(verify_internal_key)]


@router.get("/sync/status/", summary="동기화 상태")
def get_sync_status(sync_service: Annotated[SyncService, Depends(get_sync_service)]) -> SyncStatus:
    """MongoDB·Elasticsearch 연결과 게시글 수, 마지막 동기화 시각. 확인에 실패한 항목은 false·0 이다."""
    return sync_service.get_sync_status()


@router.post("/sync/", summary="동기화 실행", dependencies=_INTERNAL_ONLY, response_model_exclude_none=True)
def sync_posts(
    response: Response,
    sync_service: Annotated[SyncService, Depends(get_sync_service)],
    options: SyncOptions | None = None,
) -> SyncResult:
    """MongoDB 게시글을 검색 색인으로 동기화한다. `X-Internal-Key` 필요.

    결과가 `failed` 면 같은 본문에 상태 코드 500 이다.
    """
    result = sync_service.sync_posts(options or SyncOptions())
    _fail_with_500(result, response)
    return result


@router.post("/sync/all/", summary="전체 동기화", dependencies=_INTERNAL_ONLY, response_model_exclude_none=True)
def sync_all_posts(
    response: Response,
    sync_service: Annotated[SyncService, Depends(get_sync_service)],
) -> SyncResult:
    """기본 옵션으로 모든 게시글을 동기화한다. `X-Internal-Key` 필요. 결과가 `failed` 면 상태 코드 500 이다."""
    result = sync_service.sync_all_posts()
    _fail_with_500(result, response)
    return result


def _fail_with_500(result: SyncResult, response: Response) -> None:
    # 계약: 실패도 SyncResult 본문이고 상태 코드만 500 이다 (Django 와 같다).
    if result.status == "failed":
        response.status_code = 500
