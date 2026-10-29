"""내부 색인 엔드포인트. Post BE(NestJS)가 X-Internal-Key 를 붙여 fire-and-forget 으로 호출한다."""

from typing import Annotated

from fastapi import APIRouter, Depends

from app.dependencies import get_indexing_service, verify_internal_key
from app.schemas.indexing import IndexingResult, SearchIndexPayload
from app.services.indexing import IndexingService

router = APIRouter(tags=["Internal"], dependencies=[Depends(verify_internal_key)])

# 경로 두 벌은 계약이다. NestJS 는 /api/internal/search/index/ 를 부르고,
# /api/v1/search/internal/index/ 는 Django 에 있던 같은 기능의 별칭이라 문서에서만 숨긴다.
# 실패 응답: Django 는 404 를 {"error": "Not found", "post_id"}, 500 을 {"error", "message": 예외 문자열}로 보냈다.
# 지금은 공통 형식 {"error", "message"} 이고 500 에 예외 내용을 넣지 않는다. NestJS 는 응답 본문을 읽지 않는다.


@router.post("/api/internal/search/index/", summary="게시글 색인")
@router.post("/api/v1/search/internal/index/", include_in_schema=False)
def index_post(
    payload: SearchIndexPayload,
    indexing_service: Annotated[IndexingService, Depends(get_indexing_service)],
) -> IndexingResult:
    """게시글을 검색 색인에 넣는다. 이미 있으면 통째로 바꾼다. `X-Internal-Key` 가 틀리면 403."""
    return indexing_service.index_post(payload)


@router.delete("/api/internal/search/index/{post_id}/", summary="게시글 색인 삭제")
@router.delete("/api/v1/search/internal/index/{post_id}/", include_in_schema=False)
def remove_post(
    post_id: str,
    indexing_service: Annotated[IndexingService, Depends(get_indexing_service)],
) -> IndexingResult:
    """게시글을 검색 색인에서 지운다. 색인에 없으면 404. `X-Internal-Key` 가 틀리면 403."""
    return indexing_service.remove_post(post_id)
