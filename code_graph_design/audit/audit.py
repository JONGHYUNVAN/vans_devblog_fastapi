"""분석기 신뢰도 재측정. 저장소별 HEAD 를 분석기에 넣고 분석기를 쓰지 않는 독립 기준과 비교한다 (README "감사").

- TS: TypeScript 컴파일러 API(ts_truth.js). 모듈 해석과 transpile 뒤 남는 import 까지 본다. vans_story 의 node_modules 가 필요하다.
- Python: 표준 라이브러리 ast.
- Kotlin: 컴파일러가 없어 최상위 선언 이름 토큰으로 근사한다.

테스트 통과는 신뢰의 근거가 못 된다. 기대값이 분석기 자신의 출력에서 나왔기 때문이다(G-23 의 버그는 모든 테스트를 통과했다).
저장소에 쓰지 않는다. 사용: python code_graph_design/audit/audit.py <저장소들이 있는 폴더>
"""

import ast
import io
import json
import logging
import re
import subprocess
import sys
import tarfile
import tempfile
from pathlib import Path

HERE = Path(__file__).parent
ROOT = HERE.parents[1]
sys.path.insert(0, str(ROOT))

from app.services.code_graph import CodeGraphService  # noqa: E402
from tests.fakes import FakeCodeGraphRepository, FakeGitHubClient  # noqa: E402

_WANTED_TOP = ("src/", "app/", "cli/", "tsconfig", "package.json", "build.gradle", "pyproject.toml", "requirements.txt")
_KT_DECL = re.compile(
    r"^(?:(?:public|internal|private|protected|abstract|open|sealed|data|enum|annotation|inline|value|const|lateinit"
    r"|suspend|operator|infix|tailrec)\s+)*(?:class|object|interface|typealias|fun|val|var)\s+(?:<[^>]+>\s+)?"
    r"(?:[A-Za-z_][\w<>,? ]*\.)?([A-Za-z_]\w*)",
    re.M,
)
_KT_PACKAGE = re.compile(r"^package\s+([\w.]+)", re.M)
_KT_IMPORT = re.compile(r"^import\s+([\w.]+)(\.\*)?(?:\s+as\s+\w+)?\s*$", re.M)
_KT_NOISE = re.compile(r'//[^\n]*|/\*.*?\*/|"(?:\\.|[^"\\])*"', re.S)


def _analyze(repo: str, repos: dict, tarball: bytes) -> dict:
    repository = FakeCodeGraphRepository()
    service = CodeGraphService(repository, FakeGitHubClient(tarball), repos, run_in_background=lambda f, *a: f(*a))
    service.analyze_repo(repo)
    status = service.get_analysis_status(repo)
    if status.state != "done":
        raise RuntimeError(f"{repo}: analysis {status.state}: {status.error}")
    return repository.get_latest(repo).model_dump(mode="json")


def _extract(tarball: bytes, target: Path) -> None:
    with tarfile.open(fileobj=io.BytesIO(tarball)) as archive:
        for member in archive.getmembers():
            name = member.name.split("/", 1)[-1]
            if member.isfile() and name.startswith(_WANTED_TOP):
                member.name = name
                archive.extract(member, target, filter="data")


def _ts_truth(tree: Path, graph: dict, blog: Path) -> tuple[set, set]:
    with tempfile.TemporaryDirectory() as workdir:
        analyzer_json, truth_json = Path(workdir) / "a.json", Path(workdir) / "t.json"
        analyzer_json.write_text(json.dumps(graph), encoding="utf-8")
        typescript = blog / "vans_story" / "node_modules" / "typescript"
        command = ["node", str(HERE / "ts_truth.js"), str(typescript), str(tree), str(analyzer_json), str(truth_json)]
        subprocess.run(command, check=True, capture_output=True, timeout=600)
        edges = json.loads(truth_json.read_text(encoding="utf-8"))["edges"]
    return {(e["source"], e["target"]) for e in edges}, {(e["source"], e["target"]) for e in edges if e["runtime"]}


def _python_truth(tree: Path, nodes: set[str]) -> tuple[set, set]:
    edges, runtime = set(), set()
    for file in sorted(path for path in nodes if path.endswith(".py")):
        parsed = ast.parse((tree / file).read_text(encoding="utf-8"))
        guarded = {
            id(child)
            for node in ast.walk(parsed)
            if isinstance(node, ast.If) and "TYPE_CHECKING" in ast.dump(node.test)
            for child in ast.walk(node)
        }
        package = file.split("/")[:-1]
        for node in ast.walk(parsed):
            if isinstance(node, ast.Import):
                pairs = [(alias.name, None) for alias in node.names]
            elif isinstance(node, ast.ImportFrom):
                module = node.module or ""
                if node.level:
                    module = ".".join(package[: len(package) - node.level + 1] + ([module] if module else []))
                pairs = [(module, alias.name) for alias in node.names]
            else:
                continue
            for module, name in pairs:
                base = module.replace(".", "/")
                candidates = ([f"{base}/{name}.py", f"{base}/{name}/__init__.py"] if name else []) + [
                    f"{base}.py",
                    f"{base}/__init__.py",
                ]
                target = next((each for each in candidates if each in nodes), None)
                if target is not None and target != file:
                    edges.add((file, target))
                    if id(node) not in guarded:
                        runtime.add((file, target))
    return edges, runtime


def _kotlin_truth(tree: Path, nodes: set[str]) -> tuple[set, set]:
    info, qualified, by_package = {}, {}, {}
    for file in nodes:
        text = (tree / file).read_text(encoding="utf-8")
        match = _KT_PACKAGE.search(text)
        package = match.group(1) if match else ""
        body = _KT_IMPORT.sub("", _KT_PACKAGE.sub("", _KT_NOISE.sub(" ", text)))
        info[file] = (package, _KT_IMPORT.findall(text), set(re.findall(r"\b[A-Za-z_]\w*\b", body)))
        for name in _KT_DECL.findall(text):
            qualified[f"{package}.{name}"] = file
            by_package.setdefault(package, {}).setdefault(name, file)
    edges = set()
    for file, (package, imports, tokens) in info.items():
        for name, wildcard in imports:
            if wildcard:
                edges |= {(file, t) for d, t in by_package.get(name, {}).items() if d in tokens and t != file}
            elif name in qualified and qualified[name] != file:
                edges.add((file, qualified[name]))
        edges |= {(file, t) for d, t in by_package.get(package, {}).items() if d in tokens and t != file}
    return edges, edges


def main() -> None:
    logging.disable(logging.WARNING)
    blog = Path(sys.argv[1]).resolve()
    repos = json.loads((HERE.parent / "repos.example.json").read_text(encoding="utf-8"))
    print(f"{'repo':24}{'edges':>7}{'truth':>7}{'missed':>8}{'extra':>7}{'type wrong':>12}{'kept wrong':>12}  basis")
    for repo in repos:
        tarball = subprocess.run(
            ["git", "-C", str(blog / repo), "archive", "--format=tar.gz", f"--prefix={repo}-main/", "HEAD"],
            check=True,
            capture_output=True,
        ).stdout
        graph = _analyze(repo, repos, tarball)
        nodes = {node["id"] for node in graph["nodes"]}
        with tempfile.TemporaryDirectory() as workdir:
            tree = Path(workdir)
            _extract(tarball, tree)
            if any(path.endswith(".py") for path in nodes):
                truth, runtime, basis = *_python_truth(tree, nodes), "python ast"
            elif any(path.endswith(".kt") for path in nodes):
                truth, runtime, basis = *_kotlin_truth(tree, nodes), "kotlin regex (approximate)"
            else:
                truth, runtime, basis = *_ts_truth(tree, graph, blog), "typescript compiler"
        truth = {edge for edge in truth if edge[1] in nodes and edge[0] != edge[1]}
        kinds = {(edge["source"], edge["target"]): edge["kind"] for edge in graph["edges"]}
        said_runtime = {edge for edge, kind in kinds.items() if kind != "type"}
        print(
            f"{repo:24}{len(kinds):>7}{len(truth):>7}{len(truth - set(kinds)):>8}{len(set(kinds) - truth):>7}"
            f"{len(said_runtime - runtime):>12}{len((set(kinds) - said_runtime) & runtime):>12}  {basis}"
        )
    print(
        "\ntype wrong: 분석기는 실행 시 의존이라는데 기준은 지운다. NestJS 는 emitDecoratorMetadata 때문에 일부러 보수적이다(G-27)."
    )
    print("kept wrong: 분석기는 타입 전용이라는데 기준은 실행 때 남긴다. 0 이어야 한다.")


if __name__ == "__main__":
    main()
