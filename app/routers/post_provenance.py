"""제작 기록 공개 API (설계 08 문서 3.5절, D-67).

/api/v1/agents/ 라우터(routers/agent_rounds.py)는 라우터 수준에서 X-Internal-Key 를 요구하므로, 같은 접두의 공개 엔드포인트는
여기 따로 둔다. 공개 범위는 ProvenancePublic 이 정한다(built_by 와 호출 오류 문자열 제외, Q-24). vans_story 의 BFF
(src/app/api/agents/posts/[id]/provenance)가 5분 캐시로 읽는다. 쓰기는 없다: 기록은 발행 따라 쓰기와 CLI 가 만든다.
"""

from typing import Annotated

from fastapi import APIRouter, Depends

from app.dependencies import get_provenance_service
from app.schemas.provenance import ProvenancePublic
from app.services.provenance import ProvenanceService

# 의존성(verify_agents_key)을 일부러 달지 않는다. 같은 태그 "Agents" 로 묶여 /docs 에서는 한 묶음으로 보인다
router = APIRouter(prefix="/api/v1/agents/posts", tags=["Agents"])


@router.get("/{post_id}/provenance/", summary="글의 제작 기록")
async def get_provenance(
    post_id: str, service: Annotated[ProvenanceService, Depends(get_provenance_service)]
) -> ProvenancePublic:
    """어느 단계에서 어느 벤더의 어느 모델이 무엇을 했는지. 기록이 없는 글(사람 글, 아직 안 만들어짐)은 404."""
    # 404 는 "아직" 일 수 있다. 배포 뒤 첫 정리 스캔이 기존 발행 글의 기록을 채우기 전까지 프런트는 블록을 숨긴다
    return await service.get_public(post_id)
