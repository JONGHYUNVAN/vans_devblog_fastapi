"""CodeGraphService 테스트. 가짜 다운로더가 tests/golden/code_graph 의 작은 소스 트리를 tarball 로 주고, 가짜 리포지토리에 저장한다.

트리마다 파싱·경로 해석·진단 결과를 golden(tests/golden/code_graph/{트리}.json)과 그대로 비교한다.
"""

import logging
import sys
from datetime import UTC, datetime

import pytest
from pydantic import ValidationError

from app.core.exceptions import NotFoundError, ServiceUnavailableError
from app.models.code_graph import CodeGraphRepo
from app.services.code_graph import KEEP_PER_REPO, AnalysisTracker, CodeGraphRepoConfigService, CodeGraphService
from tests.conftest import load_golden
from tests.factories import make_tarball
from tests.fakes import FakeCodeGraphRepoConfigRepository, FakeCodeGraphRepository, FakeGitHubClient

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


def test_analyze_repo_follows_dynamic_imports_so_lazy_files_are_not_orphans():
    repos = {
        "ts_dynamic": {
            **REPOS["ts_app"],
            "name": "ts_dynamic",
            "layers": [
                {"id": "app", "path": "src/app", "may_import": ["components"]},
                {"id": "components", "path": "src/components", "may_import": []},
            ],
            "allowed_edges": [],
            "entry_points": ["src/app/**/page.tsx"],
            "diagnostics": {"orphans": True, "hubs": False},
        }
    }
    repository = FakeCodeGraphRepository()
    github_client = FakeGitHubClient(make_tarball("ts_dynamic", SHA))
    CodeGraphService(repository, github_client, repos, run_in_background=_run_now).analyze_repo("ts_dynamic")
    code_graph = repository.get_by_commit("ts_dynamic", SHA)

    assert [(edge.source, edge.target) for edge in code_graph.edges] == [
        ("src/app/page.tsx", "src/components/Lazy.tsx")
    ]
    assert code_graph.diagnostics.orphan_files == ["src/components/Unused.tsx"]


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


def _kinds_repos(tree: str, source_root: str) -> dict:
    return {
        tree: {
            **REPOS["ts_app"],
            "name": tree,
            "source_roots": [source_root],
            "layers": [],
            "allowed_edges": [],
            "entry_points": [],
            "diagnostics": {"orphans": False, "hubs": False},
        }
    }


def _analyze_with(tree: str, repos: dict) -> FakeCodeGraphRepository:
    repository = FakeCodeGraphRepository()
    github_client = FakeGitHubClient(make_tarball(tree, SHA))
    CodeGraphService(repository, github_client, repos, run_in_background=_run_now).analyze_repo(tree)
    return repository


def test_ts_edge_kinds_match_what_the_compiler_erases():
    code_graph = _analyze_with("ts_kinds", _kinds_repos("ts_kinds", "src")).get_by_commit("ts_kinds", SHA)

    kinds = {edge.target.rsplit("/", 1)[-1]: edge.kind for edge in code_graph.edges if edge.source.endswith("main.tsx")}
    assert kinds == {
        "typeKeyword.ts": "type",  # import type
        "usedAsType.ts": "type",  # 타입 위치에서만 쓴다
        "typeSpecifier.ts": "type",  # 지정자가 전부 type
        "typeofOnly.ts": "type",  # typeof 타입 쿼리에서만 쓴다
        "value.ts": "static",  # 값과 타입이 섞였다
        "sideEffect.ts": "static",  # 부수 효과 import
        "Comp.tsx": "static",  # JSX 로 쓴다
        "reexportedType.ts": "type",  # export type ... from
        "reexportedValue.ts": "static",
        "lazy.ts": "dynamic",
    }


def test_decorated_file_keeps_constructor_type_import_as_runtime():
    code_graph = _analyze_with("ts_kinds", _kinds_repos("ts_kinds", "src")).get_by_commit("ts_kinds", SHA)

    [edge] = [edge for edge in code_graph.edges if edge.source.endswith("di/service.ts")]
    assert edge.kind == "static"


def test_cycles_count_only_runtime_and_type_only_groups_are_reported_apart():
    diagnostics = _analyze_with("ts_kinds", _kinds_repos("ts_kinds", "src")).get_by_commit("ts_kinds", SHA).diagnostics

    assert diagnostics.cycles == [["src/cycle/runA.ts", "src/cycle/runB.ts", "src/cycle/runA.ts"]]
    assert diagnostics.cycle_groups == [["src/cycle/runA.ts", "src/cycle/runB.ts"]]
    assert diagnostics.type_only_cycle_groups == [["src/cycle/forward.ts", "src/cycle/typeBack2.ts"]]


def test_python_type_checking_import_is_type_edge():
    code_graph = _analyze_with("py_kinds", _kinds_repos("py_kinds", "app")).get_by_commit("py_kinds", SHA)

    assert {edge.target: edge.kind for edge in code_graph.edges} == {"app/core.py": "static", "app/typed.py": "type"}


def test_analyze_repo_checks_commit_without_full_download_when_result_is_valid():
    repository = _analyzed()
    github_client = FakeGitHubClient(make_tarball("ts_app", SHA))

    result = _service(repository, github_client).analyze_repo("ts_app")

    assert result.status == "stored"
    assert github_client.sha_calls == [("JONGHYUNVAN", "ts_app", "main")]
    assert github_client.calls == []


def test_analyze_repo_reanalyzes_same_commit_when_settings_change():
    repository = _analyzed()
    changed = {"ts_app": {**REPOS["ts_app"], "allowed_edges": []}}

    result = CodeGraphService(
        repository, FakeGitHubClient(make_tarball("ts_app", SHA)), changed, run_in_background=_run_now
    ).analyze_repo("ts_app")

    assert result.status == "queued"
    assert repository.get_by_commit("ts_app", SHA).allowed_edges == []


def test_analyze_repo_reanalyzes_document_from_before_fingerprints():
    repository = _analyzed()
    stored = repository.get_by_commit("ts_app", SHA)
    repository.upsert(stored.model_copy(update={"fingerprint": ""}))

    assert _service(repository).analyze_repo("ts_app").status == "queued"
    assert repository.get_by_commit("ts_app", SHA).fingerprint != ""


def test_analyze_repo_reports_running_and_does_not_queue_twice():
    queued = []
    tracker = AnalysisTracker()
    service = CodeGraphService(
        FakeCodeGraphRepository(),
        FakeGitHubClient(make_tarball("ts_app", SHA)),
        REPOS,
        run_in_background=lambda *args: queued.append(args),
        tracker=tracker,
    )

    assert service.analyze_repo("ts_app").status == "queued"
    assert service.analyze_repo("ts_app").status == "running"
    assert len(queued) == 1
    assert service.get_analysis_status("ts_app").state == "running"


def test_analysis_status_is_done_after_success_and_idle_before():
    repository = FakeCodeGraphRepository()
    service = _service(repository)

    assert service.get_analysis_status("ts_app").state == "idle"
    service.analyze_repo("ts_app")
    status = service.get_analysis_status("ts_app")
    assert (status.state, status.commit_sha, status.error) == ("done", SHA, None)


def test_analysis_status_is_failed_when_background_fails_and_can_retry():
    service = _service(FakeCodeGraphRepository(upsert_error=RuntimeError("mongo down with secret")))

    assert service.analyze_repo("ts_app").status == "queued"
    status = service.get_analysis_status("ts_app")
    assert (status.state, status.error) == ("failed", "RuntimeError")
    assert service.analyze_repo("ts_app").status == "queued"


def test_analysis_fails_visibly_when_grammar_package_cannot_load(monkeypatch):
    monkeypatch.setitem(sys.modules, "tree_sitter_language_pack", None)
    repository = FakeCodeGraphRepository()
    service = _service(repository)

    assert service.analyze_repo("ts_app").status == "queued"
    assert service.get_analysis_status("ts_app").state == "failed"
    assert repository.code_graphs == {}


def test_analysis_fails_instead_of_storing_empty_graph_when_source_root_is_stale():
    repos = {"ts_app": {**REPOS["ts_app"], "source_roots": ["no_such_dir"]}}
    repository = FakeCodeGraphRepository()
    service = CodeGraphService(
        repository, FakeGitHubClient(make_tarball("ts_app", SHA)), repos, run_in_background=_run_now
    )

    service.analyze_repo("ts_app")

    assert service.get_analysis_status("ts_app").error == "ValueError"
    assert repository.code_graphs == {}


def test_get_analysis_status_raises_not_found_for_unconfigured_repo():
    with pytest.raises(NotFoundError):
        _service(FakeCodeGraphRepository()).get_analysis_status("unknown")


def test_get_impact_walks_both_directions():
    impact = _service(_analyzed()).get_impact("ts_app", "src/lib/api.ts", 1)

    assert "src/app/about/page.tsx" in impact.upstream
    assert "src/store/index.ts" in impact.downstream
    assert impact.hops == 1


def test_get_impact_raises_not_found_for_unknown_file():
    with pytest.raises(NotFoundError):
        _service(_analyzed()).get_impact("ts_app", "src/nope.ts", 1)


def test_seed_repos_stores_valid_configs():
    repository = FakeCodeGraphRepoConfigRepository()

    CodeGraphRepoConfigService(repository).seed(REPOS)

    assert CodeGraphRepoConfigService(repository).load() == dict(sorted(REPOS.items()))


@pytest.mark.parametrize("repos", [[{"owner": "x"}], {"ok": REPOS["ts_app"], "broken": {"owner": "x"}}])
def test_seed_repos_writes_nothing_when_any_config_is_invalid(repos):
    repository = FakeCodeGraphRepoConfigRepository({"existing": REPOS["ts_app"]})

    with pytest.raises(ValueError):
        CodeGraphRepoConfigService(repository).seed(repos)

    assert repository.replace_calls == 0
    assert list(repository.repos) == ["existing"]
