"""1회용 PoC(2026-11-03): tree-sitter 로 저장소를 훑어 파일·심볼·import 그래프를 만들고 networkx Louvain 으로 묶어 본다.

README 의 "확인된 전제" 수치는 이 스크립트로 얻었다. 앱 코드가 아니며 A 단계 `services/code_graph.py` 의 출발점이다.
의존성(앱 requirements 에는 아직 없다): tree-sitter==0.26.0 tree-sitter-language-pack==1.23.1 networkx==3.7
사용: python code_graph_design/poc_codegraph.py <repo_root> <src_subdir>   예) ... ../vans_story src
"""

import json
import os
import sys
import time
from collections import Counter
from pathlib import Path

import networkx as nx
from tree_sitter_language_pack import get_parser

LANG_BY_EXT = {".py": "python", ".ts": "typescript", ".tsx": "tsx", ".kt": "kotlin"}
SKIP_DIRS = {
    "node_modules",
    ".git",
    ".venv",
    "__pycache__",
    ".next",
    "dist",
    "build",
    "docs",
    "coverage",
    ".ruff_cache",
    ".pytest_cache",
    "public",
    "test",
    "tests",
    "__tests__",
}
DEF_TYPES = {
    "python": {"function_definition", "class_definition"},
    "typescript": {
        "function_declaration",
        "class_declaration",
        "interface_declaration",
        "type_alias_declaration",
        "enum_declaration",
        "method_definition",
    },
    "kotlin": {"function_declaration", "class_declaration", "object_declaration"},
}
DEF_TYPES["tsx"] = DEF_TYPES["typescript"]
IMPORT_TYPES = {
    "python": {"import_statement", "import_from_statement"},
    "typescript": {"import_statement"},
    "tsx": {"import_statement"},
    "kotlin": {"import_header"},
}


def iter_files(root: Path):
    for path in root.rglob("*"):
        if any(part in SKIP_DIRS for part in path.relative_to(root).parts):
            continue
        if path.is_file() and path.suffix in LANG_BY_EXT and not path.name.endswith(".d.ts"):
            yield path


def walk(node):
    stack = [node]
    while stack:
        current = stack.pop()
        yield current
        stack.extend(reversed(current.children))


def import_targets(node, lang: str) -> list[str]:
    text = node.text.decode("utf-8", "replace")
    if lang == "python":
        if node.type == "import_from_statement":
            module = node.child_by_field_name("module_name")
            return [module.text.decode()] if module is not None else []
        return [c.text.decode() for c in node.children if c.type == "dotted_name"] + [
            c.child_by_field_name("name").text.decode() for c in node.children if c.type == "aliased_import"
        ]
    if lang in ("typescript", "tsx"):
        source = node.child_by_field_name("source")
        return [source.text.decode().strip("'\"")] if source is not None else []
    if lang == "kotlin":
        return [text.replace("import", "", 1).strip().split(" as ")[0].strip()]
    return []


def resolve(target: str, from_file: Path, repo: Path, src: Path, lang: str, index: dict[str, Path]) -> Path | None:
    """내부 파일이면 경로, 외부 패키지면 None."""
    if lang == "python":
        return index.get(target) or index.get(target + ".__init__")
    if lang in ("typescript", "tsx"):
        if target.startswith("@/"):
            base = src / target[2:]
        elif target.startswith("src/"):
            base = repo / target  # NestJS tsconfig baseUrl 방식
        elif target.startswith("."):
            base = (from_file.parent / target).resolve()
        else:
            return None
        # with_suffix 는 posts.service → posts.ts 로 바꿔 버리므로 이름 뒤에 붙인다
        for candidate in (
            base,
            *(base.parent / (base.name + ext) for ext in (".ts", ".tsx")),
            base / "index.ts",
            base / "index.tsx",
        ):
            if candidate.is_file() and candidate.suffix in LANG_BY_EXT:
                return candidate
        return None
    if lang == "kotlin":
        return index.get(target)
    return None


def build_index(files: list[Path], repo: Path, src: Path) -> dict[str, Path]:
    index: dict[str, Path] = {}
    for path in files:
        if path.suffix == ".py":
            dotted = ".".join(path.relative_to(repo).with_suffix("").parts)
            index[dotted] = path
        elif path.suffix == ".kt":
            parts = path.relative_to(src).with_suffix("").parts
            index[".".join(parts)] = path
            # 파일 안의 최상위 선언은 패키지.파일명 으로 import 된다고 근사한다
    return index


def main() -> None:
    repo = Path(sys.argv[1]).resolve()
    src = (repo / sys.argv[2]).resolve()
    files = sorted(iter_files(src))
    index = build_index(files, repo, src)
    parsers = {lang: get_parser(lang) for lang in set(LANG_BY_EXT.values())}
    # 레이어 = 모든 파일의 공통 조상 바로 아래 디렉터리 (Kotlin 은 패키지 루트 아래가 된다)
    common = Path(os.path.commonpath([p.parent for p in files]))

    def layer_of(path: Path) -> str:
        parts = path.relative_to(common).parts
        return parts[0] if len(parts) > 1 else "(root)"

    graph = nx.DiGraph()
    defs = Counter()
    external = Counter()
    symbol_count = 0
    started = time.perf_counter()
    for path in files:
        lang = LANG_BY_EXT[path.suffix]
        tree = parsers[lang].parse(path.read_bytes())
        node_id = path.relative_to(repo).as_posix()
        graph.add_node(node_id, layer=layer_of(path))
        for node in walk(tree.root_node):
            if node.type in DEF_TYPES[lang]:
                defs[node.type] += 1
                symbol_count += 1
            elif node.type in IMPORT_TYPES[lang]:
                for target in import_targets(node, lang):
                    resolved = resolve(target, path, repo, src, lang, index)
                    if resolved is None:
                        external[target.split("/")[0].split(".")[0]] += 1
                    elif resolved != path:
                        graph.add_edge(node_id, resolved.relative_to(repo).as_posix())
    elapsed = time.perf_counter() - started

    undirected = graph.to_undirected()
    communities = nx.community.louvain_communities(undirected, seed=1) if graph.number_of_edges() else []
    community_summary = []
    for members in sorted(communities, key=len, reverse=True)[:8]:
        layers = Counter(graph.nodes[m]["layer"] for m in members)
        community_summary.append({"size": len(members), "dirs": dict(layers.most_common(3))})

    layer_edges = Counter()
    for source, target in graph.edges:
        layer_edges[f"{graph.nodes[source]['layer']} -> {graph.nodes[target]['layer']}"] += 1

    print(
        json.dumps(
            {
                "repo": repo.name,
                "files": len(files),
                "parse_seconds": round(elapsed, 2),
                "symbols": symbol_count,
                "defs_by_type": dict(defs.most_common()),
                "internal_edges": graph.number_of_edges(),
                "external_imports_top": dict(external.most_common(8)),
                "layer_edges_top": dict(layer_edges.most_common()),
                "suspect_edges": sorted(
                    f"{s} -> {t}"
                    for s, t in graph.edges
                    if (graph.nodes[s]["layer"], graph.nodes[t]["layer"])
                    in {
                        ("routers", "models"),
                        ("routers", "repositories"),
                        ("routers", "clients"),
                        ("schemas", "models"),
                        ("models", "schemas"),
                        ("models", "services"),
                        ("models", "repositories"),
                        ("clients", "models"),
                        ("clients", "services"),
                        ("clients", "repositories"),
                        ("repositories", "services"),
                        ("repositories", "schemas"),
                        ("agents", "services"),
                        ("agents", "repositories"),
                        ("agents", "clients"),
                        ("agents", "schemas"),
                        ("core", "models"),
                        ("core", "services"),
                        ("core", "clients"),
                        ("core", "repositories"),
                    }
                ),
                "most_depended_on": [n for n, _ in sorted(graph.in_degree, key=lambda item: -item[1])[:6]],
                "louvain_communities": community_summary,
            },
            ensure_ascii=False,
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
