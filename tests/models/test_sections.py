"""섹션 앵커 찾기·지우기·자르기 테스트."""

from app.models.sections import find_anchors, section_anchor, section_text, strip_anchors

BODY = "<!-- sec:intro -->\n서론\n\n<!-- sec:body-1 -->\n본론 <!-- sec:code-1 -->\n```python\nx = 1\n```\n<!-- sec:intro -->\n다시"


def test_find_anchors_keeps_order_and_duplicates():
    assert find_anchors(BODY) == ["intro", "body-1", "code-1", "intro"]


def test_strip_anchors_removes_anchor_lines_and_inline_anchors():
    assert strip_anchors(BODY) == "서론\n\n본론 \n```python\nx = 1\n```\n다시"


def test_section_text_returns_first_match_until_next_anchor():
    assert section_text(BODY, "intro") == "서론"
    assert section_text(BODY, "body-1") == "본론 "
    assert section_text(BODY, "code-1") == "```python\nx = 1\n```"
    assert section_text(BODY, "missing") is None


def test_section_anchor_format():
    assert section_anchor("body-2") == "<!-- sec:body-2 -->"
