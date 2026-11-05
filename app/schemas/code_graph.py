"""코드 그래프 API 응답 (code_graph_design/README.md "API" 절). 블로그 프로젝트 페이지의 뷰어가 읽는다."""

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field


class CodeGraphAnalysis(BaseModel):
    """분석 요청 결과. stored 는 저장된 결과가 그대로 유효하다(200), queued 는 백그라운드 분석을 시작했다(202),
    running 은 이 저장소 분석이 이미 돌고 있다(202)."""

    repo: str
    commit_sha: str
    status: Literal["stored", "queued", "running"]


class AnalysisStatusPublic(BaseModel):
    """이 프로세스에서의 마지막 분석. 백그라운드 실패가 여기에 드러난다. error 는 예외 종류 이름뿐이다."""

    repo: str
    state: Literal["idle", "running", "done", "failed"]
    commit_sha: str | None = None
    updated_at: datetime | None = None
    error: str | None = None


class CodeGraphSummary(BaseModel):
    """저장소 하나의 최신 분석과 진단 건수."""

    repo: str
    commit_sha: str
    analyzed_at: datetime
    violation_count: int
    type_only_violation_count: int = Field(
        description="violation_count 중 타입으로만 쓰는 import. 실행 시 의존은 아니다"
    )
    cycle_count: int = Field(description="실행 시 순환 묶음 수. 타입 import 로만 생기는 순환은 세지 않는다")
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
    type_only: bool = Field(description="타입으로만 쓰는 import. 구조 규칙 위반이지만 실행 시 의존은 아니다")


class HubFile(BaseModel):
    path: str
    in_degree: int
    out_degree: int
    symbol_count: int


class GraphDiagnostics(BaseModel):
    layer_violations: list[LayerViolation]
    cycles: list[list[str]] = Field(description="실행 시 순환마다 대표 경로 하나 [a, b, ..., a]")
    cycle_groups: list[list[str]] = Field(description="cycles 와 같은 순서. 그 순환 묶음의 파일 전부")
    type_only_cycle_groups: list[list[str]] = Field(
        description="타입 import 까지 넣어야 생기는 순환 묶음. 실행에는 없다"
    )
    orphan_files: list[str] = Field(description="아무도 import 하지 않는 파일. 이 진단을 끈 저장소는 빈 목록")
    hub_files: list[HubFile] = Field(description="in_degree 5 이상, 내림차순 최대 15개. 이 진단을 끈 저장소는 빈 목록")
    unassigned_files: list[str] = Field(
        description="어느 레이어에도 안 걸린 하위 폴더 파일. 레이어 규칙 검사에서 빠져 있다"
    )


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
    kind: Literal["static", "dynamic", "type"] = Field(
        description="static 모듈을 읽을 때 실행, dynamic `import()` 로 지연 실행, type 타입으로만 쓰여 실행 때 사라진다"
    )


class Graph(BaseModel):
    nodes: list[GraphNode]
    edges: list[GraphEdge] = Field(description="저장소 안 파일 사이의 import. 외부 패키지는 없다")


class OutboundCall(BaseModel):
    caller_file: str
    caller_symbol: str = Field(description="감싼 함수. 메서드는 `클래스.메서드`, 모듈 수준이면 빈 문자열")
    env_var: str = Field(description="기준 URL 을 담은 환경변수. 주소가 코드에 박힌 호출은 빈 문자열")
    http_method: str
    raw_path: str = Field(description="기준 URL 뒤의 원문. `${...}`·쿼리를 그대로 둔다")
    external_host: str | None = Field(description="주소가 코드에 박힌 호출의 호스트. 코드에서 못 정하면 unresolved")


class InboundRoute(BaseModel):
    file: str
    http_method: str
    path: str = Field(
        description="소스에서 읽은 경로(Next.js route.ts, NestJS 데코레이터, Spring 어노테이션). 변수는 `{id}`"
    )


class ImpactPublic(BaseModel):
    """파일 하나의 영향 반경. upstream 은 이 파일을 쓰는 쪽(바꾸면 깨지는 곳), downstream 은 이 파일이 쓰는 쪽이다."""

    file: str
    hops: int
    upstream: list[str]
    downstream: list[str]
    edges: list[GraphEdge]


class CodeGraphPublic(BaseModel):
    """저장소 하나의 최신 코드 그래프와 진단. 영향 반경(ancestors·descendants)은 이 JSON 으로 계산한다."""

    repo: str
    commit_sha: str
    analyzed_at: datetime
    tech_stack: list[str] = Field(description="매니페스트에 선언된 의존성 이름·버전")
    layers: list[GraphLayer]
    diagnostics: GraphDiagnostics
    graph: Graph
    outbound_calls: list[OutboundCall] = Field(description="서비스 간 호출 지점(설정의 call_sites 파일에서만)")
    inbound_routes: list[InboundRoute] = Field(
        description="소스에서 읽은 엔드포인트. route_root·route_framework 가 없는 저장소는 빈 목록"
    )


class ServiceNode(BaseModel):
    id: str = Field(
        description="endpoint 는 `서비스 메서드 경로`, code 는 `서비스:파일#심볼`, external 은 `env:환경변수` 또는 `host:호스트`"
    )
    kind: Literal["endpoint", "code", "external"]
    service: str | None
    repo: str | None
    http_method: str | None
    path: str | None = Field(description="OpenAPI 경로 템플릿. 매칭되지 않은 호출 대상은 비교 키(`{}`)")
    file: str | None
    symbol: str | None
    request_schema: str | None = Field(description="requestBody JSON 스키마 이름")
    response_schema: str | None = Field(description="200·201·202 응답 JSON 스키마 이름. 배열은 `이름[]`")


class ServiceEdge(BaseModel):
    source: str
    target: str
    caller_repo: str
    caller_file: str
    caller_symbol: str
    env_var: str
    http_method: str
    path: str
    status: Literal["matched", "missing", "unknown", "external"] = Field(
        description="matched 대상 OpenAPI(또는 route.ts)에 있다, missing 없다, unknown 대상 문서를 못 받았다, "
        "external 네 저장소 밖"
    )


class ServiceGraphPublic(BaseModel):
    """서비스 간 호출 그래프. 노드는 엔드포인트·호출 코드·외부 서비스, 엣지는 호출이다."""

    nodes: list[ServiceNode]
    edges: list[ServiceEdge]
    openapi: dict[str, Literal["ok", "unavailable", "none"]] = Field(
        description="서비스별 OpenAPI 문서 상태. unavailable 이면 그 서비스의 스키마·매칭이 빈다"
    )
    repos: dict[str, str] = Field(description="서비스 → 저장소")


class FlowHop(BaseModel):
    caller: str = Field(description="서비스 이름. 흐름의 시작은 browser")
    callee: str
    http_method: str
    path: str
    request_schema: str | None
    response_schema: str | None
    call_sites: list[str] = Field(description="이 호출을 하는 코드 `저장소:파일#심볼`")
    status: Literal["matched", "missing", "unknown"] = Field(
        description="matched 대상에 엔드포인트가 있다, missing 없다, unknown 대상 엔드포인트 목록을 모른다"
    )
    verified: bool = Field(description="호출 코드를 찾았고 대상 엔드포인트도 있다. 흐름 표가 코드와 맞는다는 뜻이다")


class FlowPublic(BaseModel):
    """고정된 e2e 흐름 하나를 hop 마다 따라간 결과와 mermaid sequenceDiagram 텍스트."""

    flow: str
    title: str
    hops: list[FlowHop]
    mermaid: str
