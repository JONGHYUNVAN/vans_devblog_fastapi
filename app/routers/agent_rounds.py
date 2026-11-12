"""에이전트 파이프라인 내부 API (설계 06 문서 3절 routers/agent_rounds.py 표). X-Internal-Key 는 AGENTS_INTERNAL_API_KEY 다.

상태를 바꾸는 요청은 DB 만 바꾸고, 진행은 다음 스캔이 이어 간다(최대 SCAN_INTERVAL_S 지연). 상태 전이 위반은 409.
읽기는 PipelineViewService, 결정은 DraftDecisionService, 라운드 생성·중단은 RoundService 다.
파이프라인 버전은 넣기만 한다(09 문서 D-89, CLI edit-prompt·edit-param 과 같다). 조회·되돌리기는 두지 않는다(D-77).
"""

from typing import Annotated

from fastapi import APIRouter, Depends, Query

from app.core.exceptions import InvalidRequestError, NotFoundError
from app.dependencies import (
    get_draft_decision_service,
    get_pipeline_version_service,
    get_pipeline_view_service,
    get_round_service,
    verify_agents_key,
)
from app.models.enums import RoundStatus
from app.models.pipeline import COMMON_FAMILY, EXPOSITORY_FAMILY, PROMPT_FAMILIES, PipelineVersion
from app.schemas.agent_pipeline import (
    CallPublic,
    DraftApproval,
    DraftDiscard,
    DraftHistory,
    DraftPublic,
    DraftRetry,
    EscalationDecision,
    EscalationSummary,
    ParameterEdit,
    PipelineVersionCreated,
    PromptEdit,
    RoundCreate,
    RoundDetail,
    RoundPublic,
    RouteEdit,
)
from app.services.draft_decision import DraftDecisionService
from app.services.metrics import PipelineMetrics
from app.services.pipeline_validation import validate_version
from app.services.pipeline_versions import PipelineVersionService
from app.services.pipeline_views import PipelineViewService, build_draft_public, build_round_public
from app.services.round import RoundService

router = APIRouter(prefix="/api/v1/agents", tags=["Agents"], dependencies=[Depends(verify_agents_key)])

RoundServiceDep = Annotated[RoundService, Depends(get_round_service)]
DecisionServiceDep = Annotated[DraftDecisionService, Depends(get_draft_decision_service)]
ViewServiceDep = Annotated[PipelineViewService, Depends(get_pipeline_view_service)]
VersionServiceDep = Annotated[PipelineVersionService, Depends(get_pipeline_version_service)]


@router.post("/rounds/", summary="라운드 생성·시작", status_code=201)
async def create_round(body: RoundCreate, round_service: RoundServiceDep) -> RoundPublic:
    """Post BE 활성 카테고리를 읽어 스냅샷을 만들고 라운드를 TOPIC_SELECTION 으로 만든다. 다른 라운드가 주제 선정 중이면 409."""
    try:
        publishing_round = await round_service.start_round(
            theme_keyword=body.theme_keyword, slot_bindings=body.bindings, evidence=body.evidence
        )
    except ValueError as exc:  # 라우트·페르소나·지원하지 않는 조합, 발행 설정 없음
        raise InvalidRequestError(str(exc)) from exc
    return build_round_public(publishing_round)


@router.get("/rounds/", summary="라운드 목록")
async def list_rounds(views: ViewServiceDep, status: RoundStatus | None = None) -> list[RoundPublic]:
    return await views.list_rounds(status)


@router.get("/rounds/{round_id}/", summary="라운드 상세")
async def get_round(round_id: str, views: ViewServiceDep) -> RoundDetail:
    """라운드와 글 3편 요약."""
    return await views.get_round_detail(round_id)


@router.post("/rounds/{round_id}/abort/", summary="라운드 중단")
async def abort_round(round_id: str, round_service: RoundServiceDep) -> RoundPublic:
    """라운드를 ABORTED 로 쓰고 PUBLISHED 외 글을 REJECTED, 레지스트리를 RELEASED 로 정리한다. 종결 라운드면 409."""
    if await round_service.get(round_id) is None:
        raise NotFoundError(f"round {round_id} does not exist")
    return build_round_public(await round_service.abort_round(round_id))


@router.get("/drafts/{draft_id}/", summary="글 전체 이력")
async def get_draft(draft_id: str, views: ViewServiceDep) -> DraftHistory:
    return await views.get_draft_history(draft_id)


@router.get("/drafts/{draft_id}/escalation/", summary="사람용 요약")
async def get_escalation(draft_id: str, views: ViewServiceDep) -> EscalationSummary:
    """최종 본문과 직전 버전과의 diff, 미해소 차단 항목, 기각 항목과 사유."""
    return await views.get_escalation_summary(draft_id)


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
    return build_draft_public(draft)


@router.post("/drafts/{draft_id}/approve/", summary="발행 승인")
async def approve_draft(draft_id: str, body: DraftApproval, decisions: DecisionServiceDep) -> DraftPublic:
    """승인 토글이 켜진 경우 READY_TO_PUBLISH 글의 발행을 승인한다. 승인은 그 버전에 대한 것이다."""
    return build_draft_public(await decisions.approve(draft_id, approved_by=body.approved_by))


@router.post("/drafts/{draft_id}/retry/", summary="FAILED 재개")
async def retry_draft(draft_id: str, body: DraftRetry, decisions: DecisionServiceDep) -> DraftPublic:
    """실패한 단계부터 다시 돈다. token_limit 은 어느 사유든 받을 수 있고 현재 사용량보다 커야 한다(아니면 400)."""
    return build_draft_public(await decisions.retry(draft_id, token_limit=body.token_limit))


@router.post("/drafts/{draft_id}/discard/", summary="FAILED 글 폐기")
async def discard_draft(draft_id: str, body: DraftDiscard, decisions: DecisionServiceDep) -> DraftPublic:
    return build_draft_public(await decisions.discard(draft_id, decided_by=body.decided_by))


@router.get("/calls/", summary="호출 영수증")
async def list_calls(
    views: ViewServiceDep,
    draft_id: Annotated[str | None, Query()] = None,
    round_id: Annotated[str | None, Query()] = None,
) -> list[CallPublic]:
    """draft_id 또는 round_id 로 거른 영수증(토큰·지연). 둘 다 없으면 400."""
    return await views.list_calls(draft_id=draft_id, round_id=round_id)


@router.get("/metrics/", summary="운영 지표")
async def get_metrics(views: ViewServiceDep, round_id: Annotated[str | None, Query()] = None) -> PipelineMetrics:
    """07 문서 4절 지표. round_id 가 없으면 모든 라운드."""
    return await views.compute_metrics(round_id)


@router.post("/pipeline/prompts/{family}/{name}/", summary="프롬프트 템플릿 하나 바꾸기", status_code=201)
async def edit_prompt(family: str, name: str, body: PromptEdit, versions: VersionServiceDep) -> PipelineVersionCreated:
    """최신 버전을 부모로 템플릿 하나만 바꾼 새 버전. 다음 라운드부터 쓴다. 검증 오류·최신과 같은 내용이면 400.

    최신 버전에 없는 템플릿 이름은 400 이다. 오타가 아무도 읽지 않는 템플릿을 만들고 바꾼 줄 알게 되는 것을 막는다.
    """
    if family not in (*PROMPT_FAMILIES, COMMON_FAMILY, EXPOSITORY_FAMILY):
        raise InvalidRequestError(f"unknown prompt family {family}")
    try:
        latest = await versions.require_latest()
        if name not in latest.prompts.get(family, {}):
            raise InvalidRequestError(f"template {family}/{name} does not exist in {latest.version_id}")
        version = await versions.edit_prompt(family, name, body.text, note=body.note)
    except ValueError as exc:  # 검증 오류, 최신과 같은 내용, 버전 없음
        raise InvalidRequestError(str(exc)) from exc
    return _created(version)


@router.post("/pipeline/parameters/", summary="조절 값 바꾸기", status_code=201)
async def edit_parameters(body: ParameterEdit, versions: VersionServiceDep) -> PipelineVersionCreated:
    """점 경로 → 값으로 조절 값만 바꾼 새 버전. 없는 경로·타입 오류·검증 오류·최신과 같은 내용이면 400."""
    try:
        version = await versions.edit_parameters(body.assignments, note=body.note)
    except ValueError as exc:  # pydantic ValidationError 도 ValueError 다
        raise InvalidRequestError(str(exc)) from exc
    return _created(version)


@router.post("/pipeline/routes/{route_id}/", summary="라우트 하나의 후보 바꾸기", status_code=201)
async def edit_route(route_id: str, body: RouteEdit, versions: VersionServiceDep) -> PipelineVersionCreated:
    """최신 버전을 부모로 라우트 하나의 후보 목록만 바꾼 새 버전. 다음 라운드부터 쓴다.

    최신 버전에 없는 라우트 id, 지원표에 없는 조합, 검증 오류, 최신과 같은 내용이면 400.
    """
    try:
        version = await versions.edit_route(
            route_id, [candidate.model_dump() for candidate in body.candidates], note=body.note
        )
    except ValueError as exc:  # 없는 라우트, 지원하지 않는 조합, 검증 오류, 최신과 같은 내용
        raise InvalidRequestError(str(exc)) from exc
    return _created(version)


def _created(version: PipelineVersion) -> PipelineVersionCreated:
    """create() 는 경고를 로그로만 남긴다. 원격에서 넣는 사람은 로그를 못 보므로 같은 검증을 다시 돌려 경고를 돌려준다."""
    warnings = [str(finding) for finding in validate_version(version.body()) if finding.level == "warning"]
    return PipelineVersionCreated(
        version_id=version.version_id,
        parent_version_id=version.parent_version_id,
        content_hash=version.content_hash,
        source=version.source,
        note=version.note,
        created_at=version.created_at,
        warnings=warnings,
    )
