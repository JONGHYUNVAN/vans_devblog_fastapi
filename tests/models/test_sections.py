"""섹션 앵커 찾기·지우기·자르기 테스트."""

from app.models.sections import (
    REQUIRED_SECTIONS,
    changed_sections,
    find_anchors,
    markdown_links,
    required_sections_for,
    section_anchor,
    section_text,
    strip_anchors,
)

BODY = "<!-- sec:intro -->\n서론\n\n<!-- sec:body-1 -->\n본론 <!-- sec:code-1 -->\n```python\nx = 1\n```\n<!-- sec:intro -->\n다시"


def test_find_anchors_keeps_order_and_duplicates():
    assert find_anchors(BODY) == ["intro", "body-1", "code-1", "intro"]


def test_strip_anchors_removes_anchor_text_but_keeps_line_breaks():
    # 앵커만 있던 줄은 빈 줄로 남아 앞뒤 문단이 합쳐지지 않는다
    assert strip_anchors(BODY) == "\n서론\n\n\n본론 \n```python\nx = 1\n```\n\n다시"


def test_anchors_inside_code_fences_are_code_not_anchors():
    body = "<!-- sec:intro -->\n서론\n```python\n# <!-- sec:intro -->\nx = 1\n```\n<!-- sec:body-1 -->\n본론"

    assert find_anchors(body) == ["intro", "body-1"]
    assert strip_anchors(body) == "\n서론\n```python\n# <!-- sec:intro -->\nx = 1\n```\n\n본론"
    assert section_text(body, "intro") == "서론\n```python\n# <!-- sec:intro -->\nx = 1\n```"
    tilde = "~~~\n<!-- sec:intro -->\n~~~\n<!-- sec:intro -->\n"
    assert find_anchors(tilde) == ["intro"]


def test_section_text_returns_first_match_until_next_anchor():
    assert section_text(BODY, "intro") == "서론"
    assert section_text(BODY, "body-1") == "본론 "
    assert section_text(BODY, "code-1") == "```python\nx = 1\n```"
    assert section_text(BODY, "missing") is None


def test_section_anchor_format():
    assert section_anchor("body-2") == "<!-- sec:body-2 -->"


def test_required_sections_drop_tradeoff_only_for_level_100():
    assert "tradeoff" not in required_sections_for(100)
    assert required_sections_for(None) == REQUIRED_SECTIONS == required_sections_for(300)


def test_changed_sections_lists_edited_and_new_sections():
    before = "<!-- sec:intro -->\n서론\n\n<!-- sec:body-1 -->\n본론\n"
    after = "<!-- sec:intro -->\n서론\n\n<!-- sec:body-1 -->\n고친 본론\n\n<!-- sec:body-2 -->\n새 본론\n"

    assert changed_sections(before, after) == ["body-1", "body-2"]
    assert changed_sections(before, before) == []


def test_markdown_links_skips_code_blocks_and_duplicates():
    markdown = "[a](https://a.test) [a](https://a.test)\n\n```\n[b](https://b.test)\n```\n\n[c](#x)\n"

    assert markdown_links(markdown) == ["https://a.test", "#x"]
