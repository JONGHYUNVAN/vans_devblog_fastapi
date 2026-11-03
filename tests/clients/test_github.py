"""GitHubClient·tarball 함수 테스트 (다운로드·크기 상한·pax comment SHA·임시 디렉터리). HTTP 는 httpx2.MockTransport 로 대신한다."""

import io
import tarfile

import httpx2
import pytest

from app.clients.github import GitHubClient, extract_tarball, read_commit_sha
from app.core.exceptions import ServiceUnavailableError
from tests.factories import make_tarball

SHA = "0123456789abcdef0123456789abcdef01234567"


def _client(handler, max_bytes: int = 1024) -> GitHubClient:
    return GitHubClient(timeout_s=5, max_bytes=max_bytes, transport=httpx2.MockTransport(handler))


def test_download_tarball_requests_codeload_and_returns_bytes():
    requests: list[httpx2.Request] = []

    def handler(request: httpx2.Request) -> httpx2.Response:
        requests.append(request)
        return httpx2.Response(200, content=b"tarball")

    assert _client(handler).download_tarball("JONGHYUNVAN", "vans_story", "main") == b"tarball"
    [request] = requests
    assert str(request.url) == "https://codeload.github.com/JONGHYUNVAN/vans_story/tar.gz/main"


def test_download_tarball_raises_service_unavailable_on_error_status():
    with pytest.raises(ServiceUnavailableError):
        _client(lambda request: httpx2.Response(404)).download_tarball("someone", "repo", "main")


def test_download_tarball_raises_service_unavailable_on_connection_error():
    def handler(request: httpx2.Request) -> httpx2.Response:
        raise httpx2.ConnectError("refused")

    with pytest.raises(ServiceUnavailableError):
        _client(handler).download_tarball("JONGHYUNVAN", "vans_story", "main")


def test_download_tarball_stops_over_size_limit():
    with pytest.raises(ServiceUnavailableError, match="exceeds"):
        _client(lambda request: httpx2.Response(200, content=b"x" * 2048)).download_tarball(
            "JONGHYUNVAN", "vans_story", "main"
        )


def test_read_commit_sha_reads_pax_comment():
    assert read_commit_sha(make_tarball("kotlin_app", SHA)) == SHA


def test_read_commit_sha_rejects_tarball_without_comment():
    buffer = io.BytesIO()
    with tarfile.open(fileobj=buffer, mode="w:gz") as archive:
        archive.addfile(tarfile.TarInfo("repo-main"))

    with pytest.raises(ServiceUnavailableError, match="commit SHA"):
        read_commit_sha(buffer.getvalue())


def test_read_commit_sha_rejects_non_tarball():
    with pytest.raises(ServiceUnavailableError):
        read_commit_sha(b"not a tarball")


def test_extract_tarball_yields_top_directory_and_removes_it():
    with extract_tarball(make_tarball("kotlin_app", SHA)) as root:
        assert root.name == "kotlin_app-main"
        assert (root / "build.gradle").is_file()

    assert not root.exists()
