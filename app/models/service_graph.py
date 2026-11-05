"""서비스 그래프와 e2e 흐름 (code_graph_design/README.md "서비스 그래프" 2~4절). I/O 없는 계산만 한다.

저장된 호출 지점(CodeGraph.outbound_calls)·Next.js inbound 엔드포인트와 OpenAPI 문서를 받아 노드·엣지를 만들고,
고정된 흐름(FLOWS)을 hop 마다 호출 지점 근거·스키마 이름과 함께 따라간다.
"""

import re
from dataclasses import dataclass
from typing import Any, Literal, Self

from pydantic import BaseModel

from app.models.code_graph import CodeGraph, CodeGraphRepo, OutboundCall

BROWSER = "browser"  # 흐름의 시작. 서비스가 아니다
OpenApiStatus = Literal["ok", "unavailable", "none"]
EdgeStatus = Literal["matched", "missing", "unknown", "external"]

_PLACEHOLDER = re.compile(r"\$\{[^}]*\}|\{[^}]*\}")
_SUCCESS_STATUSES = ("200", "201", "202")
_HTTP_METHODS = ("get", "post", "put", "patch", "delete")


def normalize_path(raw_path: str) -> str:
    """비교 키. 쿼리·조각을 떼고 `${...}`·`{...}` 를 `{}` 로, 겹 슬래시를 하나로, 끝 슬래시를 뗀다."""
    path = raw_path.split("?", 1)[0].split("#", 1)[0]
    path = re.sub(r"/+", "/", _PLACEHOLDER.sub("{}", path))
    return path.rstrip("/") or "/"


class FlowStep(BaseModel):
    caller: str  # 서비스 이름 또는 BROWSER
    callee: str
    http_method: str
    path: str  # 비교 키 형태


class Flow(BaseModel):
    title: str
    steps: list[FlowStep]


def _step(caller: str, callee: str, http_method: str, path: str) -> FlowStep:
    return FlowStep(caller=caller, callee=callee, http_method=http_method, path=path)


# README "서비스 그래프" 4절 표. 서비스 이름은 CODE_GRAPH_REPOS 의 service 다
FLOWS: dict[str, Flow] = {
    "search": Flow(
        title="블로그 검색",
        steps=[
            _step(BROWSER, "bff", "GET", "/api/search"),
            _step("bff", "fastapi", "GET", "/api/v1/search/posts"),
        ],
    ),
    "publish": Flow(
        title="에이전트 발행",
        steps=[
            _step("fastapi", "spring", "POST", "/api/v1/auth/login"),
            _step("fastapi", "nestjs", "POST", "/api/v1/posts"),
            _step("nestjs", "fastapi", "POST", "/api/internal/search/index"),
        ],
    ),
    "posts": Flow(
        title="글 목록",
        steps=[
            _step(BROWSER, "bff", "GET", "/api/posts"),
            _step("bff", "nestjs", "GET", "/api/v1/posts"),
            _step("nestjs", "spring", "GET", "/api/v1/users/email/{}"),
        ],
    ),
    "auth": Flow(
        title="로그인",
        steps=[
            _step(BROWSER, "bff", "POST", "/api/auth/login"),
            _step("bff", "spring", "POST", "/api/v1/auth/login"),
        ],
    ),
}


class ServiceNode(BaseModel):
    """endpoint: 서비스의 HTTP 엔드포인트, code: 엔드포인트가 아닌 호출 지점(클라이언트 클래스 등),
    external: CODE_GRAPH_REPOS 의 client_env 에 없는 환경변수가 가리키는 서비스와 코드에 박힌 외부 호스트."""

    id: str
    kind: Literal["endpoint", "code", "external"]
    service: str | None = None
    repo: str | None = None
    http_method: str | None = None
    path: str | None = None
    file: str | None = None
    symbol: str | None = None
    request_schema: str | None = None
    response_schema: str | None = None


class ServiceEdge(BaseModel):
    """호출 하나. status: matched 대상 엔드포인트 목록에 있다, missing 없다(죽은 호출이거나 경로 접두가 틀렸다),
    unknown 대상의 엔드포인트 목록을 못 받았다, external 네 저장소 밖이다."""

    source: str
    target: str
    caller_repo: str
    caller_file: str
    caller_symbol: str
    env_var: str
    http_method: str
    path: str
    status: EdgeStatus


class FlowHop(BaseModel):
    caller: str
    callee: str
    http_method: str
    path: str
    request_schema: str | None
    response_schema: str | None
    call_sites: list[str]  # `저장소:파일#심볼`. 비어 있으면 코드에서 이 호출을 찾지 못했다
    status: EdgeStatus
    verified: bool  # 호출 코드를 찾았고 대상 엔드포인트도 있다. FLOWS 표는 손으로 쓴 것이라 이 값으로 코드와 대조한다


class FlowTrace(BaseModel):
    flow: str
    title: str
    hops: list[FlowHop]
    mermaid: str


@dataclass
class ServiceSource:
    """서비스 그래프 입력 하나. openapi 는 받은 문서(못 받았거나 없으면 None)다."""

    repo: str
    spec: CodeGraphRepo
    code_graph: CodeGraph | None
    openapi: dict[str, Any] | None
    openapi_status: OpenApiStatus

    @property
    def service(self) -> str:
        return self.spec.service or self.repo


@dataclass
class _Endpoint:
    path: str
    request_schema: str | None = None
    response_schema: str | None = None
    file: str | None = None  # Next.js route.ts


class ServiceGraph(BaseModel):
    nodes: list[ServiceNode]
    edges: list[ServiceEdge]
    openapi: dict[str, OpenApiStatus]  # 서비스 → 문서 상태
    repos: dict[str, str]  # 서비스 → 저장소

    @classmethod
    def build(cls, sources: list[ServiceSource]) -> Self:
        env_targets = {
            env_var: (source.service, prefix)
            for source in sources
            for env_var, prefix in source.spec.client_env.items()
        }
        endpoints = {source.service: _endpoints(source) for source in sources}
        nodes: dict[str, ServiceNode] = {}
        edges: list[ServiceEdge] = []

        for source in sources:
            service = source.service
            for (method, _), endpoint in (endpoints[service] or {}).items():
                _add_endpoint(nodes, service, source.repo, method, endpoint)
            if source.code_graph is None:
                continue
            routes = {(route.file, route.http_method): route for route in source.code_graph.inbound_routes}
            for call in source.code_graph.outbound_calls:
                route = routes.get((call.caller_file, call.caller_symbol))
                if route is not None:
                    source_id = _endpoint_id(service, route.http_method, route.path)
                else:
                    source_id = f"{service}:{call.caller_file}#{call.caller_symbol}"
                    nodes.setdefault(
                        source_id,
                        ServiceNode(
                            id=source_id,
                            kind="code",
                            service=service,
                            repo=source.repo,
                            file=call.caller_file,
                            symbol=call.caller_symbol,
                        ),
                    )
                target_id, path, status = _resolve_target(call, env_targets, endpoints)
                if status == "external":
                    nodes.setdefault(target_id, ServiceNode(id=target_id, kind="external"))
                elif target_id not in nodes:
                    target_service = env_targets[call.env_var][0]
                    target_repo = next((each.repo for each in sources if each.service == target_service), None)
                    nodes[target_id] = ServiceNode(
                        id=target_id,
                        kind="endpoint",
                        service=target_service,
                        repo=target_repo,
                        http_method=call.http_method,
                        path=path,
                    )
                edges.append(
                    ServiceEdge(
                        source=source_id,
                        target=target_id,
                        caller_repo=source.repo,
                        caller_file=call.caller_file,
                        caller_symbol=call.caller_symbol,
                        env_var=call.env_var,
                        http_method=call.http_method,
                        path=path,
                        status=status,
                    )
                )
        # `${apiUrl}/posts` 와 `${apiUrl}/posts?${query}` 처럼 원문만 다른 호출은 엣지 하나다
        unique_edges = {(edge.source, edge.target, edge.caller_file, edge.caller_symbol): edge for edge in edges}
        return cls(
            nodes=sorted(nodes.values(), key=lambda node: node.id),
            edges=[unique_edges[key] for key in sorted(unique_edges)],
            openapi={source.service: source.openapi_status for source in sources},
            repos={source.service: source.repo for source in sources},
        )

    def trace(self, flow_id: str, flow: Flow) -> FlowTrace:
        """흐름의 hop 마다 대상 엔드포인트(스키마)와 그 호출을 하는 코드 위치를 찾는다."""
        node_by_id = {node.id: node for node in self.nodes}
        hops = []
        for step in flow.steps:
            key = normalize_path(step.path)
            endpoint = next(
                (
                    node
                    for node in self.nodes
                    if node.kind == "endpoint"
                    and node.service == step.callee
                    and node.http_method == step.http_method
                    and normalize_path(node.path or "") == key
                ),
                None,
            )
            status: EdgeStatus = "missing"
            if step.caller == BROWSER:
                call_sites = [f"{self.repos.get(step.callee)}:{node.file}" for node in [endpoint] if node and node.file]
                status = "matched" if call_sites else "missing"
            else:
                incoming = [
                    edge
                    for edge in self.edges
                    if endpoint is not None
                    and edge.target == endpoint.id
                    and node_by_id[edge.source].service == step.caller
                ]
                call_sites = sorted(
                    {f"{edge.caller_repo}:{edge.caller_file}#{edge.caller_symbol}" for edge in incoming}
                )
                if incoming:
                    status = incoming[0].status
            hops.append(
                FlowHop(
                    caller=step.caller,
                    callee=step.callee,
                    http_method=step.http_method,
                    path=endpoint.path if endpoint is not None and endpoint.path else step.path,
                    request_schema=endpoint.request_schema if endpoint is not None else None,
                    response_schema=endpoint.response_schema if endpoint is not None else None,
                    call_sites=call_sites,
                    status=status,
                    verified=bool(call_sites) and status == "matched",
                )
            )
        return FlowTrace(flow=flow_id, title=flow.title, hops=hops, mermaid=self._sequence_diagram(hops))

    def _sequence_diagram(self, hops: list[FlowHop]) -> str:
        """mermaid sequenceDiagram. 코드와 대조되지 않은 hop 은 점선(-->>)이다."""
        participants = list(dict.fromkeys(name for hop in hops for name in (hop.caller, hop.callee)))
        lines = ["sequenceDiagram"]
        for name in participants:
            label = "Browser" if name == BROWSER else f"{name} ({self.repos.get(name, '?')})"
            lines.append(f"    participant {name} as {label}")
        for hop in hops:
            arrow = "->>" if hop.verified else "-->>"
            message = f"{hop.http_method} {hop.path}"
            if hop.request_schema or hop.response_schema:
                message += f" ({hop.request_schema or '-'} → {hop.response_schema or '-'})"
            lines.append(f"    {hop.caller}{arrow}{hop.callee}: {message}")
        return "\n".join(lines) + "\n"


def _endpoint_id(service: str, http_method: str, path: str) -> str:
    return f"{service} {http_method} {path}"


def _add_endpoint(
    nodes: dict[str, ServiceNode], service: str, repo: str, http_method: str, endpoint: _Endpoint
) -> None:
    node_id = _endpoint_id(service, http_method, endpoint.path)
    nodes[node_id] = ServiceNode(
        id=node_id,
        kind="endpoint",
        service=service,
        repo=repo,
        http_method=http_method,
        path=endpoint.path,
        file=endpoint.file,
        request_schema=endpoint.request_schema,
        response_schema=endpoint.response_schema,
    )


def _endpoints(source: ServiceSource) -> dict[tuple[str, str], _Endpoint] | None:
    """(메서드, 비교 키) → 엔드포인트. OpenAPI 문서가 있으면 그것(스키마까지), 없으면 소스에서 읽은 라우트, 둘 다 없으면 None."""
    if source.openapi is not None:
        endpoints: dict[tuple[str, str], _Endpoint] = {}
        for path, operations in source.openapi.get("paths", {}).items():
            for method, operation in operations.items() if isinstance(operations, dict) else []:
                if method in _HTTP_METHODS and isinstance(operation, dict):
                    endpoints[(method.upper(), normalize_path(path))] = _Endpoint(
                        path=path,
                        request_schema=_schema_name((operation.get("requestBody") or {}).get("content")),
                        response_schema=_response_schema(operation.get("responses") or {}),
                    )
        return endpoints
    if source.code_graph is not None and source.code_graph.inbound_routes:
        return {
            (route.http_method, normalize_path(route.path)): _Endpoint(path=route.path, file=route.file)
            for route in source.code_graph.inbound_routes
        }
    return None


def _resolve_target(
    call: OutboundCall,
    env_targets: dict[str, tuple[str, str]],
    endpoints: dict[str, dict[tuple[str, str], _Endpoint] | None],
) -> tuple[str, str, EdgeStatus]:
    """(대상 노드 id, 표시 경로, 상태). 환경변수의 경로 접두를 붙여 대상 서비스 엔드포인트와 맞춘다."""
    env_var, http_method, raw_path = call.env_var, call.http_method, call.raw_path
    if call.external_host is not None:
        return f"host:{call.external_host}", normalize_path(raw_path), "external"
    if env_var not in env_targets:
        return f"env:{env_var}", normalize_path(raw_path), "external"
    service, prefix = env_targets[env_var]
    key = normalize_path(prefix + "/" + raw_path)
    service_endpoints = endpoints.get(service)
    if service_endpoints is None:
        return _endpoint_id(service, http_method, key), key, "unknown"
    endpoint = service_endpoints.get((http_method, key))
    if endpoint is None:
        return _endpoint_id(service, http_method, key), key, "missing"
    return _endpoint_id(service, http_method, endpoint.path), endpoint.path, "matched"


def _response_schema(responses: dict[str, Any]) -> str | None:
    for status in _SUCCESS_STATUSES:
        if status in responses and isinstance(responses[status], dict):
            return _schema_name(responses[status].get("content"))
    return None


def _schema_name(content: dict[str, Any] | None) -> str | None:
    """`application/json`(없으면 첫 미디어 타입. springdoc 은 `*/*`)의 `$ref` 마지막 이름. 배열이면 `이름[]`."""
    if not isinstance(content, dict) or not content:
        return None
    media = content.get("application/json") or next(iter(content.values()))
    schema = media.get("schema") if isinstance(media, dict) else None
    if not isinstance(schema, dict):
        return None
    if schema.get("type") == "array" and isinstance(schema.get("items"), dict):
        name = _ref_name(schema["items"])
        return f"{name}[]" if name else None
    return _ref_name(schema)


def _ref_name(schema: dict[str, Any]) -> str | None:
    ref = schema.get("$ref")
    if ref is None and isinstance(schema.get("allOf"), list) and len(schema["allOf"]) == 1:
        ref = schema["allOf"][0].get("$ref")
    return ref.rsplit("/", 1)[-1] if isinstance(ref, str) else None
