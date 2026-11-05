"""CodeGraph 진단 계산 테스트: 레이어 위반 규칙(G-14), 순환 대표 경로(G-07), orphan·hub(G-06), context 마크다운의 빈 절."""

from datetime import UTC, datetime

from app.models.code_graph import CodeGraph, CodeGraphRepo, SourceFile, stronger_kind


def _build(paths: list[str], imports: list[tuple[str, str]], **spec: object) -> CodeGraph:
    repo_spec = CodeGraphRepo.model_validate(
        {
            "owner": "JONGHYUNVAN",
            "name": "demo",
            "source_roots": ["src"],
            "layers": [
                {"id": "upper", "path": "src/upper", "may_import": ["lower"]},
                {"id": "inner", "path": "src/upper/inner", "may_import": []},
                {"id": "lower", "path": "src/lower", "may_import": []},
            ],
        }
        | spec
    )
    return CodeGraph.build(
        repo="demo",
        commit_sha="c" * 40,
        analyzed_at=datetime(2026, 11, 3, tzinfo=UTC),
        tech_stack=[],
        repo_spec=repo_spec,
        files=[SourceFile(path=path, symbols=[], is_barrel=path.endswith("index.ts")) for path in paths],
        imports=dict.fromkeys(imports, "static"),
    )


def test_build_flags_only_undeclared_cross_layer_imports():
    imports = [
        ("src/upper/a.ts", "src/lower/b.ts"),  # 허용
        ("src/lower/b.ts", "src/upper/a.ts"),  # 위반
        ("src/lower/b.ts", "src/lower/c.ts"),  # 같은 레이어
        ("src/lower/c.ts", "src/main.ts"),  # target 이 root
        ("src/main.ts", "src/upper/a.ts"),  # source 가 root
        ("src/lower/c.ts", "src/upper/a.ts"),  # 선언된 예외
    ]
    paths = ["src/main.ts", "src/upper/a.ts", "src/lower/b.ts", "src/lower/c.ts"]

    code_graph = _build(paths, imports, allowed_edges=[{"source": "src/lower/c.ts", "target": "src/upper/a.ts"}])

    assert [
        (each.source, each.target, each.from_layer, each.to_layer) for each in code_graph.diagnostics.layer_violations
    ] == [("src/lower/b.ts", "src/upper/a.ts", "lower", "upper")]
    assert [edge.source for edge in code_graph.edges if edge.status == "violation"] == ["src/lower/b.ts"]


def test_build_assigns_longest_matching_layer_prefix():
    code_graph = _build(["src/upper/inner/x.ts", "src/upper/y.ts", "src/upperish/z.ts"], [])

    assert {node.id: node.layer for node in code_graph.nodes} == {
        "src/upper/inner/x.ts": "inner",
        "src/upper/y.ts": "upper",
        "src/upperish/z.ts": "root",  # 접두는 디렉터리 경계까지 맞아야 한다
    }
    assert [(layer.id, layer.file_count) for layer in code_graph.layers] == [("upper", 1), ("inner", 1), ("lower", 0)]


def test_build_records_one_representative_path_per_cycle():
    imports = [
        ("src/lower/q.ts", "src/lower/p.ts"),
        ("src/lower/p.ts", "src/lower/q.ts"),
        ("src/lower/q.ts", "src/lower/r.ts"),
        ("src/lower/r.ts", "src/lower/p.ts"),
        ("src/upper/y.ts", "src/upper/x.ts"),
        ("src/upper/x.ts", "src/upper/y.ts"),
        ("src/upper/x.ts", "src/lower/p.ts"),
    ]
    paths = sorted({path for edge in imports for path in edge})

    code_graph = _build(paths, imports)

    assert code_graph.diagnostics.cycles == [
        ["src/lower/p.ts", "src/lower/q.ts", "src/lower/p.ts"],
        ["src/upper/x.ts", "src/upper/y.ts", "src/upper/x.ts"],
    ]


def test_build_orphans_skip_entry_globs_and_barrels():
    paths = [
        "src/app/page.tsx",  # `**/` 는 0개 디렉터리도 된다
        "src/app/a/b/page.tsx",
        "src/app.module.ts",
        "src/app/pages.tsx",
        "src/lib/index.ts",
        "src/lib/used.ts",
    ]
    imports = [("src/app/page.tsx", "src/lib/used.ts")]
    entry_points = ["src/app/**/page.tsx", "src/**/*.module.ts"]

    code_graph = _build(paths, imports, entry_points=entry_points, diagnostics={"orphans": True})
    toggled_off = _build(paths, imports, entry_points=entry_points)

    assert code_graph.diagnostics.orphan_files == ["src/app/pages.tsx"]
    assert toggled_off.diagnostics.orphan_files == []


def test_build_hubs_rank_by_in_then_out_degree_up_to_limit():
    sources = [f"src/upper/s{i:02}.ts" for i in range(20)]
    targets = [f"src/lower/t{i:02}.ts" for i in range(17)]
    imports = [(source, target) for target in targets for source in sources[:5]]
    imports += [(sources[5], targets[16]), (targets[16], targets[15]), (targets[3], targets[0])]
    imports += [(source, "src/lower/few.ts") for source in sources[:4]]

    code_graph = _build([*sources, *targets, "src/lower/few.ts"], imports, diagnostics={"hubs": True})

    # in 6: t16(out 1) > t00·t15(out 0, 경로순). in 5: t03(out 1) > 나머지 경로순. 15개에서 자른다. few(in 4)는 빠진다
    assert [hub.path.removeprefix("src/lower/") for hub in code_graph.diagnostics.hub_files] == [
        "t16.ts",
        "t00.ts",
        "t15.ts",
        "t03.ts",
        *(f"t{i:02}.ts" for i in (1, 2, 4, 5, 6, 7, 8, 9, 10, 11, 12)),
    ]
    assert (code_graph.diagnostics.hub_files[0].in_degree, code_graph.diagnostics.hub_files[0].out_degree) == (6, 1)


def test_render_context_marks_empty_sections():
    code_graph = _build(["src/upper/App.kt"], [], diagnostics={"orphans": True, "hubs": True})

    context = code_graph.render_context()

    assert context.startswith("# Code Graph Context: demo (`ccccccc`)\n")
    assert "- Violations (0)\n- Declared Exceptions (0)\n" in context
    assert "- Runtime Cycles (0)\n- Orphans (1): `src/upper/App.kt`\n" in context
    assert "| lower | 0 | - |" in context


def _build_kinds(paths: list[str], imports: dict[tuple[str, str], str]) -> CodeGraph:
    repo_spec = CodeGraphRepo.model_validate(
        {
            "owner": "JONGHYUNVAN",
            "name": "demo",
            "source_roots": ["src"],
            "layers": [
                {"id": "upper", "path": "src/upper", "may_import": ["lower"]},
                {"id": "lower", "path": "src/lower", "may_import": []},
            ],
        }
    )
    return CodeGraph.build(
        repo="demo",
        commit_sha="c" * 40,
        analyzed_at=datetime(2026, 11, 3, tzinfo=UTC),
        tech_stack=[],
        repo_spec=repo_spec,
        files=[SourceFile(path=path, symbols=[]) for path in paths],
        imports=imports,
    )


def test_type_only_violation_is_flagged_and_marked_in_context():
    code_graph = _build_kinds(
        ["src/upper/a.ts", "src/lower/b.ts", "src/lower/c.ts"],
        {("src/lower/b.ts", "src/upper/a.ts"): "type", ("src/lower/c.ts", "src/upper/a.ts"): "static"},
    )

    assert [(each.source, each.type_only) for each in code_graph.diagnostics.layer_violations] == [
        ("src/lower/b.ts", True),
        ("src/lower/c.ts", False),
    ]
    context = code_graph.render_context()
    assert "- Violations, type-only 1 (2):" in context
    assert "`src/upper/a.ts` (upper) [type-only]" in context


def test_cycle_group_lists_every_member_not_only_the_representative_path():
    files = ["src/lower/a.ts", "src/lower/b.ts", "src/lower/c.ts", "src/lower/d.ts"]
    imports = {
        ("src/lower/a.ts", "src/lower/b.ts"): "static",
        ("src/lower/b.ts", "src/lower/a.ts"): "static",
        ("src/lower/a.ts", "src/lower/c.ts"): "static",
        ("src/lower/c.ts", "src/lower/a.ts"): "dynamic",
        ("src/lower/a.ts", "src/lower/d.ts"): "static",
        ("src/lower/d.ts", "src/lower/a.ts"): "type",
    }

    diagnostics = _build_kinds(files, imports).diagnostics

    assert diagnostics.cycle_groups == [["src/lower/a.ts", "src/lower/b.ts", "src/lower/c.ts"]]
    assert diagnostics.type_only_cycle_groups == [files]


def test_unassigned_files_reports_new_folders_but_not_files_at_the_source_root():
    diagnostics = _build_kinds(["src/main.ts", "src/orders/a.ts", "src/upper/b.ts"], {}).diagnostics

    assert diagnostics.unassigned_files == ["src/orders/a.ts"]


def test_impact_limits_hops_and_can_skip_type_edges():
    files = ["src/lower/a.ts", "src/lower/b.ts", "src/lower/c.ts", "src/lower/d.ts"]
    code_graph = _build_kinds(
        files,
        {
            ("src/lower/b.ts", "src/lower/a.ts"): "static",
            ("src/lower/c.ts", "src/lower/b.ts"): "static",
            ("src/lower/d.ts", "src/lower/a.ts"): "type",
            ("src/lower/a.ts", "src/lower/c.ts"): "dynamic",
        },
    )

    one_hop = code_graph.impact("src/lower/a.ts", 1)
    assert one_hop.upstream == ["src/lower/b.ts", "src/lower/d.ts"]
    assert one_hop.downstream == ["src/lower/c.ts"]
    assert code_graph.impact("src/lower/a.ts", 1, runtime_only=True).upstream == ["src/lower/b.ts"]
    assert code_graph.impact("src/lower/nope.ts", 1) is None


def test_stronger_kind_prefers_runtime():
    assert stronger_kind("type", "dynamic") == "dynamic"
    assert stronger_kind("static", "dynamic") == "static"
    assert stronger_kind("type", "type") == "type"
