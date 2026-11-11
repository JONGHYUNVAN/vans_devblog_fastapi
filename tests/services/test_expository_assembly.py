"""설명문 파이프라인 조립: 사실 확인 패치, 요약·정리 표, 용어 정리 절, 서론 첫 문단."""

from app.services.expository_assembly import apply_patches, assemble_article, first_paragraph
from tests.factories import (
    make_expository_english_article,
    make_expository_korean_article,
    make_expository_outline,
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
        article, make_expository_summaries(), make_expository_outline("A thesis."), make_expository_terms()
    )
    assert assembled.index("**📊 Summary: Part intro**") < assembled.index("<!-- sec:concept -->")
    assert "| Mechanism 1 | Advice runs only on proxy entry 1 |" in assembled
    glossary = assembled.split("<!-- sec:glossary -->")[1]
    assert glossary.startswith("\n## 9. Glossary")
    assert "| proxy | an object that stands in for another |" in glossary
    assert "propagation" not in glossary  # 본문이 정의한 용어(정의 없음)는 용어 정리에 넣지 않는다


def test_first_paragraph_skips_the_heading():
    assert first_paragraph(make_expository_korean_article()).startswith("intro 절은 프록시가")
