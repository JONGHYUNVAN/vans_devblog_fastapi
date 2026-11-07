"""제작 기록 공개 API (설계 08 문서 3.5절).

/api/v1/agents/ 라우터(routers/agent_rounds.py)는 라우터 수준에서 X-Internal-Key 를 요구하므로, 같은 접두의 공개 엔드포인트는 여기 따로 둔다.
"""

from typing import Annotated

from fastapi import APIRouter, Depends

from app.dependencies import get_provenance_service
from app.schemas.provenance import ProvenancePublic
from app.services.provenance import ProvenanceService

router = APIRouter(prefix="/api/v1/agents/posts", tags=["Agents"])


@router.get("/{post_id}/provenance/", summary="글의 제작 기록")
async def get_provenance(
    post_id: str, service: Annotated[ProvenanceService, Depends(get_provenance_service)]
) -> ProvenancePublic:
    """어느 단계에서 어느 벤더의 어느 모델이 무엇을 했는지. 기록이 없는 글(사람 글, 아직 안 만들어짐)은 404."""
    return await service.get_public(post_id)
