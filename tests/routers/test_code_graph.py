"""코드 그래프 라우터 테스트. 인증(INTERNAL_API_KEY), 상태 코드(200 stored·202 queued·404·503), 응답 계약(golden)을 확인한다.

ts_app 트리를 실제 서비스로 분석해 가짜 리포지토리에 넣는다. 시각을 고정해 golden 과 그대로 비교한다.
"""

from datetime import UTC, datetime

from fastapi import BackgroundTasks

from app.core.config import Settings, get_settings
from app.core.exceptions import ServiceUnavailableError
from app.dependencies import get_code_graph_service
from app.main import app
from app.services.code_graph import CodeGraphService
from tests.conftest import GOLDEN_DIR, load_golden
from tests.factories import make_tarball
from tests.fakes import FakeCodeGraphRepository, FakeGitHubClient

REPOS = load_golden("code_graph/repos")
KEY = {"X-Internal-Key": "secret"}
SHA = "a" * 40
ANALYZED_AT = datetime(2026, 11, 3, 12, 0, tzinfo=UTC)


def _use_service(repository: FakeCodeGraphRepository, github_client: FakeGitHubClient | None = None) -> None:
    github_client = github_client or FakeGitHubClient(make_tarball("ts_app", SHA))

    def _override(background_tasks: BackgroundTasks) -> CodeGraphService:
        return CodeGraphService(repository, github_client, REPOS, run_in_background=background_tasks.add_task)

    app.dependency_overrides[get_settings] = lambda: Settings(_env_file=None, internal_api_key="secret")
    app.dependency_overrides[get_code_graph_service] = _override


def _seeded() -> FakeCodeGraphRepository:
    repository = FakeCodeGraphRepository()
    service = CodeGraphService(
        repository, FakeGitHubClient(make_tarball("ts_app", SHA)), REPOS, run_in_background=lambda f, *a: f(*a)
    )
    service.analyze_repo("ts_app")
    code_graph = repository.code_graphs[("ts_app", SHA)]
    repository.code_graphs[("ts_app", SHA)] = code_graph.model_copy(update={"analyzed_at": ANALYZED_AT})
    return repository


def test_analyze_403_when_key_missing(client):
    _use_service(FakeCodeGraphRepository())

    response = client.post("/api/v1/code-graph/ts_app/analyze/")

    assert response.status_code == 403


def test_analyze_202_queues_then_200_for_stored_commit(client):
    repository = FakeCodeGraphRepository()
    _use_service(repository)

    queued = client.post("/api/v1/code-graph/ts_app/analyze/", headers=KEY)
    stored = client.post("/api/v1/code-graph/ts_app/analyze/", headers=KEY)

    assert queued.status_code == 202
    assert queued.json() == {"repo": "ts_app", "commit_sha": SHA, "status": "queued"}
    assert stored.status_code == 200
    assert stored.json() == {"repo": "ts_app", "commit_sha": SHA, "status": "stored"}


def test_analyze_404_for_unconfigured_repo(client):
    _use_service(FakeCodeGraphRepository())

    response = client.post("/api/v1/code-graph/vans_unknown/analyze/", headers=KEY)

    assert response.status_code == 404
    assert set(response.json()) == {"error", "message"}


def test_analyze_503_when_download_fails(client):
    _use_service(FakeCodeGraphRepository(), FakeGitHubClient(error=ServiceUnavailableError("tarball download failed")))

    response = client.post("/api/v1/code-graph/ts_app/analyze/", headers=KEY)

    assert response.status_code == 503


def test_list_code_graphs_matches_golden(client):
    _use_service(_seeded())

    response = client.get("/api/v1/code-graph/")

    assert response.status_code == 200
    assert response.json() == load_golden("code_graph_list")


def test_get_code_graph_matches_golden(client):
    _use_service(_seeded())

    response = client.get("/api/v1/code-graph/ts_app/")

    assert response.status_code == 200
    assert response.json() == load_golden("code_graph_detail")


def test_get_code_graph_404_when_not_analyzed(client):
    _use_service(FakeCodeGraphRepository())

    response = client.get("/api/v1/code-graph/kotlin_app/")

    assert response.status_code == 404


def test_get_code_graph_context_returns_markdown_matching_golden(client):
    _use_service(_seeded())

    response = client.get("/api/v1/code-graph/ts_app/context/")

    assert response.status_code == 200
    assert response.headers["content-type"] == "text/markdown; charset=utf-8"
    assert response.text == (GOLDEN_DIR / "code_graph_context.md").read_text(encoding="utf-8")
