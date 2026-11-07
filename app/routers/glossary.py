"""용어 사전 공개 읽기 API (설계 08 문서 2.6절, D-66).

vans_story 의 BFF(src/app/api/glossary/*)와 서버 컴포넌트가 키 없이 읽고 5분 캐시한다. 공개인 이유는 블로그 독자가 보는 값이라
숨길 것이 없고, 승인된 것만 나가도록 GlossaryViewService 가 이미 걸러 주기 때문이다. 쓰기는 CLI(agents glossary) 뿐이고
HTTP 쓰기 엔드포인트는 일부러 두지 않았다(관리 API 는 08 문서 5절 G-9, 뒤). 응답 모양은 schemas/glossary.py, 골든은 tests/golden/glossary_*.json.
"""

from typing import Annotated

from fastapi import APIRouter, Depends, Query

from app.dependencies import get_glossary_view_service
from app.schemas.glossary import GlossaryTermList, HeadwordPublic, PostGlossaryPublic
from app.services.glossary_views import GlossaryViewService

# 경로 끝 슬래시는 이 앱의 다른 공개 API(검색·코드 그래프)와 같은 계약이다. vans_story BFF 가 그대로 잇는다
router = APIRouter(prefix="/api/v1/glossary", tags=["Glossary"])

ViewServiceDep = Annotated[GlossaryViewService, Depends(get_glossary_view_service)]


@router.get("/posts/{post_id}/", summary="글의 용어")
async def get_post_glossary(post_id: str, views: ViewServiceDep) -> PostGlossaryPublic:
    """그 글의 승인된 항목. 항목마다 이 글의 뜻과 같은 표제어의 다른 승인된 뜻을 함께 준다. 추출 기록이 없는 글(사람 글)은 404."""
    # post_id 는 Post BE 게시글 id 다(글 draft id 가 아니다). 글 페이지가 이미 가진 값이라 추가 조회가 없다
    return await views.get_post_glossary(post_id)


@router.get("/terms/", summary="전역 사전")
async def list_terms(
    views: ViewServiceDep,
    q: Annotated[str | None, Query(max_length=100)] = None,
    page: Annotated[int, Query(ge=1)] = 1,
    size: Annotated[int, Query(ge=1, le=200)] = 50,
) -> GlossaryTermList:
    """승인된 표제어와 뜻, 뜻마다 글 목록. q 는 표기·별칭의 정규화 키 부분 일치. 정렬은 term_ko 순이고 페이지는 1부터다."""
    # size 상한 200 은 전역 /glossary 페이지(G-9)가 한 번에 받을 분량이다. 사전이 작아 서버는 메모리에서 자른다
    return await views.list_terms(q=q, page=page, size=size)


@router.get("/headwords/{headword_key}/", summary="표제어 하나")
async def get_headword(headword_key: str, views: ViewServiceDep) -> HeadwordPublic:
    """표제어와 승인된 뜻 전부. 키 기반 툴팁이 글 페이지 밖에서 쓰는 조회 경로다."""
    return await views.get_headword(headword_key)
