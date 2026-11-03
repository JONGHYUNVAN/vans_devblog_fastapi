"""코드 그래프 엔티티(code_graphs 컬렉션)와 저장소 설정, I/O 없는 진단 계산·context 마크다운 (code_graph_design/README.md)."""

import re
from collections.abc import Sequence
from datetime import datetime
from typing import Literal, Self

import networkx as nx
from pydantic import BaseModel

ROOT_LAYER = (
    "root"  # 소스 루트 아래지만 어느 레이어에도 안 속한 파일. source 든 target 이든 위반으로 세지 않는다 (G-14)
)
HUB_MIN_IN_DEGREE = 5
HUB_LIMIT = 15
_KEY_SYMBOL_COUNT = 5


class LayerRule(BaseModel):
    """레이어 하나. path 는 저장소 루트 기준 디렉터리 접두, may_import 는 import 해도 되는 다른 레이어 id 다(G-05)."""

    id: str
    path: str
    may_import: list[str] = []


class AllowedEdge(BaseModel):
    """레이어 규칙의 파일 단위 예외. 양 끝은 저장소 루트 기준 경로다."""

    source: str
    target: str


class DiagnosticToggles(BaseModel):
    """orphan·hub 진단 켜기. DI 로 묶는 Spring·NestJS 는 import 그래프가 의존을 반영하지 않아 끈다 (G-06)."""

    orphans: bool = False
    hubs: bool = False


class OpenApiLogin(BaseModel):
    """OpenAPI 문서 앞의 세션 로그인. NestJS 는 swagger-json 도 SwaggerAuthMiddleware 비밀번호 세션 뒤에 있다."""

    path: str  # base_setting 호스트 기준. 본문 {"password": ...} 로 POST 하고 받은 쿠키로 문서를 받는다
    password_setting: str  # Settings 필드 이름. 비어 있으면 문서를 받지 않는다(스키마만 빈다, G-08)


class OpenApiSource(BaseModel):
    """다른 프로세스의 OpenAPI 문서 위치. 호스트는 Settings 필드(base_setting) 값이다(G-13)."""

    base_setting: str
    path: str
    login: OpenApiLogin | None = None


class CodeGraphRepo(BaseModel):
    """CODE_GRAPH_REPOS 의 저장소 하나.

    B 단계(서비스 그래프) 필드: service 는 서비스 그래프의 이름, openapi 는 엔드포인트·스키마 출처("self" 는 이 앱),
    client_env 는 다른 저장소가 이 서비스를 부를 때 쓰는 환경변수 → 그 값에 붙어 있는 경로 접두,
    call_sites 는 호출 지점을 찾을 파일(glob), route_root 는 Next.js App Router 루트(그 아래 route.ts 가 inbound 엔드포인트).
    """

    owner: str
    name: str
    ref: str = "main"
    source_roots: list[str]
    layers: list[LayerRule]
    allowed_edges: list[AllowedEdge] = []
    entry_points: list[str] = []  # orphan 에서 뺀다. `**/` 는 0개 이상의 디렉터리
    diagnostics: DiagnosticToggles = DiagnosticToggles()
    service: str | None = None  # 비면 저장소 키를 서비스 이름으로 쓴다
    openapi: Literal["self"] | OpenApiSource | None = None
    client_env: dict[str, str] = {}
    call_sites: list[str] = []
    route_root: str | None = None

    def is_call_site(self, path: str) -> bool:
        return any(glob_regex(pattern).fullmatch(path) for pattern in self.call_sites)


class SourceFile(BaseModel):
    """파싱한 파일 하나. symbols 는 정의 순서다.

    is_barrel 은 orphan 에서 빼는 `__init__.py` 와 `export ... from` 만 있는 index.ts(x) 다.
    """

    path: str
    symbols: list[str]
    is_barrel: bool = False


class GraphLayer(BaseModel):
    id: str
    may_import: list[str]
    file_count: int


class GraphNode(BaseModel):
    id: str  # 저장소 루트 기준 POSIX 경로
    layer: str
    symbols: list[str]
    in_degree: int
    out_degree: int


class GraphEdge(BaseModel):
    source: str
    target: str
    status: Literal["valid", "violation"]


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


class CodeGraphDiagnostics(BaseModel):
    layer_violations: list[LayerViolation] = []
    cycles: list[list[str]] = []  # SCC 마다 대표 경로 하나 [a, b, ..., a]
    orphan_files: list[str] = []
    hub_files: list[HubFile] = []


class OutboundCall(BaseModel):
    """서비스 간 호출 지점. raw_path 는 기준 URL 뒤의 원문(`${...}`·`{...}`·쿼리 포함)이고 비교는 normalize_path 로 한다."""

    caller_file: str
    caller_symbol: str  # 감싼 함수. 메서드는 `클래스.메서드`, 모듈 수준이면 빈 문자열
    env_var: str
    http_method: str
    raw_path: str


class InboundRoute(BaseModel):
    """Next.js route.ts 가 export 하는 HTTP 메서드 함수 하나. path 는 파일 경로에서 만든다(`[id]` → `{id}`, `(group)` 은 뺀다)."""

    file: str
    http_method: str
    path: str


class CodeGraph(BaseModel):
    """code_graphs 컬렉션 문서. 문서 하나 = (repo, commit_sha) 이고 같은 commit 은 다시 분석하지 않는다 (G-09).

    allowed_edges 는 분석 때의 선언 예외다. context 마크다운이 위반 목록과 같은 시점의 설정을 보이도록 같이 저장한다.
    """

    repo: str
    commit_sha: str
    analyzed_at: datetime
    tech_stack: list[str]
    layers: list[GraphLayer]
    allowed_edges: list[AllowedEdge]
    nodes: list[GraphNode]
    edges: list[GraphEdge]
    diagnostics: CodeGraphDiagnostics
    outbound_calls: list[OutboundCall] = []
    inbound_routes: list[InboundRoute] = []

    @classmethod
    def build(
        cls,
        *,
        repo: str,
        commit_sha: str,
        analyzed_at: datetime,
        tech_stack: list[str],
        repo_spec: CodeGraphRepo,
        files: list[SourceFile],
        imports: set[tuple[str, str]],
        outbound_calls: Sequence[OutboundCall] = (),
        inbound_routes: Sequence[InboundRoute] = (),
    ) -> Self:
        """파싱 결과로 그래프와 진단을 만든다. imports 의 양 끝은 files 에 있는 경로여야 한다."""
        file_by_path = {each.path: each for each in files}
        layer_by_path = {path: _layer_of(path, repo_spec.layers) for path in file_by_path}
        graph = nx.DiGraph()
        graph.add_nodes_from(sorted(file_by_path))
        graph.add_edges_from(sorted(imports))

        may_import = {layer.id: set(layer.may_import) for layer in repo_spec.layers}
        allowed = {(edge.source, edge.target) for edge in repo_spec.allowed_edges}
        violations: list[LayerViolation] = []
        for source, target in sorted(imports):
            from_layer, to_layer = layer_by_path[source], layer_by_path[target]
            if _is_violation(from_layer, to_layer, may_import, is_allowed=(source, target) in allowed):
                violations.append(
                    LayerViolation(source=source, target=target, from_layer=from_layer, to_layer=to_layer)
                )
        violating = {(each.source, each.target) for each in violations}

        orphans: list[str] = []
        if repo_spec.diagnostics.orphans:
            entry_patterns = [glob_regex(pattern) for pattern in repo_spec.entry_points]
            orphans = [
                path
                for path in graph
                if graph.in_degree(path) == 0
                and not file_by_path[path].is_barrel
                and not any(pattern.fullmatch(path) for pattern in entry_patterns)
            ]
        hubs: list[HubFile] = []
        if repo_spec.diagnostics.hubs:
            ranked = sorted(
                (path for path in graph if graph.in_degree(path) >= HUB_MIN_IN_DEGREE),
                key=lambda path: (-graph.in_degree(path), -graph.out_degree(path), path),
            )
            hubs = [
                HubFile(
                    path=path,
                    in_degree=graph.in_degree(path),
                    out_degree=graph.out_degree(path),
                    symbol_count=len(file_by_path[path].symbols),
                )
                for path in ranked[:HUB_LIMIT]
            ]

        return cls(
            repo=repo,
            commit_sha=commit_sha,
            analyzed_at=analyzed_at,
            tech_stack=tech_stack,
            layers=[
                GraphLayer(
                    id=layer.id,
                    may_import=layer.may_import,
                    file_count=sum(1 for each in layer_by_path.values() if each == layer.id),
                )
                for layer in repo_spec.layers
            ],
            allowed_edges=repo_spec.allowed_edges,
            nodes=[
                GraphNode(
                    id=path,
                    layer=layer_by_path[path],
                    symbols=file_by_path[path].symbols,
                    in_degree=graph.in_degree(path),
                    out_degree=graph.out_degree(path),
                )
                for path in graph
            ],
            edges=[
                GraphEdge(
                    source=source,
                    target=target,
                    status="violation" if (source, target) in violating else "valid",
                )
                for source, target in sorted(imports)
            ],
            diagnostics=CodeGraphDiagnostics(
                layer_violations=violations, cycles=_find_cycles(graph), orphan_files=orphans, hub_files=hubs
            ),
            outbound_calls=list(outbound_calls),
            inbound_routes=list(inbound_routes),
        )

    def render_context(self) -> str:
        """GET {repo}/context/ 마크다운. CLAUDE.md 갱신·프롬프트 주입용이라 README 템플릿 그대로다."""
        node_by_id = {node.id: node for node in self.nodes}
        diagnostics = self.diagnostics
        lines = [
            f"# Code Graph Context: {self.repo} (`{self.commit_sha[:7]}`)",
            "",
            f"- Analyzed At: {self.analyzed_at.isoformat(timespec='seconds')}",
            f"- Files: {len(self.nodes)} | Internal Edges: {len(self.edges)} "
            f"| Symbols: {sum(len(node.symbols) for node in self.nodes)}",
            f"- Tech Stack: {', '.join(self.tech_stack)}",
        ]
        if any(node.id.endswith(".kt") for node in self.nodes):
            # Kotlin 은 같은 패키지 안의 참조에 import 가 없어 엣지로 보이지 않는다 (README 파싱 규칙)
            lines.append("- Note: Kotlin same-package references have no import and are not edges.")
        lines += [
            "",
            "## 1. Layers & Allowed Imports",
            "| Layer | Files | May Import |",
            "|---|---|---|",
            *(f"| {layer.id} | {layer.file_count} | {', '.join(layer.may_import) or '-'} |" for layer in self.layers),
            "",
            "## 2. Layer Violations (Do Not Add More)",
            _heading("Violations", len(diagnostics.layer_violations)),
            *(
                f"  - `{each.source}` ({each.from_layer}) -> `{each.target}` ({each.to_layer})"
                for each in diagnostics.layer_violations
            ),
            _heading("Declared Exceptions", len(self.allowed_edges)),
            *(f"  - `{edge.source}` -> `{edge.target}`" for edge in self.allowed_edges),
            "",
            "## 3. Cycles & Orphans",
            _inline("Cycles", [" -> ".join(cycle) for cycle in diagnostics.cycles]),
            _inline("Orphans", diagnostics.orphan_files),
            "",
            "## 4. Hub Files (Top In-Degree)",
            "| File | Layer | In | Out | Key Symbols |",
            "|---|---|---|---|---|",
            *(
                f"| `{hub.path}` | {node_by_id[hub.path].layer} | {hub.in_degree} | {hub.out_degree} "
                f"| {', '.join(node_by_id[hub.path].symbols[:_KEY_SYMBOL_COUNT])} |"
                for hub in diagnostics.hub_files
            ),
        ]
        return "\n".join(lines) + "\n"


def _layer_of(path: str, layers: list[LayerRule]) -> str:
    """경로 접두가 가장 긴 레이어. 어느 레이어에도 안 속하면 root (G-14)."""
    matches = [layer for layer in layers if path.startswith(layer.path.rstrip("/") + "/")]
    return max(matches, key=lambda layer: len(layer.path)).id if matches else ROOT_LAYER


def _is_violation(from_layer: str, to_layer: str, may_import: dict[str, set[str]], *, is_allowed: bool) -> bool:
    return (
        ROOT_LAYER not in (from_layer, to_layer)
        and from_layer != to_layer
        and to_layer not in may_import[from_layer]
        and not is_allowed
    )


def _find_cycles(graph: nx.DiGraph) -> list[list[str]]:
    # simple_cycles 는 밀집 그래프에서 지수적이라 크기 2 이상 SCC 마다 대표 경로 하나만 적는다 (G-07).
    # subgraph 뷰는 노드·이웃을 set 순서로 돌 수 있어(해시 시드마다 다르다) 정렬한 엣지로 그래프를 새로 만들고
    # 가장 작은 경로에서 시작한다. 그래야 같은 commit 의 대표 경로가 실행마다 같다.
    cycles = []
    for component in nx.strongly_connected_components(graph):
        if len(component) < 2:
            continue
        component_graph = nx.DiGraph(sorted(graph.subgraph(component).edges))
        cycle_edges = nx.find_cycle(component_graph, source=min(component))
        cycles.append([cycle_edges[0][0], *(target for _, target in cycle_edges)])
    return sorted(cycles)


def glob_regex(pattern: str) -> re.Pattern[str]:
    # fnmatch 의 `*` 는 `/` 도 삼키고 `**/` 가 0개 디렉터리를 뜻하지 않아(src/app/page.tsx) 직접 바꾼다
    parts = re.split(r"(\*\*/|\*|\?)", pattern)
    return re.compile(
        "".join({"**/": "(?:.*/)?", "*": "[^/]*", "?": "[^/]"}.get(part, re.escape(part)) for part in parts)
    )


def _heading(label: str, count: int) -> str:
    return f"- {label} ({count})" + (":" if count else "")


def _inline(label: str, items: list[str]) -> str:
    line = _heading(label, len(items))
    return f"{line} {', '.join(f'`{item}`' for item in items)}" if items else line
