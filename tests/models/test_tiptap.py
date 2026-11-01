"""markdown_to_tiptap 테스트. 변환표(설계 04 문서 8절) 행마다 하나, 금지 문법마다 ValueError, 전체 샘플은 golden 과 비교한다."""

import json
from pathlib import Path

import pytest

from app.models.tiptap import markdown_to_tiptap
from tests.conftest import GOLDEN_DIR, load_golden

# 블로그 에디터(vans_story EditorExtensions)가 쓰는 노드·마크 이름. 변환 결과는 이 안에만 있어야 한다.
EDITOR_NODES = {
    "doc",
    "paragraph",
    "text",
    "heading",
    "bulletList",
    "orderedList",
    "listItem",
    "blockquote",
    "codeBlock",
    "horizontalRule",
    "hardBreak",
    "table",
    "tableRow",
    "tableHeader",
    "tableCell",
}
EDITOR_MARKS = {"bold", "italic", "strike", "code", "link"}


def _content(markdown: str) -> list[dict]:
    return markdown_to_tiptap(markdown)["content"]


def _text(text: str, *marks: dict) -> dict:
    node = {"type": "text", "text": text}
    if marks:
        node["marks"] = list(marks)
    return node


def test_paragraph_joins_soft_breaks_with_space():
    assert _content("첫 줄\n둘째 줄") == [{"type": "paragraph", "content": [_text("첫 줄 둘째 줄")]}]


@pytest.mark.parametrize("level", [1, 3, 6])
def test_heading_levels(level):
    assert _content("#" * level + " 제목") == [
        {"type": "heading", "attrs": {"level": level}, "content": [_text("제목")]}
    ]


def test_bullet_and_ordered_lists_wrap_items_in_paragraphs():
    assert _content("- a\n- b") == [
        {
            "type": "bulletList",
            "content": [
                {"type": "listItem", "content": [{"type": "paragraph", "content": [_text("a")]}]},
                {"type": "listItem", "content": [{"type": "paragraph", "content": [_text("b")]}]},
            ],
        }
    ]
    assert _content("3. x")[0] == {
        "type": "orderedList",
        "attrs": {"start": 3},
        "content": [{"type": "listItem", "content": [{"type": "paragraph", "content": [_text("x")]}]}],
    }


def test_blockquote():
    assert _content("> 인용") == [
        {"type": "blockquote", "content": [{"type": "paragraph", "content": [_text("인용")]}]}
    ]


def test_code_block_keeps_language_and_maps_snippet_to_python():
    assert _content("```sql\nSELECT 1;\n```") == [
        {"type": "codeBlock", "attrs": {"language": "sql"}, "content": [_text("SELECT 1;")]}
    ]
    assert _content("```python-snippet\nx = 1\n```")[0]["attrs"] == {"language": "python"}


def test_table_uses_header_for_first_row_and_cells_hold_paragraphs():
    [table] = _content("| h |\n|---|\n| c |")
    header_row, body_row = table["content"]
    assert header_row["content"][0]["type"] == "tableHeader"
    assert body_row["content"][0] == {
        "type": "tableCell",
        "attrs": {"colspan": 1, "rowspan": 1, "colwidth": None},
        "content": [{"type": "paragraph", "content": [_text("c")]}],
    }


def test_horizontal_rule():
    assert _content("---") == [{"type": "horizontalRule"}]


def test_hard_breaks_from_trailing_spaces_and_backslash():
    assert _content("a  \nb\\\nc") == [
        {
            "type": "paragraph",
            "content": [_text("a"), {"type": "hardBreak"}, _text("b"), {"type": "hardBreak"}, _text("c")],
        }
    ]


def test_inline_marks_and_link():
    [paragraph] = _content("**b** *i* ~~s~~ `c` [l](https://x.y)")
    link = {
        "type": "link",
        "attrs": {"href": "https://x.y", "target": "_blank", "rel": "noopener noreferrer", "class": None},
    }
    assert paragraph["content"] == [
        _text("b", {"type": "bold"}),
        _text(" "),
        _text("i", {"type": "italic"}),
        _text(" "),
        _text("s", {"type": "strike"}),
        _text(" "),
        _text("c", {"type": "code"}),
        _text(" "),
        _text("l", link),
    ]


def test_nested_marks_are_stacked():
    [paragraph] = _content("**굵은 *기울임***")
    assert paragraph["content"] == [
        _text("굵은 ", {"type": "bold"}),
        _text("기울임", {"type": "bold"}, {"type": "italic"}),
    ]


def test_section_anchors_are_removed():
    assert _content("<!-- sec:intro -->\n문단 <!-- sec:x -->끝") == [
        {"type": "paragraph", "content": [_text("문단 끝")]}
    ]


@pytest.mark.parametrize(
    ("markdown", "reason"),
    [
        ("<div>html</div>", "HTML"),
        ("문단 <b>굵게</b>", "HTML"),
        ("![그림](a.png)", "이미지"),
        ("각주[^1]\n\n[^1]: 설명", "각주"),
        ("```mermaid\ngraph TD\n```", "mermaid"),
        ("    들여쓰기 코드", "들여쓰기"),
    ],
)
def test_forbidden_syntax_raises_value_error(markdown, reason):
    with pytest.raises(ValueError, match=reason):
        markdown_to_tiptap(markdown)


def test_sample_matches_golden_and_uses_only_editor_schema():
    markdown = (GOLDEN_DIR / "tiptap_sample.md").read_text(encoding="utf-8")

    document = markdown_to_tiptap(markdown)

    assert document == load_golden("tiptap_sample")
    assert _node_types(document) <= EDITOR_NODES
    assert _mark_types(document) <= EDITOR_MARKS


def _node_types(node: dict) -> set[str]:
    types = {node["type"]}
    for child in node.get("content", []):
        types |= _node_types(child)
    return types


def _mark_types(node: dict) -> set[str]:
    types = {mark["type"] for mark in node.get("marks", [])}
    for child in node.get("content", []):
        types |= _mark_types(child)
    return types


def test_golden_file_is_valid_json():
    json.loads(Path(GOLDEN_DIR / "tiptap_sample.json").read_text(encoding="utf-8"))
