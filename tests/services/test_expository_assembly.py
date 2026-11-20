"""설명문 파이프라인 조립: 사실 확인 패치, 요약·정리 표, 용어 정리 절, 서론 첫 문단."""

from app.services.expository_assembly import (
    apply_patches,
    assemble_article,
    first_paragraph,
    join_blocks,
    split_blocks,
)
from tests.factories import (
    make_expository_english_article,
    make_expository_korean_article,
    make_expository_summaries,
    make_expository_terms,
)


def test_apply_patches_applies_a_unique_match_and_rejects_the_rest():
    article = make_expository_english_article()
    patches = [
        {"section_id": "concept", "before": "The concept part", "after": "The concept section", "reason": "r"},
        {"section_id": "intro", "before": "the", "after": "a", "reason": "occurs twice in the section"},
        {"section_id": "missing", "before": "x", "after": "y", "reason": "no such section"},
    ]
    patched, rejected = apply_patches(article, patches, lambda markdown: [])
    assert "The concept section explains" in patched
    assert "The intro part explains how the proxy" in patched
    assert [patch["section_id"] for patch in rejected] == ["intro", "missing"]


def test_apply_patches_reverts_a_patch_that_breaks_the_article():
    article = make_expository_english_article()
    patch = {"section_id": "intro", "before": "The intro part", "after": "Broken", "reason": "r"}
    patched, rejected = apply_patches(article, [patch], lambda markdown: ["anchor missing"])
    assert patched == article
    assert rejected[0]["rejected_because"].startswith("patched article fails checks")


def test_assemble_article_adds_summary_tables_overview_and_numbered_glossary():
    article = make_expository_english_article()
    assembled = assemble_article(
        article,
        make_expository_summaries(),
        make_expository_terms(),
        overview_header=("Mechanism", "Takeaway"),
        overview_rows=[("Mechanism 1", "Advice runs only on proxy entry 1")],
        last_section="conclusion",
    )
    assert assembled.index("**요약: Part intro**\n\n| 항목 | 설명 |") < assembled.index("<!-- sec:concept -->")
    # 전체 정리 표는 마지막 섹션(용어 정리 절 앞)에 양식의 머리로 붙는다
    overview = assembled.split("<!-- sec:conclusion -->")[1].split("<!-- sec:glossary -->")[0]
    assert "**전체 정리**" in overview
    assert "| Mechanism | Takeaway |\n|---|---|\n| Mechanism 1 | Advice runs only on proxy entry 1 |" in overview
    glossary = assembled.split("<!-- sec:glossary -->")[1]
    assert glossary.startswith("\n## 9. 용어 정리\n\n| 용어 | 정의 |")
    assert "| proxy | an object that stands in for another |" in glossary
    assert "propagation" not in glossary  # 본문이 정의한 용어(정의 없음)는 용어 정리에 넣지 않는다


def test_split_blocks_keeps_a_code_fence_with_a_blank_line_and_its_anchor_in_one_block():
    markdown = (
        "<!-- sec:intro -->\n## 1. 소개\n\n첫 문단입니다.\n\n- 하나\n- 둘\n\n"
        "<!-- sec:code-1 -->\n```java\nclass A {}\n\nclass B {}\n```\n\n"
        "**요약: 소개**\n\n| 항목 | 설명 |\n|---|---|\n| a | b |\n\n> 인용입니다.\n\n"
        "<!-- sec:concept -->\n\n~~~\nx\n\ny\n~~~\n\n둘째 문단입니다.\n"
    )
    blocks = split_blocks(markdown)
    assert [(block.kind, block.section) for block in blocks] == [
        ("heading", "intro"),
        ("paragraph", "intro"),
        ("list", "intro"),
        ("code", "intro"),
        ("table", "intro"),
        ("table", "intro"),
        ("quote", "intro"),
        ("anchor", "concept"),
        ("code", "concept"),
        ("paragraph", "concept"),
    ]
    assert blocks[3].text == "<!-- sec:code-1 -->\n```java\nclass A {}\n\nclass B {}\n```"
    assert join_blocks(blocks) == markdown


def test_first_paragraph_skips_the_heading():
    assert first_paragraph(make_expository_korean_article()).startswith("intro 절은 프록시가")
