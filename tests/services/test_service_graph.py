"""ServiceGraphService 테스트: OpenAPI 출처별 처리(self·원격·세션 로그인·설정 비어 있음), 실패 캐시(G-13), 결과 캐시, 없는 흐름.

ts_app 트리를 실제로 분석해 가짜 리포지토리에 넣고, 서비스 그래프 입력으로 쓴다.
"""

from datetime import UTC, datetime

import pytest

from app.core.cache import SERVICE_GRAPH_CACHE_KEY, cache
from app.core.exceptions import NotFoundError
from app.models.code_graph import CodeGraph, CodeGraphDiagnostics, CodeGraphRepo, InboundRoute, OutboundCall
from app.models.service_graph import Flow, FlowHop, FlowStep, ServiceGraph, ServiceSource
from app.services.code_graph import CodeGraphService
from app.services.service_graph import ServiceGraphService
from tests.conftest import load_golden
from tests.factories import make_tarball
from tests.fakes import FakeCodeGraphRepository, FakeGitHubClient, FakeOpenApiClient

SELF_OPENAPI = {"paths": {"/api/v1/search/posts/": {"get": {"responses": {}}}}}
SPRING_DOC = {"paths": {"/api/v1/auth/login": {"post": {}}}}
POST_DOC = {"paths": {"/api/v1/posts": {"get": {}}}}


def _repos(**overrides: dict) -> dict:
    repos = load_golden("code_graph/repos")
    repos["kotlin_app"]["openapi"] = {"base_setting": "user_service_url", "path": "/v3/api-docs"}
    repos["nest_app"] = {
        "owner": "JONGHYUNVAN",
        "name": "nest_app",
        "source_roots": ["src"],
        "layers": [],
        "service": "nestjs",
        "openapi": {
            "base_setting": "post_be_url",
            "path": "/api/posts/swagger-json",
            "login": {"path": "/api/posts/swagger/auth", "password_setting": "post_be_swagger_password"},
        },
        "client_env": {"POST_API_URL": "/api/v1"},
    }
    return repos | overrides


def _service(openapi_client: FakeOpenApiClient, settings: dict[str, str], repos: dict | None = None):
    repository = FakeCodeGraphRepository()
    CodeGraphService(
        repository, FakeGitHubClient(make_tarball("ts_app")), _repos(), run_in_background=lambda f, *a: f(*a)
    ).analyze_repo("ts_app")
    return ServiceGraphService(
        repository=repository,
        openapi_client=openapi_client,
        repos=repos or _repos(),
        self_openapi=lambda: SELF_OPENAPI,
        setting_value=lambda name: settings.get(name, ""),
    )


SETTINGS = {
    "user_service_url": "https://spring/",
    "post_be_url": "https://post",
    "post_be_swagger_password": "pw",
}


def test_service_graph_reads_each_openapi_source():
    openapi_client = FakeOpenApiClient(
        {"https://spring/v3/api-docs": SPRING_DOC, "https://post/api/posts/swagger-json": POST_DOC}
    )

    graph = _service(openapi_client, SETTINGS).get_service_graph()

    assert graph.openapi == {"fastapi": "ok", "bff": "none", "spring": "ok", "nestjs": "ok"}
    assert openapi_client.calls == [
        ("https://spring/v3/api-docs", None, ""),
        ("https://post/api/posts/swagger-json", "https://post/api/posts/swagger/auth", "pw"),
    ]
    ids = {node.id for node in graph.nodes}
    assert {"fastapi GET /api/v1/search/posts/", "spring POST /api/v1/auth/login"} <= ids
    assert "bff GET /api/posts" in ids  # ts_app route.ts


@pytest.mark.parametrize("missing", ["post_be_url", "post_be_swagger_password"])
def test_openapi_skipped_without_host_or_password(missing):
    openapi_client = FakeOpenApiClient({"https://spring/v3/api-docs": SPRING_DOC})

    graph = _service(
        openapi_client, {key: value for key, value in SETTINGS.items() if key != missing}
    ).get_service_graph()

    assert graph.openapi["nestjs"] == "unavailable"
    assert all("swagger" not in url for url, _, _ in openapi_client.calls)


def test_openapi_failure_is_cached():
    openapi_client = FakeOpenApiClient({})
    service = _service(openapi_client, SETTINGS)

    first = service.get_service_graph()
    cache.delete(SERVICE_GRAPH_CACHE_KEY)  # 결과 캐시만 지워도 OpenAPI 는 다시 부르지 않는다
    second = service.get_service_graph()

    assert first.openapi["spring"] == second.openapi["spring"] == "unavailable"
    assert len(openapi_client.calls) == 2


def test_service_graph_result_is_cached_until_analysis_clears_it():
    openapi_client = FakeOpenApiClient({})
    service = _service(openapi_client, {})

    service.get_service_graph()

    assert cache.get(SERVICE_GRAPH_CACHE_KEY) is not None
    CodeGraphService(
        FakeCodeGraphRepository(),
        FakeGitHubClient(make_tarball("ts_app", "b" * 40)),
        _repos(),
        run_in_background=lambda f, *a: f(*a),
    ).analyze_repo("ts_app")
    assert cache.get(SERVICE_GRAPH_CACHE_KEY) is None


def test_get_flow_traces_known_flow_and_rejects_unknown():
    service = _service(FakeOpenApiClient({}), {})

    flow = service.get_flow("search")

    assert [hop.callee for hop in flow.hops] == ["bff", "fastapi"]
    assert flow.mermaid.startswith("sequenceDiagram\n")
    with pytest.raises(NotFoundError):
        service.get_flow("nope")


def _source(repo: str, *, calls=(), routes=(), client_env=None, service=None) -> ServiceSource:
    spec = CodeGraphRepo(
        owner="x", name=repo, source_roots=["src"], layers=[], service=service or repo, client_env=client_env or {}
    )
    code_graph = CodeGraph(
        repo=repo,
        commit_sha="a" * 40,
        analyzed_at=datetime(2026, 11, 5, tzinfo=UTC),
        tech_stack=[],
        layers=[],
        allowed_edges=[],
        nodes=[],
        edges=[],
        diagnostics=CodeGraphDiagnostics(),
        outbound_calls=list(calls),
        inbound_routes=list(routes),
    )
    return ServiceSource(repo=repo, spec=spec, code_graph=code_graph, openapi=None, openapi_status="unavailable")


def _call(env_var: str, method: str, path: str, host: str | None = None) -> OutboundCall:
    return OutboundCall(
        caller_file="src/c.ts",
        caller_symbol="C.call",
        env_var=env_var,
        http_method=method,
        raw_path=path,
        external_host=host,
    )


def test_external_host_becomes_external_node():
    graph = ServiceGraph.build([_source("bff", calls=[_call("", "GET", "/v8/chart", "query1.finance.yahoo.com")])])

    [edge] = graph.edges
    assert (edge.target, edge.status) == ("host:query1.finance.yahoo.com", "external")
    assert any(node.id == edge.target and node.kind == "external" for node in graph.nodes)


def test_source_routes_match_calls_when_openapi_is_unavailable():
    # Spring 의 /v3/api-docs 는 운영에서 비공개라 소스에서 읽은 라우트로 맞춘다 (G-29)
    caller = _source("nest", calls=[_call("USER_URL", "GET", "/users/email/${email}")])
    callee = _source(
        "spring",
        routes=[InboundRoute(file="U.kt", http_method="GET", path="/api/v1/users/email/{email}")],
        client_env={"USER_URL": "/api/v1"},
    )

    [edge] = ServiceGraph.build([caller, callee]).edges

    assert edge.status == "matched"
    assert edge.path == "/api/v1/users/email/{email}"


def _hop(graph: ServiceGraph, caller: str, callee: str, path: str) -> FlowHop:
    flow = Flow(title="t", steps=[FlowStep(caller=caller, callee=callee, http_method="GET", path=path)])
    return graph.trace("f", flow).hops[0]


def test_hop_is_verified_only_when_call_site_and_target_endpoint_both_exist():
    matched = ServiceGraph.build(
        [
            _source("nest", calls=[_call("USER_URL", "GET", "/users")]),
            _source(
                "spring",
                routes=[InboundRoute(file="U.kt", http_method="GET", path="/api/v1/users")],
                client_env={"USER_URL": "/api/v1"},
            ),
        ]
    )
    hop = _hop(matched, "nest", "spring", "/api/v1/users")
    assert (hop.status, hop.verified) == ("matched", True)

    missing = ServiceGraph.build(
        [
            _source("nest", calls=[_call("USER_URL", "GET", "/gone")]),
            _source(
                "spring",
                routes=[InboundRoute(file="U.kt", http_method="GET", path="/api/v1/users")],
                client_env={"USER_URL": "/api/v1"},
            ),
        ]
    )
    hop = _hop(missing, "nest", "spring", "/api/v1/gone")
    assert hop.call_sites != []
    assert (hop.status, hop.verified) == ("missing", False)

    unknown = ServiceGraph.build(
        [
            _source("nest", calls=[_call("USER_URL", "GET", "/users")]),
            _source("spring", client_env={"USER_URL": "/api/v1"}),
        ]
    )
    hop = _hop(unknown, "nest", "spring", "/api/v1/users")
    assert (hop.status, hop.verified) == ("unknown", False)
