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
