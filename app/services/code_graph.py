"""코드 그래프 분석·조회 (code_graph_design/README.md). tarball → 파일·심볼·import 2-pass 해석 → CodeGraph."""

from __future__ import annotations

import hashlib
import json
import logging
import posixpath
import re
import threading
import tomllib
from collections.abc import Callable, Iterator
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any

from app.clients.github import GitHubClient, extract_tarball, read_commit_sha
from app.core.cache import CODE_GRAPH_TTL, SERVICE_GRAPH_CACHE_KEY, cache
from app.core.exceptions import NotFoundError
from app.models.code_graph import (
    CodeGraph,
    CodeGraphRepo,
    EdgeKind,
    InboundRoute,
    OutboundCall,
    SourceFile,
    stronger_kind,
)
from app.repositories.code_graph import CodeGraphRepository
from app.repositories.code_graph_repo_config import CodeGraphRepoConfigRepository
from app.schemas.code_graph import (
    AnalysisStatusPublic,
    CodeGraphAnalysis,
    CodeGraphPublic,
    CodeGraphSummary,
    ImpactPublic,
)
from app.services.code_graph_calls import (
    extract_nest_routes,
    extract_next_routes,
    extract_python_calls,
    extract_spring_routes,
    extract_ts_calls,
)

if TYPE_CHECKING:
    from tree_sitter import Node, Parser  # 네이티브 모듈은 분석할 때만 읽는다 (G-24)

logger = logging.getLogger(__name__)

KEEP_PER_REPO = 5  # 저장소마다 최신 분석 5개만 남긴다
# 추출·진단 규칙을 바꾸면 올린다. 같은 commit 의 저장된 결과가 옛 규칙으로 남지 않게 다시 분석한다 (G-26)
ANALYZER_VERSION = 2

RunInBackground = Callable[..., Any]

_LANGUAGE_BY_SUFFIX = {".py": "python", ".ts": "typescript", ".tsx": "tsx", ".kt": "kotlin"}
# Dockerfile 이 이미지를 만들 때 받아 두는 문법 목록과 같아야 한다 (tests/test_dockerfile.py)
GRAMMARS = tuple(sorted(set(_LANGUAGE_BY_SUFFIX.values())))
_MANIFESTS = frozenset({"pyproject.toml", "requirements.txt", "package.json", "build.gradle"})
_SKIPPED_DIRS = frozenset(
    {
        "node_modules",
        ".git",
        ".venv",
        "__pycache__",
        ".next",
        "dist",
        "build",
        "coverage",
        "public",
        "tests",
        "test",
        "__tests__",
        "e2e",
    }
)
_SKIPPED_SUFFIXES = (".spec.ts", ".test.ts", ".test.tsx", ".d.ts")
_TS_DEFINITIONS = {
    "function_declaration",
    "class_declaration",
    "interface_declaration",
    "type_alias_declaration",
    "enum_declaration",
    "method_definition",
}
_DEFINITION_TYPES = {
    "python": {"function_definition", "class_definition"},
    "typescript": _TS_DEFINITIONS,
    "tsx": _TS_DEFINITIONS,
    "kotlin": {"class_declaration", "object_declaration", "function_declaration"},
}
# posts.service 처럼 점이 든 이름이 있어 with_suffix 가 아니라 이름 뒤에 붙인다
_TS_CANDIDATE_SUFFIXES = ("", ".ts", ".tsx", "/index.ts", "/index.tsx")
_TS_BARREL_NAMES = {"index.ts", "index.tsx"}
_PYTHON_REQUIREMENT = re.compile(r"([A-Za-z0-9._-]+)(?:\[[^\]]*\])?\s*(.*)")
_GRADLE_PLUGIN = re.compile(r"""^\s*id\s*\(?\s*['"]([^'"]+)['"]\s*\)?(?:\s*version\s*\(?\s*['"]([^'"]+)['"])?""", re.M)
_GRADLE_DEPENDENCY = re.compile(
    r"""^\s*(\w+)\s*\(?\s*(?:platform\s*\(\s*)?['"]([^'":\s]+:[^'":\s]+)(?::([^'"\s]+))?['"]""", re.M
)


class AnalysisTracker:
    """저장소별 분석 상태와 동시 실행 제한. uvicorn 워커 1개 전제의 프로세스 안 상태다.

    tarball 을 메모리에 올리는 분석이 겹치면 검색·KIS 와 같은 프로세스가 메모리에 눌리므로 한 번에 하나만 돌린다 (G-25).
    앱 수명과 같은 객체라 lifespan 이 만든다.
    """

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._states: dict[str, AnalysisStatusPublic] = {}
        self.exclusive = threading.Lock()

    def try_start(self, repo: str, commit_sha: str) -> bool:
        """이 저장소가 이미 돌고 있으면 False."""
        with self._lock:
            current = self._states.get(repo)
            if current is not None and current.state == "running":
                return False
            self._states[repo] = self._status(repo, "running", commit_sha)
            return True

    def finish(self, repo: str, commit_sha: str) -> None:
        with self._lock:
            self._states[repo] = self._status(repo, "done", commit_sha)

    def fail(self, repo: str, commit_sha: str, error: str) -> None:
        with self._lock:
            self._states[repo] = self._status(repo, "failed", commit_sha, error)

    def get(self, repo: str) -> AnalysisStatusPublic:
        with self._lock:
            return self._states.get(repo) or AnalysisStatusPublic(repo=repo, state="idle")

    @staticmethod
    def _status(repo: str, state: str, commit_sha: str, error: str | None = None) -> AnalysisStatusPublic:
        return AnalysisStatusPublic(
            repo=repo, state=state, commit_sha=commit_sha, updated_at=datetime.now(UTC), error=error
        )


class CodeGraphRepoConfigService:
    """분석 대상 저장소 설정(Mongo code_graph_repos) 읽기와 넣기 (G-35). 비어 있으면 기능이 꺼진다."""

    def __init__(self, repository: CodeGraphRepoConfigRepository) -> None:
        self._repository = repository

    def load(self) -> dict[str, dict[str, Any]]:
        return self._repository.list_all()

    def seed(self, repos: Any) -> None:
        """저장소 키 → 설정 객체를 통째로 넣는다. 없는 저장소는 지워진다. 깨진 설정이 DB 에 들어가지 않게 전부 먼저 검사한다."""
        if not isinstance(repos, dict):
            raise ValueError("code graph repos must be a JSON object keyed by repo")
        for config in repos.values():
            CodeGraphRepo.model_validate(config)
        self._repository.replace_all(repos)


class CodeGraphService:
    """저장소 코드 그래프의 분석·저장·조회. 저장소 목록은 Mongo 원문을 받아 여기서 검증한다.

    분석은 응답을 늦추지 않도록 run_in_background 로 넘긴다 (dependencies.py 가 BackgroundTasks.add_task 를,
    CLI 는 바로 실행하는 함수를 준다).
    """

    def __init__(
        self,
        repository: CodeGraphRepository,
        github_client: GitHubClient,
        repos: dict[str, Any],
        run_in_background: RunInBackground,
        tracker: AnalysisTracker | None = None,
    ) -> None:
        self._repository = repository
        self._github_client = github_client
        self._repos = repos
        self._run_in_background = run_in_background
        self._tracker = tracker or AnalysisTracker()

    def analyze_repo(self, repo: str) -> CodeGraphAnalysis:
        """tarball 앞부분으로 commit 을 확인하고 stored·running·queued 중 하나를 돌려준다 (G-25, G-26).

        같은 commit·같은 분석기·같은 설정의 결과가 있으면 stored, 이 저장소가 이미 돌고 있으면 running, 아니면 전체
        다운로드와 분석을 백그라운드로 넘기고 queued 다. 결과는 get_analysis_status 로 본다. 설정에 없는 repo 는
        NotFoundError, commit 확인 실패는 ServiceUnavailableError, 설정 형식 오류는 ValidationError.
        """
        repo_spec = self._get_repo_spec(repo)
        fingerprint = _fingerprint(repo_spec)
        commit_sha = self._github_client.fetch_commit_sha(repo_spec.owner, repo_spec.name, repo_spec.ref)
        stored = self._repository.get_by_commit(repo, commit_sha)
        if stored is not None and stored.fingerprint == fingerprint:
            return CodeGraphAnalysis(repo=repo, commit_sha=commit_sha, status="stored")
        if not self._tracker.try_start(repo, commit_sha):
            return CodeGraphAnalysis(repo=repo, commit_sha=commit_sha, status="running")
        self._run_in_background(self._store_code_graph, repo, repo_spec, fingerprint, commit_sha)
        return CodeGraphAnalysis(repo=repo, commit_sha=commit_sha, status="queued")

    def get_analysis_status(self, repo: str) -> AnalysisStatusPublic:
        """이 프로세스에서의 마지막 분석 상태. 재시작하면 idle 로 돌아간다. 설정에 없는 repo 는 NotFoundError."""
        if repo not in self._repos:
            raise NotFoundError(f"code graph repo {repo} is not configured")
        return self._tracker.get(repo)

    def get_impact(self, repo: str, file: str, hops: int, *, runtime_only: bool = False) -> ImpactPublic:
        """파일 하나의 N-hop 영향 반경. 저장소·분석 결과·파일이 없으면 NotFoundError."""
        report = self._get_latest(repo).impact(file, hops, runtime_only=runtime_only)
        if report is None:
            raise NotFoundError(f"{file} is not in the code graph of {repo}")
        return ImpactPublic.model_validate(report.model_dump())

    def list_code_graphs(self) -> list[CodeGraphSummary]:
        """설정된 저장소 중 분석 결과가 있는 것마다 최신 commit 과 진단 건수. 저장소 키 순서다."""
        summaries = []
        for repo in self._repos:
            code_graph = self._repository.get_latest(repo)
            if code_graph is None:
                continue
            diagnostics = code_graph.diagnostics
            summaries.append(
                CodeGraphSummary(
                    repo=repo,
                    commit_sha=code_graph.commit_sha,
                    analyzed_at=code_graph.analyzed_at,
                    violation_count=len(diagnostics.layer_violations),
                    type_only_violation_count=sum(each.type_only for each in diagnostics.layer_violations),
                    cycle_count=len(diagnostics.cycles),
                    orphan_count=len(diagnostics.orphan_files),
                    hub_count=len(diagnostics.hub_files),
                )
            )
        return summaries

    def get_code_graph(self, repo: str) -> CodeGraphPublic:
        """최신 분석의 그래프와 진단. 10분 캐시한다. 설정에 없거나 분석 결과가 없으면 NotFoundError."""
        cache_key = _cache_key(repo)
        cached: CodeGraphPublic | None = cache.get(cache_key)
        if cached is not None:
            return cached
        code_graph = self._get_latest(repo)
        data = code_graph.model_dump(exclude={"allowed_edges"})
        data["graph"] = {"nodes": data.pop("nodes"), "edges": data.pop("edges")}
        result = CodeGraphPublic.model_validate(data)
        cache.set(cache_key, result, CODE_GRAPH_TTL)
        return result

    def get_code_graph_context(self, repo: str) -> str:
        """최신 분석의 context 마크다운. 설정에 없거나 분석 결과가 없으면 NotFoundError."""
        return self._get_latest(repo).render_context()

    def _get_repo_spec(self, repo: str) -> CodeGraphRepo:
        if repo not in self._repos:
            raise NotFoundError(f"code graph repo {repo} is not configured")
        return CodeGraphRepo.model_validate(self._repos[repo])

    def _get_latest(self, repo: str) -> CodeGraph:
        if repo not in self._repos:
            raise NotFoundError(f"code graph repo {repo} is not configured")
        code_graph = self._repository.get_latest(repo)
        if code_graph is None:
            raise NotFoundError(f"code graph of {repo} has not been analyzed")
        return code_graph

    def _store_code_graph(self, repo: str, repo_spec: CodeGraphRepo, fingerprint: str, commit_sha: str) -> None:
        # 응답을 보낸 뒤 실행된다. 실패하면 이전 결과를 그대로 두고 상태에 남긴다 (G-09, G-25).
        # 앞서 확인한 뒤 ref 가 움직였을 수 있어 받은 tarball 의 SHA 를 기준으로 저장한다
        try:
            with self._tracker.exclusive:
                tarball = self._github_client.download_tarball(repo_spec.owner, repo_spec.name, repo_spec.ref)
                commit_sha = read_commit_sha(tarball)
                with extract_tarball(tarball, lambda path: _is_wanted(path, repo_spec)) as root:
                    parsed = _parse_repo(root, repo_spec)
                    tech_stack = _read_tech_stack(root)
                if not parsed.files:
                    # 소스 루트 설정이 낡았을 때다. 빈 그래프를 "위반 0" 으로 저장하면 안 된다
                    raise ValueError(f"no source files under {repo_spec.source_roots}")
                code_graph = CodeGraph.build(
                    repo=repo,
                    commit_sha=commit_sha,
                    fingerprint=fingerprint,
                    analyzed_at=datetime.now(UTC),
                    tech_stack=tech_stack,
                    repo_spec=repo_spec,
                    files=parsed.files,
                    imports=parsed.imports,
                    outbound_calls=parsed.outbound_calls,
                    inbound_routes=parsed.inbound_routes,
                )
                self._repository.upsert(code_graph)
                self._repository.delete_stale(repo, keep=KEEP_PER_REPO)
        except Exception as exc:  # 백그라운드 경계
            logger.warning("코드 그래프 분석 실패 (%s@%s): %s", repo, commit_sha[:7], exc, exc_info=True)
            self._tracker.fail(repo, commit_sha, type(exc).__name__)
            return
        except BaseException as exc:  # 네이티브 파서의 panic 은 BaseException 이다. running 에 묶이지 않게 한다
            self._tracker.fail(repo, commit_sha, type(exc).__name__)
            raise
        self._tracker.finish(repo, commit_sha)
        cache.delete(_cache_key(repo))
        cache.delete(SERVICE_GRAPH_CACHE_KEY)
        logger.info(
            "코드 그래프 저장 (%s@%s): 파일 %s, 엣지 %s, 위반 %s, 호출 지점 %s",
            repo,
            commit_sha[:7],
            len(code_graph.nodes),
            len(code_graph.edges),
            len(code_graph.diagnostics.layer_violations),
            len(code_graph.outbound_calls),
        )


def _cache_key(repo: str) -> str:
    return f"code_graph:{repo}"


def _fingerprint(repo_spec: CodeGraphRepo) -> str:
    """분석기 버전과 저장소 설정의 해시. 파서나 규칙이 바뀌면 같은 commit 도 다시 분석하게 한다 (G-26)."""
    return hashlib.sha256(f"{ANALYZER_VERSION}:{repo_spec.model_dump_json()}".encode()).hexdigest()[:16]


def _is_wanted(path: str, repo_spec: CodeGraphRepo) -> bool:
    """분석에 쓰는 파일만 푼다: 저장소 루트의 매니페스트와 소스 루트 아래의 언어 파일."""
    if path in _MANIFESTS:
        return True
    return posixpath.splitext(path)[1] in _LANGUAGE_BY_SUFFIX and any(
        path.startswith(source_root.rstrip("/") + "/") for source_root in repo_spec.source_roots
    )


@dataclass
class _ParsedFile:
    """Pass 1 결과. imports 는 (언어별 원문, 종류)다: Python (모듈, 이름들), TS 경로 문자열, Kotlin (이름, 와일드카드 여부)."""

    path: str
    language: str
    symbols: list[str] = field(default_factory=list)
    imports: list[tuple[Any, EdgeKind]] = field(default_factory=list)
    package: str = ""  # Kotlin
    top_level: list[str] = field(default_factory=list)  # Kotlin 최상위 선언 이름
    references: set[str] = field(default_factory=set)  # Kotlin 파일이 쓰는 이름. 같은 패키지 참조에는 import 가 없다
    is_barrel: bool = False


@dataclass
class _ParsedRepo:
    files: list[SourceFile]
    imports: dict[tuple[str, str], EdgeKind]
    outbound_calls: list[OutboundCall]
    inbound_routes: list[InboundRoute]


def _parse_repo(root: Path, repo_spec: CodeGraphRepo) -> _ParsedRepo:
    """Pass 1 이 파일·심볼·Kotlin 패키지 인덱스와 호출 지점을, Pass 2 가 import 를 해석해 내부 엣지를 만든다.

    외부 패키지는 버린다. 호출 지점은 call_sites 에 걸린 파일에서, inbound 엔드포인트는 프레임워크 설정대로 뽑는다.
    문법 패키지는 첫 사용 때 네이티브 라이브러리를 읽으므로 여기서 import 한다. 파일 맨 위에 두면 그 실패가 앱 기동
    실패가 되어 검색·KIS 까지 함께 멈춘다 (G-24).
    """
    from tree_sitter_language_pack import get_parser

    parsers: dict[str, Parser] = {}
    parsed = []
    outbound_calls: list[OutboundCall] = []
    inbound_routes: list[InboundRoute] = []
    for path in sorted(_iter_source_paths(root, repo_spec.source_roots)):
        language = _LANGUAGE_BY_SUFFIX[posixpath.splitext(path)[1]]
        if language not in parsers:
            parsers[language] = get_parser(language)
        root_node = parsers[language].parse((root / path).read_bytes()).root_node
        parsed.append(_parse_file(path, language, root_node))
        if repo_spec.route_framework == "nest" and language in ("typescript", "tsx"):
            inbound_routes += extract_nest_routes(path, root_node)
        elif repo_spec.route_framework == "spring" and language == "kotlin":
            inbound_routes += extract_spring_routes(path, root_node)
        if repo_spec.is_call_site(path):
            if language == "python":
                outbound_calls += extract_python_calls(path, root_node)
            elif language in ("typescript", "tsx"):
                outbound_calls += extract_ts_calls(path, root_node)
                if repo_spec.route_root is not None:
                    inbound_routes += extract_next_routes(path, root_node, repo_spec.route_root)

    paths = {each.path for each in parsed}
    symbol_files: dict[str, str] = {}  # Kotlin 패키지.심볼 → 파일
    package_files: dict[str, list[str]] = {}  # Kotlin 패키지 → 파일들
    for each in parsed:
        if each.language == "kotlin":
            package_files.setdefault(each.package, []).append(each.path)
            symbol_files.update({f"{each.package}.{name}": each.path for name in each.top_level})

    imports: dict[tuple[str, str], EdgeKind] = {}

    def add_edge(source: str, target: str, kind: EdgeKind) -> None:
        if source != target:
            imports[(source, target)] = stronger_kind(kind, imports.get((source, target), kind))

    for each in parsed:
        for spec, kind in each.imports:
            if each.language == "python":
                targets = _resolve_python(each.path, *spec, paths)
            elif each.language == "kotlin":
                targets = _resolve_kotlin(*spec, symbol_files, package_files)
            else:
                targets = _resolve_ts(each.path, spec, paths)
            for target in targets:
                add_edge(each.path, target, kind)
        if each.language == "kotlin":
            # 같은 패키지의 선언은 import 없이 쓴다. 안 보면 그 패키지 안의 의존이 통째로 빠진다
            for name in each.references:
                target = symbol_files.get(f"{each.package}.{name}")
                if target is not None:
                    add_edge(each.path, target, "static")
    files = [SourceFile(path=each.path, symbols=each.symbols, is_barrel=each.is_barrel) for each in parsed]
    return _ParsedRepo(files=files, imports=imports, outbound_calls=outbound_calls, inbound_routes=inbound_routes)


def _iter_source_paths(root: Path, source_roots: list[str]) -> Iterator[str]:
    """소스 루트 아래 분석 대상 파일의 저장소 루트 기준 경로. 제외 디렉터리는 저장소 루트 기준으로 본다."""
    seen: set[str] = set()
    for source_root in source_roots:
        for file in (root / source_root).rglob("*"):
            relative = file.relative_to(root).as_posix()
            if (
                file.suffix in _LANGUAGE_BY_SUFFIX
                and not relative.endswith(_SKIPPED_SUFFIXES)
                and _SKIPPED_DIRS.isdisjoint(relative.split("/")[:-1])
                and relative not in seen
                and file.is_file()
            ):
                seen.add(relative)
                yield relative


def _parse_file(path: str, language: str, root_node: Node) -> _ParsedFile:
    parsed = _ParsedFile(path=path, language=language)
    is_ts = language in ("typescript", "tsx")
    value_names = _ts_value_names(root_node) if is_ts else None
    for node in _walk(root_node):
        if node.type in _DEFINITION_TYPES[language]:
            parsed.symbols.append(_definition_name(node, language))
            if language == "kotlin" and node.parent is not None and node.parent.type == "source_file":
                parsed.top_level.append(parsed.symbols[-1])
        elif language == "python" and node.type in ("import_statement", "import_from_statement"):
            kind: EdgeKind = "type" if _under_type_checking(node) else "static"
            parsed.imports += [(spec, kind) for spec in _python_imports(node)]
        elif is_ts and node.type in ("import_statement", "export_statement"):
            source = node.child_by_field_name("source")
            if source is not None:
                parsed.imports.append((_text(source).strip("'\"`"), _ts_statement_kind(node, value_names)))
            if node.type == "export_statement":
                parsed.symbols += _arrow_bindings(node.child_by_field_name("declaration"))
        elif is_ts and node.type == "call_expression":
            source = _dynamic_import_source(node)
            if source is not None:
                parsed.imports.append((source, "dynamic"))
        elif is_ts and node.type == "lexical_declaration":
            if node.parent is not None and node.parent.type == "program":
                parsed.symbols += _arrow_bindings(node)
        elif language == "kotlin" and node.type == "package_header":
            parsed.package = _kotlin_name(node)
        elif language == "kotlin" and node.type == "import_header":
            parsed.imports.append(
                ((_kotlin_name(node), any(child.type == "wildcard_import" for child in node.children)), "static")
            )
        elif language == "kotlin" and node.type == "property_declaration" and _is_top_level_kotlin(node):
            parsed.top_level += _kotlin_property_names(node)
    if language == "kotlin":
        parsed.references = _kotlin_references(root_node)
    parsed.is_barrel = posixpath.basename(path) == "__init__.py" or _is_ts_barrel(path, root_node)
    return parsed


def _under_type_checking(node: Node) -> bool:
    """`if TYPE_CHECKING:` 블록 안의 import. 실행 때 돌지 않는다."""
    current = node.parent
    while current is not None:
        if current.type == "if_statement":
            condition = current.child_by_field_name("condition")
            if condition is not None and "TYPE_CHECKING" in _text(condition):
                return True
        current = current.parent
    return False


def _ts_value_names(root_node: Node) -> set[str] | None:
    """값 위치에 쓰인 이름들. 데코레이터가 있는 파일은 None 이다.

    타입으로만 쓴 import 는 컴파일러가 지운다. 그런데 emitDecoratorMetadata 를 켠 NestJS 는 생성자 타입 import 를 실행
    때도 남기므로, 데코레이터가 있는 파일은 이름 쓰임으로 판단하지 않고 `type` 키워드만 믿는다.
    """
    names: set[str] = set()
    for node in _walk(root_node):
        if node.type == "decorator":
            return None
        if node.type in ("identifier", "shorthand_property_identifier") and not _has_ancestor(
            node, ("import_statement", "type_query", "nested_type_identifier")
        ):
            names.add(_text(node))
    return names


def _has_ancestor(node: Node, types: tuple[str, ...]) -> bool:
    current = node.parent
    while current is not None:
        if current.type in types:
            return True
        current = current.parent
    return False


def _ts_statement_kind(node: Node, value_names: set[str] | None) -> EdgeKind:
    """import·export 문 하나의 종류. `import type`, 전부 `type` 인 지정자, 타입으로만 쓴 이름은 type 이다 (G-27)."""
    if any(child.type == "type" for child in node.children):
        return "type"
    if node.type == "export_statement":
        clause = next((child for child in node.named_children if child.type == "export_clause"), None)
        specifiers = clause.named_children if clause is not None else []
        is_all_type = bool(specifiers) and all(any(c.type == "type" for c in each.children) for each in specifiers)
        return "type" if is_all_type else "static"
    clause = next((child for child in node.named_children if child.type == "import_clause"), None)
    if clause is None:
        return "static"  # `import './x'` 는 부수 효과가 목적이다
    names: list[str] = []
    for child in clause.named_children:
        if child.type == "identifier":
            names.append(_text(child))
        elif child.type == "namespace_import":
            names += [_text(each) for each in child.named_children if each.type == "identifier"]
        elif child.type == "named_imports":
            for specifier in child.named_children:
                if specifier.type != "import_specifier" or any(c.type == "type" for c in specifier.children):
                    continue
                local = specifier.child_by_field_name("alias") or specifier.child_by_field_name("name")
                names.append(_text(local))
    if not names:
        return "type"
    if value_names is None:
        return "static"
    return "static" if any(name in value_names for name in names) else "type"


def _is_top_level_kotlin(node: Node) -> bool:
    return node.parent is not None and node.parent.type == "source_file"


def _kotlin_property_names(node: Node) -> list[str]:
    declarations = (child for child in node.children if child.type == "variable_declaration")
    return [_text(part) for each in declarations for part in each.children if part.type == "simple_identifier"]


def _kotlin_references(root_node: Node) -> set[str]:
    """파일이 쓰는 이름들(`type_identifier`, `simple_identifier`). package·import 문은 빼고 본다."""
    names: set[str] = set()
    for child in root_node.children:
        if child.type in ("package_header", "import_list", "import_header"):
            continue
        names |= {_text(node) for node in _walk(child) if node.type in ("type_identifier", "simple_identifier")}
    return names


def _walk(node: Node) -> Iterator[Node]:
    """전위 순회. 심볼이 정의 순서로 나온다."""
    stack = [node]
    while stack:
        current = stack.pop()
        yield current
        stack.extend(reversed(current.children))


def _text(node: Node) -> str:
    return node.text.decode("utf-8", "replace") if node.text is not None else ""


def _definition_name(node: Node, language: str) -> str:
    if language == "kotlin":
        # Kotlin 문법에는 name 필드가 없다. 클래스·object 는 type_identifier, 함수는 simple_identifier 다
        wanted = "simple_identifier" if node.type == "function_declaration" else "type_identifier"
        return next((_text(child) for child in node.children if child.type == wanted), "")
    name = node.child_by_field_name("name")
    return _text(name) if name is not None else ""


def _arrow_bindings(node: Node | None) -> list[str]:
    """최상위 const 의 arrow_function 바인딩 이름(함수형 컴포넌트·훅). export 된 선언도 여기로 온다."""
    if node is None or node.type != "lexical_declaration":
        return []
    names = []
    for declarator in node.children:
        value = declarator.child_by_field_name("value") if declarator.type == "variable_declarator" else None
        name = declarator.child_by_field_name("name") if value is not None else None
        if value is not None and value.type == "arrow_function" and name is not None:
            names.append(_text(name))
    return names


def _python_imports(node: Node) -> list[tuple[str, list[str]]]:
    """(모듈, import 한 이름들). `import a.b` 는 이름 없이, 상대 import 는 모듈 앞의 점을 그대로 둔다."""
    names = []
    for child in node.children_by_field_name("name"):
        target = child.child_by_field_name("name") if child.type == "aliased_import" else child
        names.append(_text(target) if target is not None else "")
    if node.type == "import_statement":
        return [(name, []) for name in names]
    module = node.child_by_field_name("module_name")
    return [(_text(module), names)] if module is not None else []


def _kotlin_name(node: Node) -> str:
    identifier = next((child for child in node.children if child.type == "identifier"), None)
    if identifier is None:
        return ""
    return ".".join(_text(part) for part in identifier.children if part.type == "simple_identifier")


def _dynamic_import_source(node: Node) -> str | None:
    """`import('x')`(next/dynamic 포함)의 경로. 안 보면 지연 로딩 파일이 orphan 으로 나온다. 값을 모르는 템플릿 리터럴은 None."""
    function = node.child_by_field_name("function")
    arguments = node.child_by_field_name("arguments")
    if function is None or function.type != "import" or arguments is None or not arguments.named_children:
        return None
    source = arguments.named_children[0]
    return _text(source).strip("'\"") if source.type == "string" else None


def _is_ts_barrel(path: str, root_node: Node) -> bool:
    """`export ... from` 만 있는 index.ts(x). 다른 문장이 하나라도 있으면 배럴이 아니다."""
    if posixpath.basename(path) not in _TS_BARREL_NAMES:
        return False
    statements = [child for child in root_node.named_children if child.type != "comment"]
    return bool(statements) and all(
        child.type == "export_statement" and child.child_by_field_name("source") is not None for child in statements
    )


def _resolve_python(path: str, module: str, names: list[str], paths: set[str]) -> list[str]:
    """`from app.a.b import c` 는 app/a/b/c.py → app/a/b/c/__init__.py → app/a/b.py → app/a/b/__init__.py 순으로 찾는다."""
    stripped = module.lstrip(".")
    level = len(module) - len(stripped)
    if level:
        # 상대 import 는 현재 패키지(파일의 디렉터리) 기준이다. 점 하나가 늘 때마다 한 단계 위로 간다
        package = posixpath.dirname(path)
        for _ in range(level - 1):
            package = posixpath.dirname(package)
        base = posixpath.join(package, stripped.replace(".", "/")) if stripped else package
    else:
        base = stripped.replace(".", "/")
    module_candidates = (f"{base}.py", f"{base}/__init__.py")
    targets = []
    for name in names or [""]:
        candidates = (f"{base}/{name}.py", f"{base}/{name}/__init__.py") if name else ()
        target = next((each for each in (*candidates, *module_candidates) if each in paths), None)
        if target is not None:
            targets.append(target)
    return targets


def _resolve_ts(path: str, source: str, paths: set[str]) -> list[str]:
    """`@/` → `src/`, `src/` 는 그대로(NestJS tsconfig baseUrl), `./`·`../` 는 파일 기준. 그 밖은 외부 패키지다."""
    if source.startswith("@/"):
        base = "src/" + source[2:]
    elif source.startswith("src/"):
        base = source
    elif source.startswith("."):
        base = posixpath.normpath(posixpath.join(posixpath.dirname(path), source))
    else:
        return []
    return next(([base + suffix] for suffix in _TS_CANDIDATE_SUFFIXES if base + suffix in paths), [])


def _resolve_kotlin(
    name: str, is_wildcard: bool, symbol_files: dict[str, str], package_files: dict[str, list[str]]
) -> list[str]:
    """이 저장소가 선언한 패키지·심볼로만 잇는다(내부 접두 import). `.*` 는 패키지의 모든 파일이다.

    `패키지.클래스.안쪽` 처럼 더 긴 이름은 바깥 선언이 있는 파일로 잇는다.
    """
    if is_wildcard and name in package_files:
        return package_files[name]
    parts = name.split(".")
    for end in range(len(parts), 1, -1):
        target = symbol_files.get(".".join(parts[:end]))
        if target is not None:
            return [target]
    return []


def _read_tech_stack(root: Path) -> list[str]:
    """매니페스트에 선언된 의존성 이름·버전 (G-12). 실제 import 와 맞추지 않는다.

    pyproject.toml [project].dependencies, requirements.txt, package.json dependencies(devDependencies 제외),
    build.gradle plugins·dependencies(test* 구성 제외).
    """
    entries: list[str] = []
    pyproject = root / "pyproject.toml"
    if pyproject.is_file():
        project = tomllib.loads(pyproject.read_text(encoding="utf-8")).get("project", {})
        entries += [_python_requirement(each) for each in project.get("dependencies", [])]
    requirements = root / "requirements.txt"
    if requirements.is_file():
        for line in requirements.read_text(encoding="utf-8").splitlines():
            requirement = line.split("#", 1)[0].strip()
            if requirement and not requirement.startswith("-"):
                entries.append(_python_requirement(requirement))
    package_json = root / "package.json"
    if package_json.is_file():
        dependencies = json.loads(package_json.read_text(encoding="utf-8")).get("dependencies", {})
        entries += [f"{name} {version}" for name, version in dependencies.items()]
    build_gradle = root / "build.gradle"
    if build_gradle.is_file():
        text = build_gradle.read_text(encoding="utf-8")
        entries += [f"{plugin} {version}".strip() for plugin, version in _GRADLE_PLUGIN.findall(text)]
        entries += [
            f"{coordinate} {version}".strip()
            for configuration, coordinate, version in _GRADLE_DEPENDENCY.findall(text)
            if not configuration.startswith("test")
        ]
    return list(dict.fromkeys(entries))


def _python_requirement(requirement: str) -> str:
    """`uvicorn[standard]==0.54.0; python_version>"3"` → `uvicorn 0.54.0`. 범위 지정은 연산자째 둔다."""
    match = _PYTHON_REQUIREMENT.match(requirement.split(";", 1)[0].strip())
    if match is None:
        return requirement
    name, specifier = match.groups()
    return f"{name} {specifier.removeprefix('==').strip()}".strip()
