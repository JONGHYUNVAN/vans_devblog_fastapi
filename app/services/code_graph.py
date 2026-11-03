"""코드 그래프 분석·조회 (code_graph_design/README.md). tarball → 파일·심볼·import 2-pass 해석 → CodeGraph."""

import json
import logging
import posixpath
import re
import tomllib
from collections.abc import Callable, Iterator
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from tree_sitter import Node, Parser
from tree_sitter_language_pack import get_parser

from app.clients.github import GitHubClient, extract_tarball, read_commit_sha
from app.core.cache import CODE_GRAPH_TTL, cache
from app.core.exceptions import NotFoundError
from app.models.code_graph import CodeGraph, CodeGraphRepo, SourceFile
from app.repositories.code_graph import CodeGraphRepository
from app.schemas.code_graph import CodeGraphAnalysis, CodeGraphPublic, CodeGraphSummary

logger = logging.getLogger(__name__)

KEEP_PER_REPO = 5  # 저장소마다 최신 분석 5개만 남긴다

RunInBackground = Callable[..., Any]

_LANGUAGE_BY_SUFFIX = {".py": "python", ".ts": "typescript", ".tsx": "tsx", ".kt": "kotlin"}
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


class CodeGraphService:
    """저장소 코드 그래프의 분석·저장·조회. 저장소 목록은 CODE_GRAPH_REPOS 원문을 받아 여기서 검증한다.

    분석은 응답을 늦추지 않도록 run_in_background 로 넘긴다 (dependencies.py 가 BackgroundTasks.add_task 를,
    CLI 는 바로 실행하는 함수를 준다).
    """

    def __init__(
        self,
        repository: CodeGraphRepository,
        github_client: GitHubClient,
        repos: dict[str, Any],
        run_in_background: RunInBackground,
    ) -> None:
        self._repository = repository
        self._github_client = github_client
        self._repos = repos
        self._run_in_background = run_in_background

    def analyze_repo(self, repo: str) -> CodeGraphAnalysis:
        """tarball 을 받아 commit SHA 를 뽑고, 저장된 결과가 있으면 stored, 없으면 분석·저장을 넘기고 queued (G-11).

        설정에 없는 repo 는 NotFoundError, 다운로드·SHA 실패는 ServiceUnavailableError, 설정 형식 오류는 ValidationError.
        백그라운드 분석이 실패하면 경고 로그만 남기고 이전 결과를 그대로 둔다 (G-09).
        """
        repo_spec = self._get_repo_spec(repo)
        tarball = self._github_client.download_tarball(repo_spec.owner, repo_spec.name, repo_spec.ref)
        commit_sha = read_commit_sha(tarball)
        if self._repository.get_by_commit(repo, commit_sha) is not None:
            return CodeGraphAnalysis(repo=repo, commit_sha=commit_sha, status="stored")
        self._run_in_background(self._store_code_graph, repo, repo_spec, commit_sha, tarball)
        return CodeGraphAnalysis(repo=repo, commit_sha=commit_sha, status="queued")

    def list_code_graphs(self) -> list[CodeGraphSummary]:
        """설정된 저장소 중 분석 결과가 있는 것마다 최신 commit 과 진단 건수. 설정 순서다."""
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

    def _store_code_graph(self, repo: str, repo_spec: CodeGraphRepo, commit_sha: str, tarball: bytes) -> None:
        # 응답을 보낸 뒤 실행되므로 알릴 곳이 없다. 실패하면 이전 결과를 그대로 두고 로그만 남긴다 (G-09)
        try:
            with extract_tarball(tarball) as root:
                files, imports = _parse_repo(root, repo_spec.source_roots)
                tech_stack = _read_tech_stack(root)
            code_graph = CodeGraph.build(
                repo=repo,
                commit_sha=commit_sha,
                analyzed_at=datetime.now(UTC),
                tech_stack=tech_stack,
                repo_spec=repo_spec,
                files=files,
                imports=imports,
            )
            self._repository.upsert(code_graph)
            self._repository.delete_stale(repo, keep=KEEP_PER_REPO)
        except Exception as exc:  # 백그라운드 경계
            logger.warning("코드 그래프 분석 실패 (%s@%s): %s", repo, commit_sha[:7], exc, exc_info=True)
            return
        cache.delete(_cache_key(repo))
        logger.info(
            "코드 그래프 저장 (%s@%s): 파일 %s, 엣지 %s, 위반 %s",
            repo,
            commit_sha[:7],
            len(code_graph.nodes),
            len(code_graph.edges),
            len(code_graph.diagnostics.layer_violations),
        )


def _cache_key(repo: str) -> str:
    return f"code_graph:{repo}"


@dataclass
class _ParsedFile:
    """Pass 1 결과. imports 는 언어별 원문이다: Python (모듈, 이름들), TS 경로 문자열, Kotlin (이름, 와일드카드 여부)."""

    path: str
    language: str
    symbols: list[str] = field(default_factory=list)
    imports: list[Any] = field(default_factory=list)
    package: str = ""  # Kotlin
    top_level: list[str] = field(default_factory=list)  # Kotlin 최상위 선언 이름
    is_barrel: bool = False


def _parse_repo(root: Path, source_roots: list[str]) -> tuple[list[SourceFile], set[tuple[str, str]]]:
    """Pass 1 이 파일·심볼·Kotlin 패키지 인덱스를, Pass 2 가 import 를 해석해 내부 엣지를 만든다. 외부 패키지는 버린다."""
    parsers: dict[str, Parser] = {}
    parsed = []
    for path in sorted(_iter_source_paths(root, source_roots)):
        language = _LANGUAGE_BY_SUFFIX[posixpath.splitext(path)[1]]
        if language not in parsers:
            parsers[language] = get_parser(language)
        parsed.append(_parse_file(path, language, parsers[language].parse((root / path).read_bytes()).root_node))

    paths = {each.path for each in parsed}
    symbol_files: dict[str, str] = {}  # Kotlin 패키지.심볼 → 파일
    package_files: dict[str, list[str]] = {}  # Kotlin 패키지 → 파일들
    for each in parsed:
        if each.language == "kotlin":
            package_files.setdefault(each.package, []).append(each.path)
            symbol_files.update({f"{each.package}.{name}": each.path for name in each.top_level})

    imports: set[tuple[str, str]] = set()
    for each in parsed:
        for spec in each.imports:
            if each.language == "python":
                targets = _resolve_python(each.path, *spec, paths)
            elif each.language == "kotlin":
                targets = _resolve_kotlin(*spec, symbol_files, package_files)
            else:
                targets = _resolve_ts(each.path, spec, paths)
            imports.update((each.path, target) for target in targets if target != each.path)
    files = [SourceFile(path=each.path, symbols=each.symbols, is_barrel=each.is_barrel) for each in parsed]
    return files, imports


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
    for node in _walk(root_node):
        if node.type in _DEFINITION_TYPES[language]:
            parsed.symbols.append(_definition_name(node, language))
            if language == "kotlin" and node.parent is not None and node.parent.type == "source_file":
                parsed.top_level.append(parsed.symbols[-1])
        elif language == "python" and node.type in ("import_statement", "import_from_statement"):
            parsed.imports += _python_imports(node)
        elif language in ("typescript", "tsx") and node.type in ("import_statement", "export_statement"):
            source = node.child_by_field_name("source")
            if source is not None:
                parsed.imports.append(_text(source).strip("'\"`"))
            if node.type == "export_statement":
                parsed.symbols += _arrow_bindings(node.child_by_field_name("declaration"))
        elif language in ("typescript", "tsx") and node.type == "lexical_declaration":
            if node.parent is not None and node.parent.type == "program":
                parsed.symbols += _arrow_bindings(node)
        elif language == "kotlin" and node.type == "package_header":
            parsed.package = _kotlin_name(node)
        elif language == "kotlin" and node.type == "import_header":
            parsed.imports.append((_kotlin_name(node), any(child.type == "wildcard_import" for child in node.children)))
    parsed.is_barrel = posixpath.basename(path) == "__init__.py" or _is_ts_barrel(path, root_node)
    return parsed


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
