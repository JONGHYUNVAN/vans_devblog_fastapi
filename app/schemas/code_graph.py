"""코드 그래프 API 응답 (code_graph_design/README.md "API" 절). 블로그 프로젝트 페이지의 뷰어가 읽는다."""

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field


class CodeGraphAnalysis(BaseModel):
    """분석 요청 결과. stored 는 같은 commit 의 저장된 결과가 있다(200), queued 는 백그라운드 분석을 시작했다(202)."""

    repo: str
    commit_sha: str
    status: Literal["stored", "queued"]


class CodeGraphSummary(BaseModel):
    """저장소 하나의 최신 분석과 진단 건수."""

    repo: str
    commit_sha: str
    analyzed_at: datetime
    violation_count: int
    cycle_count: int
    orphan_count: int
    hub_count: int


class GraphLayer(BaseModel):
    id: str
    may_import: list[str] = Field(description="이 레이어가 import 해도 되는 다른 레이어. 같은 레이어 안은 항상 허용")
    file_count: int


class LayerViolation(BaseModel):
    source: str
    target: str
    from_layer: str
    to_layer: str


class HubFile(BaseModel):
    path: str
    in_degree: int
    out_degree: int
    symbol_count: int


class GraphDiagnostics(BaseModel):
    layer_violations: list[LayerViolation]
    cycles: list[list[str]] = Field(description="순환마다 대표 경로 하나 [a, b, ..., a]")
    orphan_files: list[str] = Field(description="아무도 import 하지 않는 파일. 이 진단을 끈 저장소는 빈 목록")
    hub_files: list[HubFile] = Field(description="in_degree 5 이상, 내림차순 최대 15개. 이 진단을 끈 저장소는 빈 목록")


class GraphNode(BaseModel):
    id: str = Field(description="저장소 루트 기준 파일 경로")
    layer: str = Field(description="선언된 레이어 id. 어느 레이어에도 안 속하면 root")
    symbols: list[str] = Field(description="정의 순서의 함수·클래스 등 이름")
    in_degree: int
    out_degree: int


class GraphEdge(BaseModel):
    source: str
    target: str
    status: Literal["valid", "violation"]


class Graph(BaseModel):
    nodes: list[GraphNode]
    edges: list[GraphEdge] = Field(description="저장소 안 파일 사이의 import. 외부 패키지는 없다")


class OutboundCall(BaseModel):
    caller_file: str
    caller_symbol: str
    env_var: str
    http_method: str
    raw_path: str


class CodeGraphPublic(BaseModel):
    """저장소 하나의 최신 코드 그래프와 진단. 영향 반경(ancestors·descendants)은 이 JSON 으로 계산한다."""

    repo: str
    commit_sha: str
    analyzed_at: datetime
    tech_stack: list[str] = Field(description="매니페스트에 선언된 의존성 이름·버전")
    layers: list[GraphLayer]
    diagnostics: GraphDiagnostics
    graph: Graph
    outbound_calls: list[OutboundCall] = Field(description="서비스 간 호출 지점. 아직 비어 있다")
