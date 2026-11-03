"""코드 그래프 엔드포인트. 블로그 프로젝트 페이지(뷰어)가 조회하고, 운영자가 분석을 실행한다 (code_graph_design/README.md)."""

from typing import Annotated

from fastapi import APIRouter, Depends, Response

from app.dependencies import get_code_graph_service, get_service_graph_service, verify_internal_key
from app.schemas.code_graph import CodeGraphAnalysis, CodeGraphPublic, CodeGraphSummary, FlowPublic, ServiceGraphPublic
from app.services.code_graph import CodeGraphService
from app.services.service_graph import ServiceGraphService

router = APIRouter(prefix="/api/v1/code-graph", tags=["Code Graph"])


@router.get("/", summary="저장소별 최신 분석")
def list_code_graphs(
    code_graph_service: Annotated[CodeGraphService, Depends(get_code_graph_service)],
) -> list[CodeGraphSummary]:
    """분석 결과가 있는 저장소마다 최신 commit·분석 시각·진단 건수."""
    return code_graph_service.list_code_graphs()


# services/·flows/ 는 /{repo}/ 보다 먼저 등록해야 저장소 이름으로 잡히지 않는다
@router.get("/services/", summary="서비스 그래프")
def get_service_graph(
    service_graph_service: Annotated[ServiceGraphService, Depends(get_service_graph_service)],
) -> ServiceGraphPublic:
    """저장된 호출 지점과 각 백엔드 OpenAPI 를 맞춘 서비스 간 호출 그래프. 10분 캐시한다.

    OpenAPI 를 받지 못한 서비스는 `openapi` 가 unavailable 이고 그 서비스로 가는 엣지는 unknown 이다.
    """
    return service_graph_service.get_service_graph()


@router.get("/flows/{flow}/", summary="e2e 흐름")
def get_flow(
    flow: str,
    service_graph_service: Annotated[ServiceGraphService, Depends(get_service_graph_service)],
) -> FlowPublic:
    """search·publish·posts·auth 흐름을 hop 마다 요청·응답 스키마 이름, 호출 코드 위치와 mermaid 로. 없는 흐름은 404."""
    return service_graph_service.get_flow(flow)


@router.post(
    "/{repo}/analyze/",
    summary="코드 그래프 분석",
    dependencies=[Depends(verify_internal_key)],
    responses={202: {"description": "백그라운드 분석을 시작했다", "model": CodeGraphAnalysis}},
)
def analyze_repo(
    repo: str,
    response: Response,
    code_graph_service: Annotated[CodeGraphService, Depends(get_code_graph_service)],
) -> CodeGraphAnalysis:
    """저장소의 GitHub tarball 을 받아 commit 을 확인한다. `X-Internal-Key` 필요.

    같은 commit 의 결과가 있으면 200 `stored`, 없으면 202 `queued` 이고 분석이 끝나면 조회에 나온다.
    분석 실패는 응답에 드러나지 않고 이전 결과가 남는다. 설정에 없는 저장소는 404, GitHub 실패는 503 이다.
    """
    result = code_graph_service.analyze_repo(repo)
    if result.status == "queued":
        response.status_code = 202
    return result


@router.get("/{repo}/", summary="코드 그래프")
def get_code_graph(
    repo: str,
    code_graph_service: Annotated[CodeGraphService, Depends(get_code_graph_service)],
) -> CodeGraphPublic:
    """최신 분석의 파일 그래프(노드·엣지)와 진단(레이어 위반·순환·orphan·hub). 10분 캐시한다.

    설정에 없거나 아직 분석하지 않은 저장소는 404 다.
    """
    return code_graph_service.get_code_graph(repo)


@router.get(
    "/{repo}/context/",
    summary="코드 그래프 context 마크다운",
    response_class=Response,
    responses={200: {"content": {"text/markdown": {}}}},
)
def get_code_graph_context(
    repo: str,
    code_graph_service: Annotated[CodeGraphService, Depends(get_code_graph_service)],
) -> Response:
    """최신 분석을 레이어·위반·순환·orphan·hub 요약 마크다운으로. CLAUDE.md 갱신·프롬프트 주입용이다.

    설정에 없거나 아직 분석하지 않은 저장소는 404 다.
    """
    return Response(code_graph_service.get_code_graph_context(repo), media_type="text/markdown")
