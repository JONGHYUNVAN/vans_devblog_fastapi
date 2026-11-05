"""GitHub 공개 저장소 tarball 받기·commit SHA 읽기·임시 디렉터리에 풀기 (code_graph_design/README.md G-10·G-11).

이미지에 git 이 없어 codeload 의 tar.gz 를 받는다. 네 저장소가 모두 public 이라 토큰을 쓰지 않는다.
"""

import io
import re
import tarfile
import tempfile
from collections.abc import Callable, Iterable, Iterator
from contextlib import contextmanager
from pathlib import Path

import httpx2

from app.core.exceptions import ServiceUnavailableError

CODELOAD_URL = "https://codeload.github.com/{owner}/{name}/tar.gz/{ref}"
_SHA_PATTERN = re.compile(r"[0-9a-f]{40}")


class GitHubClient:
    """codeload tarball 다운로드. 타임아웃과 크기 상한을 항상 건다."""

    def __init__(self, timeout_s: int, max_bytes: int, transport: httpx2.BaseTransport | None = None) -> None:
        self._timeout_s = timeout_s
        self._max_bytes = max_bytes
        self._transport = transport

    def fetch_commit_sha(self, owner: str, name: str, ref: str) -> str:
        """tarball 앞부분만 받아 ref 의 commit SHA 를 읽고 연결을 끊는다. pax 전역 헤더가 스트림 맨 앞이라 가능하다.

        이미 분석한 commit 인지 보려고 tarball 전체(vans_story 는 30MB)를 메모리에 올리지 않기 위해서다(G-25).
        네트워크 오류·2xx 가 아닌 응답·SHA 를 못 읽으면 ServiceUnavailableError.
        """
        url = CODELOAD_URL.format(owner=owner, name=name, ref=ref)
        try:
            with (
                httpx2.Client(transport=self._transport, timeout=self._timeout_s) as client,
                client.stream("GET", url) as response,
            ):
                response.raise_for_status()
                return _read_sha(lambda: tarfile.open(fileobj=_ChunkReader(response.iter_bytes()), mode="r|gz"))
        except httpx2.HTTPError as exc:
            raise ServiceUnavailableError(f"commit lookup failed: {owner}/{name}@{ref}") from exc

    def download_tarball(self, owner: str, name: str, ref: str) -> bytes:
        """tar.gz 원본 바이트. 네트워크 오류·2xx 가 아닌 응답·크기 상한 초과는 ServiceUnavailableError."""
        url = CODELOAD_URL.format(owner=owner, name=name, ref=ref)
        chunks: list[bytes] = []
        size = 0
        try:
            with (
                httpx2.Client(transport=self._transport, timeout=self._timeout_s) as client,
                client.stream("GET", url) as response,
            ):
                response.raise_for_status()
                for chunk in response.iter_bytes():
                    size += len(chunk)
                    # 끝까지 받은 뒤 버리면 상한이 메모리를 지키지 못하므로 넘는 순간 끊는다
                    if size > self._max_bytes:
                        raise ServiceUnavailableError(f"tarball exceeds {self._max_bytes} bytes: {owner}/{name}@{ref}")
                    chunks.append(chunk)
        except httpx2.HTTPError as exc:
            raise ServiceUnavailableError(f"tarball download failed: {owner}/{name}@{ref}") from exc
        return b"".join(chunks)


class _ChunkReader:
    """바이트 조각 반복자를 tarfile 스트림 모드가 읽는 read(size) 로 감싼다."""

    def __init__(self, chunks: Iterable[bytes]) -> None:
        self._chunks = iter(chunks)
        self._pending = b""

    def read(self, size: int) -> bytes:
        while len(self._pending) < size:
            chunk = next(self._chunks, None)
            if chunk is None:
                break
            self._pending += chunk
        data, self._pending = self._pending[:size], self._pending[size:]
        return data


def read_commit_sha(tarball: bytes) -> str:
    """pax 전역 헤더 `comment` 의 전체 SHA. 최상위 디렉터리명({repo}-{ref})은 SHA 가 아니다(G-10).

    헤더가 없거나 SHA 형식이 아니면 ServiceUnavailableError.
    """
    return _read_sha(lambda: tarfile.open(fileobj=io.BytesIO(tarball), mode="r:gz"))


def _read_sha(open_archive: Callable[[], tarfile.TarFile]) -> str:
    try:
        with open_archive() as archive:
            archive.next()  # 전역 헤더는 첫 멤버를 읽을 때 pax_headers 에 들어온다
            comment = archive.pax_headers.get("comment", "")
    except (tarfile.TarError, OSError) as exc:
        raise ServiceUnavailableError("tarball is not a readable tar.gz") from exc
    if not _SHA_PATTERN.fullmatch(comment):
        raise ServiceUnavailableError("tarball has no commit SHA in its pax comment header")
    return comment


@contextmanager
def extract_tarball(tarball: bytes, wanted: Callable[[str], bool] | None = None) -> Iterator[Path]:
    """임시 디렉터리에 풀고 최상위 디렉터리({repo}-{ref})를 넘긴다. 블록이 끝나면 디렉터리째 지운다.

    wanted 는 최상위 디렉터리를 뺀 경로를 받아 풀 파일을 고른다. vans_story 는 문서 HTML 만 수백 개라 다 풀 이유가 없다.
    `filter="data"` 라 절대 경로·밖을 가리키는 링크·특수 파일은 tarfile 이 거절한다(그 예외는 그대로 던진다).
    """
    with tempfile.TemporaryDirectory(prefix="code_graph_") as workdir:
        with tarfile.open(fileobj=io.BytesIO(tarball), mode="r:gz") as archive:
            members = archive.getmembers()
            top = members[0].name.split("/", 1)[0] if members else ""
            if wanted is not None:
                members = [member for member in members if member.isfile() and wanted(member.name.split("/", 1)[-1])]
            archive.extractall(workdir, members=members, filter="data")
        root = Path(workdir) / top
        root.mkdir(exist_ok=True)
        yield root
