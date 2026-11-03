"""ServiceGraphService 테스트: OpenAPI 출처별 처리(self·원격·세션 로그인·설정 비어 있음), 실패 캐시(G-13), 결과 캐시, 없는 흐름.

ts_app 트리를 실제로 분석해 가짜 리포지토리에 넣고, 서비스 그래프 입력으로 쓴다.
"""

import pytest

from app.core.cache import SERVICE_GRAPH_CACHE_KEY, cache
from app.core.exceptions import NotFoundError
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
