"""CodeGraphService 테스트. 가짜 다운로더가 tests/golden/code_graph 의 작은 소스 트리를 tarball 로 주고, 가짜 리포지토리에 저장한다.

트리마다 파싱·경로 해석·진단 결과를 golden(tests/golden/code_graph/{트리}.json)과 그대로 비교한다.
"""

import logging
from datetime import UTC, datetime

import pytest
from pydantic import ValidationError

from app.core.exceptions import NotFoundError, ServiceUnavailableError
from app.models.code_graph import CodeGraphRepo
from app.services.code_graph import KEEP_PER_REPO, CodeGraphService
from tests.conftest import load_golden
from tests.factories import make_tarball
from tests.fakes import FakeCodeGraphRepository, FakeGitHubClient

REPOS = load_golden("code_graph/repos")
SHA = "a" * 40
NEW_SHA = "b" * 40


def _run_now(function, *args) -> None:
    function(*args)


def _service(
    repository: FakeCodeGraphRepository,
    github_client: FakeGitHubClient | None = None,
    run_in_background=_run_now,
    tree: str = "ts_app",
) -> CodeGraphService:
    return CodeGraphService(
        repository=repository,
        github_client=github_client or FakeGitHubClient(make_tarball(tree, SHA)),
        repos=REPOS,
        run_in_background=run_in_background,
    )


def _analyzed(tree: str = "ts_app", commit_sha: str = SHA) -> FakeCodeGraphRepository:
    repository = FakeCodeGraphRepository()
    _service(repository, FakeGitHubClient(make_tarball(tree, commit_sha)), tree=tree).analyze_repo(tree)
    return repository


@pytest.mark.parametrize("tree", ["python_app", "ts_app", "kotlin_app"])
def test_analyze_repo_builds_graph_matching_golden(tree):
    repository = FakeCodeGraphRepository()
    github_client = FakeGitHubClient(make_tarball(tree, SHA))

    result = _service(repository, github_client, tree=tree).analyze_repo(tree)

    assert (result.repo, result.commit_sha, result.status) == (tree, SHA, "queued")
    assert github_client.calls == [("JONGHYUNVAN", tree, "main")]
    code_graph = repository.get_by_commit(tree, SHA)
    assert code_graph.model_dump(
        mode="json", include={"tech_stack", "layers", "nodes", "edges", "diagnostics"}
    ) == load_golden(f"code_graph/{tree}")
    assert code_graph.allowed_edges == CodeGraphRepo.model_validate(REPOS[tree]).allowed_edges
    assert repository.stale_calls == [(tree, KEEP_PER_REPO)]


def test_analyze_repo_hands_parsing_to_background():
    repository = FakeCodeGraphRepository()
    queued: list[tuple] = []

    result = _service(repository, run_in_background=lambda *args: queued.append(args)).analyze_repo("ts_app")

    assert result.status == "queued"
    assert len(queued) == 1 and repository.code_graphs == {}


def test_analyze_repo_returns_stored_without_reanalysis_when_commit_exists():
    repository = _analyzed()
    queued: list[tuple] = []

    result = _service(repository, run_in_background=lambda *args: queued.append(args)).analyze_repo("ts_app")

    assert (result.commit_sha, result.status) == (SHA, "stored")
    assert queued == []


def test_analyze_repo_raises_not_found_for_unconfigured_repo():
    github_client = FakeGitHubClient(make_tarball("ts_app"))

    with pytest.raises(NotFoundError):
        _service(FakeCodeGraphRepository(), github_client).analyze_repo("https://github.com/someone/else")

    assert github_client.calls == []


def test_analyze_repo_propagates_download_failure():
    github_client = FakeGitHubClient(error=ServiceUnavailableError("tarball download failed"))

    with pytest.raises(ServiceUnavailableError):
        _service(FakeCodeGraphRepository(), github_client).analyze_repo("ts_app")


def test_analyze_repo_rejects_invalid_repo_settings():
    service = CodeGraphService(
        repository=FakeCodeGraphRepository(),
        github_client=FakeGitHubClient(make_tarball("ts_app")),
        repos={"broken": {"owner": "JONGHYUNVAN"}},
        run_in_background=_run_now,
    )

    with pytest.raises(ValidationError):
        service.analyze_repo("broken")


def test_analyze_repo_keeps_previous_graph_when_background_analysis_fails(caplog):
    repository = _analyzed()
    repository.upsert_error = RuntimeError("mongo down")
    service = _service(repository, FakeGitHubClient(make_tarball("ts_app", NEW_SHA)))

    with caplog.at_level(logging.WARNING, logger="app.services.code_graph"):
        result = service.analyze_repo("ts_app")

    assert (result.commit_sha, result.status) == (NEW_SHA, "queued")
    assert repository.get_latest("ts_app").commit_sha == SHA
    assert "코드 그래프 분석 실패 (ts_app@bbbbbbb)" in caplog.text


def test_get_code_graph_nests_graph_and_caches_until_next_store():
    repository = _analyzed()
    service = _service(repository, FakeGitHubClient(make_tarball("ts_app", NEW_SHA)))

    first = service.get_code_graph("ts_app")
    previous = repository.code_graphs.pop(("ts_app", SHA))
    cached = service.get_code_graph("ts_app")
    repository.code_graphs[("ts_app", SHA)] = previous
    service.analyze_repo("ts_app")  # 같은 프로세스의 저장은 캐시를 지운다
    refreshed = service.get_code_graph("ts_app")

    assert first.commit_sha == SHA and cached is first
    assert [node.id for node in first.graph.nodes][:2] == ["src/app/about/page.tsx", "src/app/api/posts/route.ts"]
    assert len(first.graph.edges) == 15 and first.outbound_calls == []
    assert refreshed.commit_sha == NEW_SHA


@pytest.mark.parametrize("repo", ["ts_app", "unknown"])
def test_get_code_graph_raises_not_found_when_not_analyzed_or_not_configured(repo):
    with pytest.raises(NotFoundError):
        _service(FakeCodeGraphRepository()).get_code_graph(repo)


def test_list_code_graphs_summarizes_analyzed_repos_in_settings_order():
    repository = _analyzed("kotlin_app")
    _service(repository, FakeGitHubClient(make_tarball("ts_app", NEW_SHA))).analyze_repo("ts_app")

    summaries = _service(repository).list_code_graphs()

    assert [(each.repo, each.commit_sha) for each in summaries] == [("ts_app", NEW_SHA), ("kotlin_app", SHA)]
    ts_summary = summaries[0]
    assert (ts_summary.violation_count, ts_summary.cycle_count, ts_summary.orphan_count, ts_summary.hub_count) == (
        2,
        1,
        1,
        1,
    )
    assert ts_summary.analyzed_at.tzinfo is not None and ts_summary.analyzed_at <= datetime.now(UTC)


def test_get_code_graph_context_renders_latest_analysis():
    context = _service(_analyzed()).get_code_graph_context("ts_app")

    assert context.startswith("# Code Graph Context: ts_app (`aaaaaaa`)\n")
    assert "  - `src/lib/api.ts` (lib) -> `src/store/index.ts` (store)\n" in context


def test_get_code_graph_context_raises_not_found_when_not_analyzed():
    with pytest.raises(NotFoundError):
        _service(FakeCodeGraphRepository()).get_code_graph_context("kotlin_app")
