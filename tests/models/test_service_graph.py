"""ServiceGraph 계산 테스트: 경로 비교 키, 환경변수 접두로 대상 찾기, 엣지 상태 4종, 스키마 이름, 흐름 hop·mermaid."""

from datetime import UTC, datetime

import pytest

from app.models.code_graph import CodeGraph, CodeGraphRepo, InboundRoute, OutboundCall
from app.models.service_graph import BROWSER, Flow, FlowStep, ServiceGraph, ServiceSource, normalize_path

NESTJS_OPENAPI = {
    "paths": {
        "/api/v1/posts": {
            "get": {
                "responses": {
                    "200": {"content": {"application/json": {"schema": {"$ref": "#/components/schemas/PostList"}}}}
                }
            },
            "post": {
                "requestBody": {
                    "content": {"application/json": {"schema": {"$ref": "#/components/schemas/CreatePostDto"}}}
                },
                "responses": {"201": {"content": {"application/json": {"schema": {"$ref": "#/c/s/Post"}}}}},
            },
        },
        "/api/v1/categories": {
            "get": {
                "responses": {
                    "200": {
                        "content": {
                            "application/json": {"schema": {"type": "array", "items": {"$ref": "#/c/s/Category"}}}
                        }
                    }
                }
            }
        },
    }
}
SPRING_OPENAPI = {
    "paths": {
        "/api/v1/auth/login": {
            "post": {
                "requestBody": {"content": {"application/json": {"schema": {"$ref": "#/c/s/LoginRequest"}}}},
                "responses": {"200": {"content": {"*/*": {"schema": {"$ref": "#/c/s/ApiResponseUnit"}}}}},
            }
        }
    }
}


def _spec(service: str, **fields: object) -> CodeGraphRepo:
    return CodeGraphRepo.model_validate(
        {"owner": "JONGHYUNVAN", "name": service, "source_roots": ["src"], "layers": [], "service": service} | fields
    )


def _graph(calls: list[OutboundCall] = (), routes: list[InboundRoute] = ()) -> CodeGraph:
    return CodeGraph(
        repo="r",
        commit_sha="c" * 40,
        analyzed_at=datetime(2026, 11, 3, tzinfo=UTC),
        tech_stack=[],
        layers=[],
        allowed_edges=[],
        nodes=[],
        edges=[],
        diagnostics={},
        outbound_calls=list(calls),
        inbound_routes=list(routes),
    )


def _call(file: str, symbol: str, env_var: str, method: str, raw_path: str) -> OutboundCall:
    return OutboundCall(caller_file=file, caller_symbol=symbol, env_var=env_var, http_method=method, raw_path=raw_path)


def _sources(*, spring_openapi=SPRING_OPENAPI) -> list[ServiceSource]:
    route = "src/app/api/posts/route.ts"
    bff_graph = _graph(
        calls=[
            _call(route, "GET", "POST_API_URL", "GET", "/posts"),
            _call(route, "GET", "POST_API_URL", "GET", "/posts?${queryString}"),
            _call(route, "POST", "POST_API_URL", "POST", "/posts"),
            _call("src/app/api/ai/chat/route.ts", "POST", "AI_API_URL", "POST", "/chat"),
            _call("src/app/api/auth/(server)/login/route.ts", "POST", "AUTH_API_URL", "POST", "/auth/login"),
            _call("src/app/api/auth/(server)/logout/route.ts", "POST", "AUTH_API_URL", "POST", "/auth/logout"),
        ],
        routes=[
            InboundRoute(file=route, http_method="GET", path="/api/posts"),
            InboundRoute(file=route, http_method="POST", path="/api/posts"),
            InboundRoute(file="src/app/api/auth/(server)/login/route.ts", http_method="POST", path="/api/auth/login"),
        ],
    )
    publisher = "app/clients/publisher/post_be.py"
    fastapi_graph = _graph(
        calls=[
            _call(publisher, "PostBEPublisher._create_post", "POST_BE_URL", "POST", "/api/v1/posts"),
            _call(publisher, "PostBEPublisher.list_categories", "POST_BE_URL", "GET", "/api/v1/categories/"),
            _call(publisher, "PostBEPublisher._login", "USER_SERVICE_URL", "POST", "/api/v1/auth/login"),
        ]
    )
    return [
        ServiceSource("vans_story", _spec("bff"), bff_graph, None, "none"),
        ServiceSource(
            "vans_devblog_fastapi", _spec("fastapi", client_env={"SEARCH_API_URL": ""}), fastapi_graph, None, "none"
        ),
        ServiceSource(
            "vans_story_be_post",
            _spec("nestjs", client_env={"POST_API_URL": "/api/v1", "POST_BE_URL": ""}),
            None,
            NESTJS_OPENAPI,
            "ok",
        ),
        ServiceSource(
            "vans_story_be",
            _spec("spring", client_env={"AUTH_API_URL": "/api/v1", "USER_SERVICE_URL": ""}),
            None,
            spring_openapi,
            "ok" if spring_openapi else "unavailable",
        ),
    ]


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("/api/v1/search/posts/?query=${q}&page=1", "/api/v1/search/posts"),
        ("/posts/${encodeURIComponent(id)}/edit", "/posts/{}/edit"),
        ("/api/v1/users/email/{email}", "/api/v1/users/email/{}"),
        ("/api/v1//posts/", "/api/v1/posts"),
        ("", "/"),
    ],
)
def test_normalize_path(raw, expected):
    assert normalize_path(raw) == expected


def test_build_matches_calls_with_env_prefix_and_reports_status():
    graph = ServiceGraph.build(_sources())
    edges = {(edge.source, edge.target): edge.status for edge in graph.edges}

    assert edges == {
        ("bff GET /api/posts", "nestjs GET /api/v1/posts"): "matched",  # 접두 /api/v1 + 쿼리만 다른 두 호출은 하나
        ("bff POST /api/posts", "nestjs POST /api/v1/posts"): "matched",
        ("bff:src/app/api/ai/chat/route.ts#POST", "env:AI_API_URL"): "external",  # inbound route 가 없으면 코드 노드
        ("bff POST /api/auth/login", "spring POST /api/v1/auth/login"): "matched",
        ("bff:src/app/api/auth/(server)/logout/route.ts#POST", "spring POST /api/v1/auth/logout"): "missing",
        ("fastapi:app/clients/publisher/post_be.py#PostBEPublisher._create_post", "nestjs POST /api/v1/posts"): (
            "matched"
        ),
        ("fastapi:app/clients/publisher/post_be.py#PostBEPublisher.list_categories", "nestjs GET /api/v1/categories"): (
            "matched"
        ),
        ("fastapi:app/clients/publisher/post_be.py#PostBEPublisher._login", "spring POST /api/v1/auth/login"): (
            "matched"
        ),
    }
    nodes = {node.id: node for node in graph.nodes}
    assert (nodes["nestjs POST /api/v1/posts"].request_schema, nodes["nestjs POST /api/v1/posts"].response_schema) == (
        "CreatePostDto",
        "Post",
    )
    assert nodes["nestjs GET /api/v1/categories"].response_schema == "Category[]"
    assert nodes["spring POST /api/v1/auth/login"].response_schema == "ApiResponseUnit"  # springdoc 의 */*
    assert nodes["bff GET /api/posts"].file == "src/app/api/posts/route.ts"
    assert nodes["env:AI_API_URL"].kind == "external"
    assert graph.openapi == {"bff": "none", "fastapi": "none", "nestjs": "ok", "spring": "ok"}


def test_build_marks_edges_unknown_when_target_openapi_is_unavailable():
    graph = ServiceGraph.build(_sources(spring_openapi=None))

    statuses = {edge.target: edge.status for edge in graph.edges if edge.target.startswith("spring")}

    assert statuses == {"spring POST /api/v1/auth/login": "unknown", "spring POST /api/v1/auth/logout": "unknown"}
    assert graph.openapi["spring"] == "unavailable"


def test_trace_reports_schemas_call_sites_and_mermaid():
    flow = Flow(
        title="글 작성",
        steps=[
            FlowStep(caller=BROWSER, callee="bff", http_method="POST", path="/api/posts"),
            FlowStep(caller="bff", callee="nestjs", http_method="POST", path="/api/v1/posts"),
            FlowStep(caller="nestjs", callee="spring", http_method="POST", path="/api/v1/auth/login"),
        ],
    )

    trace = ServiceGraph.build(_sources()).trace("write", flow)

    assert [(hop.request_schema, hop.response_schema, hop.call_sites, hop.verified) for hop in trace.hops] == [
        (None, None, ["vans_story:src/app/api/posts/route.ts"], True),
        ("CreatePostDto", "Post", ["vans_story:src/app/api/posts/route.ts#POST"], True),
        ("LoginRequest", "ApiResponseUnit", [], False),  # nestjs 는 이 호출을 하지 않는다
    ]
    assert trace.mermaid == (
        "sequenceDiagram\n"
        "    participant browser as Browser\n"
        "    participant bff as bff (vans_story)\n"
        "    participant nestjs as nestjs (vans_story_be_post)\n"
        "    participant spring as spring (vans_story_be)\n"
        "    browser->>bff: POST /api/posts\n"
        "    bff->>nestjs: POST /api/v1/posts (CreatePostDto → Post)\n"
        "    nestjs-->>spring: POST /api/v1/auth/login (LoginRequest → ApiResponseUnit)\n"
    )
