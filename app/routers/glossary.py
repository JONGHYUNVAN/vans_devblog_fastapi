"""용어 사전 공개 읽기 API (설계 08 문서 2.6절). vans_story(BFF·서버 컴포넌트)가 키 없이 읽는다. 쓰기는 CLI(agents glossary) 뿐이다."""

from typing import Annotated

from fastapi import APIRouter, Depends, Query

from app.dependencies import get_glossary_view_service
from app.schemas.glossary import GlossaryTermList, HeadwordPublic, PostGlossaryPublic
from app.services.glossary_views import GlossaryViewService

router = APIRouter(prefix="/api/v1/glossary", tags=["Glossary"])

ViewServiceDep = Annotated[GlossaryViewService, Depends(get_glossary_view_service)]


@router.get("/posts/{post_id}/", summary="글의 용어")
async def get_post_glossary(post_id: str, views: ViewServiceDep) -> PostGlossaryPublic:
    """그 글의 승인된 항목. 항목마다 이 글의 뜻과 같은 표제어의 다른 승인된 뜻을 함께 준다. 추출 기록이 없는 글(사람 글)은 404."""
    return await views.get_post_glossary(post_id)


@router.get("/terms/", summary="전역 사전")
async def list_terms(
    views: ViewServiceDep,
    q: Annotated[str | None, Query(max_length=100)] = None,
    page: Annotated[int, Query(ge=1)] = 1,
    size: Annotated[int, Query(ge=1, le=200)] = 50,
) -> GlossaryTermList:
    """승인된 표제어와 뜻, 뜻마다 글 목록. q 는 표기·별칭의 정규화 키 부분 일치."""
    return await views.list_terms(q=q, page=page, size=size)


@router.get("/headwords/{headword_key}/", summary="표제어 하나")
async def get_headword(headword_key: str, views: ViewServiceDep) -> HeadwordPublic:
    """표제어와 승인된 뜻 전부. 키 기반 툴팁이 글 페이지 밖에서 쓰는 조회 경로다."""
    return await views.get_headword(headword_key)
