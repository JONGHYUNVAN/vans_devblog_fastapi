"""에이전트 파이프라인 내부 API (설계 06 문서 3절 routers/agent_rounds.py 표). X-Internal-Key 는 AGENTS_INTERNAL_API_KEY 다.

상태를 바꾸는 요청은 DB 만 바꾸고, 진행은 다음 스캔이 이어 간다(최대 SCAN_INTERVAL_S 지연). 상태 전이 위반은 409.
"""

from typing import Annotated

from fastapi import APIRouter, Depends, Query

from app.core.config import Settings, get_settings
from app.core.exceptions import InvalidRequestError, NotFoundError
from app.dependencies import (
    get_agent_call_repository,
    get_draft_decision_service,
    get_draft_repository,
    get_round_service,
    verify_agents_key,
)
from app.models.enums import RoundStatus
from app.repositories.agent_call import AgentCallRepository
from app.repositories.post_draft import PostDraftRepository
from app.schemas.agent_pipeline import (
    CallPublic,
    DraftApproval,
    DraftDiscard,
    DraftHistory,
    DraftPublic,
    DraftRetry,
    EscalationDecision,
    EscalationSummary,
    RoundCreate,
    RoundDetail,
    RoundPublic,
)
from app.services.draft_decision import DraftDecisionService
from app.services.round import RoundService

router = APIRouter(prefix="/api/v1/agents", tags=["Agents"], dependencies=[Depends(verify_agents_key)])

RoundServiceDep = Annotated[RoundService, Depends(get_round_service)]
DecisionServiceDep = Annotated[DraftDecisionService, Depends(get_draft_decision_service)]
DraftRepositoryDep = Annotated[PostDraftRepository, Depends(get_draft_repository)]


@router.post("/rounds/", summary="라운드 생성·시작", status_code=201)
async def create_round(
    body: RoundCreate, round_service: RoundServiceDep, settings: Annotated[Settings, Depends(get_settings)]
) -> RoundPublic:
    """Post BE 활성 카테고리를 읽어 스냅샷을 만들고 라운드를 TOPIC_SELECTION 으로 만든다. 다른 라운드가 주제 선정 중이면 409."""
    try:
        publishing_round = await round_service.start_round(
            theme_keyword=body.theme_keyword, slot_bindings=body.bindings or settings.agent_slot_bindings
        )
    except ValueError as exc:  # 바인딩·페르소나·렌즈 매트릭스, 발행 설정 없음
        raise InvalidRequestError(str(exc)) from exc
    return RoundPublic.from_round(publishing_round)


@router.get("/rounds/", summary="라운드 목록")
async def list_rounds(round_service: RoundServiceDep, status: RoundStatus | None = None) -> list[RoundPublic]:
    return [RoundPublic.from_round(each) for each in await round_service.list_rounds(status)]


@router.get("/rounds/{round_id}/", summary="라운드 상세")
async def get_round(round_id: str, round_service: RoundServiceDep, drafts: DraftRepositoryDep) -> RoundDetail:
    """라운드와 글 3편 요약."""
    publishing_round = await round_service.get(round_id)
    if publishing_round is None:
        raise NotFoundError(f"round {round_id} does not exist")
    return RoundDetail.from_round_and_drafts(publishing_round, await drafts.list_by_round(round_id))


@router.post("/rounds/{round_id}/abort/", summary="라운드 중단")
async def abort_round(round_id: str, round_service: RoundServiceDep) -> RoundPublic:
    """라운드를 ABORTED 로 쓰고 PUBLISHED 외 글을 REJECTED, 레지스트리를 RELEASED 로 정리한다. 종결 라운드면 409."""
    if await round_service.get(round_id) is None:
        raise NotFoundError(f"round {round_id} does not exist")
    await round_service.abort_round(round_id)
    publishing_round = await round_service.get(round_id)
    assert publishing_round is not None
    return RoundPublic.from_round(publishing_round)


@router.get("/drafts/{draft_id}/", summary="글 전체 이력")
async def get_draft(draft_id: str, drafts: DraftRepositoryDep) -> DraftHistory:
    return DraftHistory.model_validate(await _draft(drafts, draft_id))


@router.get("/drafts/{draft_id}/escalation/", summary="사람용 요약")
async def get_escalation(draft_id: str, drafts: DraftRepositoryDep) -> EscalationSummary:
    """최종 본문과 직전 버전과의 diff, 미해소 차단 항목, 기각 항목과 사유."""
    return EscalationSummary.from_draft(await _draft(drafts, draft_id))


@router.post("/drafts/{draft_id}/escalation/", summary="격리 해소")
async def resolve_escalation(draft_id: str, body: EscalationDecision, decisions: DecisionServiceDep) -> DraftPublic:
    """PUBLISH_AS_IS·REVISE·REJECT·CONFIRM_PUBLISHED. CONFIRM_PUBLISHED 는 PUBLISH_UNCERTAIN 에서만."""
    draft = await decisions.resolve_escalation(
        draft_id,
        body.decision,
        decided_by=body.decided_by,
        instruction=body.instruction,
        external_post_id=body.external_post_id,
    )
    return DraftPublic.from_draft(draft)


@router.post("/drafts/{draft_id}/approve/", summary="발행 승인")
async def approve_draft(draft_id: str, body: DraftApproval, decisions: DecisionServiceDep) -> DraftPublic:
    """승인 토글이 켜진 경우 READY_TO_PUBLISH 글의 발행을 승인한다. 승인은 그 버전에 대한 것이다."""
    return DraftPublic.from_draft(await decisions.approve(draft_id, approved_by=body.approved_by))


@router.post("/drafts/{draft_id}/retry/", summary="FAILED 재개")
async def retry_draft(draft_id: str, body: DraftRetry, decisions: DecisionServiceDep) -> DraftPublic:
    """실패한 단계부터 다시 돈다. token_limit 은 어느 사유든 받을 수 있고 현재 사용량보다 커야 한다(아니면 400)."""
    return DraftPublic.from_draft(await decisions.retry(draft_id, token_limit=body.token_limit))


@router.post("/drafts/{draft_id}/discard/", summary="FAILED 글 폐기")
async def discard_draft(draft_id: str, body: DraftDiscard, decisions: DecisionServiceDep) -> DraftPublic:
    return DraftPublic.from_draft(await decisions.discard(draft_id, decided_by=body.decided_by))


@router.get("/calls/", summary="호출 영수증")
async def list_calls(
    calls: Annotated[AgentCallRepository, Depends(get_agent_call_repository)],
    draft_id: Annotated[str | None, Query()] = None,
    round_id: Annotated[str | None, Query()] = None,
) -> list[CallPublic]:
    """draft_id 또는 round_id 로 거른 영수증(토큰·지연). 둘 다 없으면 400."""
    if draft_id:
        rows = await calls.list_by_draft(draft_id)
    elif round_id:
        rows = await calls.list_by_round(round_id)
    else:
        raise InvalidRequestError("draft_id or round_id is required")
    return [CallPublic.from_call(call) for call in rows]


async def _draft(drafts: PostDraftRepository, draft_id: str):
    draft = await drafts.get(draft_id)
    if draft is None:
        raise NotFoundError(f"draft {draft_id} does not exist")
    return draft
