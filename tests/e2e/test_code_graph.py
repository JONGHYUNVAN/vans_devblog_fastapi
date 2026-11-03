"""실제 GitHub tarball 로 네 저장소를 분석해 로드맵 A 완료 기준(레이어 위반)을 확인한다. 기본 실행에서 빠진다.

Mongo 대신 가짜 리포지토리에 저장한다(Mongo 저장은 `python -m cli code-graph analyze <repo>` 로 확인한다).
설정은 code_graph_design/repos.example.json 이다. 사용: pytest -m e2e tests/e2e/test_code_graph.py
"""

import json
from collections import Counter
from pathlib import Path

import pytest

from app.clients.github import GitHubClient
from app.services.code_graph import CodeGraphService
from tests.fakes import FakeCodeGraphRepository

pytestmark = pytest.mark.e2e

REPOS = json.loads((Path(__file__).parents[2] / "code_graph_design" / "repos.example.json").read_text(encoding="utf-8"))


@pytest.mark.parametrize(
    ("repo", "expected"),
    [
        ("vans_devblog_fastapi", {}),  # 선언된 예외 7건 밖의 위반이 없다
        ("vans_story", {"components -> app": 17, "lib -> store": 2, "lib -> components": 1, "constants -> lib": 1}),
        ("vans_story_be_post", {}),
        ("vans_story_be", {}),
    ],
)
def test_layer_violations_match_roadmap_a(repo, expected):
    repository = FakeCodeGraphRepository()
    service = CodeGraphService(
        repository,
        GitHubClient(timeout_s=60, max_bytes=64 * 1024 * 1024),
        REPOS,
        run_in_background=lambda function, *args: function(*args),
    )

    service.analyze_repo(repo)

    code_graph = repository.get_latest(repo)
    assert code_graph is not None, "분석이 저장되지 않았다(경고 로그 참고)"
    violations = Counter(f"{each.from_layer} -> {each.to_layer}" for each in code_graph.diagnostics.layer_violations)
    assert violations == Counter(expected)


def test_search_and_publish_flows_match_roadmap_b():
    """로드맵 B 완료 기준: search·publish 흐름의 hop 마다 호출 지점과 스키마 이름이 나온다.

    이 앱의 스키마는 app.openapi 로 늘 확인한다. NestJS·Spring 스키마는 .env 에 POST_BE_URL·POST_BE_SWAGGER_PASSWORD·
    USER_SERVICE_URL 이 있을 때만 확인하고, 없으면 그 hop 은 호출 지점만 본다.
    """
    from app.clients.openapi import OpenApiClient
    from app.core.config import get_settings
    from app.main import app
    from app.services.service_graph import ServiceGraphService

    repository = FakeCodeGraphRepository()
    code_graph_service = CodeGraphService(
        repository,
        GitHubClient(timeout_s=60, max_bytes=64 * 1024 * 1024),
        REPOS,
        run_in_background=lambda function, *args: function(*args),
    )
    for repo in REPOS:
        code_graph_service.analyze_repo(repo)
    settings = get_settings()

    def setting_value(name: str) -> str:
        value = getattr(settings, name, None)
        return value.get_secret_value() if hasattr(value, "get_secret_value") else value or ""

    # Mongo 대신 방금 분석한 결과를 읽는다
    service = ServiceGraphService(repository, OpenApiClient(timeout_s=15), REPOS, app.openapi, setting_value)

    openapi = service.get_service_graph().openapi
    search, publish = service.get_flow("search"), service.get_flow("publish")

    assert all(hop.verified for hop in [*search.hops, *publish.hops]), [*search.hops, *publish.hops]
    assert (search.hops[1].path, search.hops[1].response_schema) == ("/api/v1/search/posts/", "SearchResult")
    assert (publish.hops[2].request_schema, publish.hops[2].response_schema) == ("SearchIndexPayload", "IndexingResult")
    if openapi["spring"] == "ok":
        assert publish.hops[0].request_schema is not None
    if openapi["nestjs"] == "ok":
        assert publish.hops[1].request_schema is not None
    print(openapi, search.mermaid, publish.mermaid, sep="\n")
