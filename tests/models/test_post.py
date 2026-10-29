"""models/post.py 테스트."""

from app.models.post import extract_content_text


def test_extract_content_text_joins_nested_tiptap_text_nodes():
    content = {
        "type": "doc",
        "content": [
            {"type": "heading", "content": [{"type": "text", "text": "제목"}]},
            {"type": "paragraph", "content": [{"type": "text", "text": "본문"}, {"type": "hardBreak"}]},
            {"type": "codeBlock", "content": None},
        ],
    }

    assert extract_content_text(content) == "제목 본문"


def test_extract_content_text_keeps_legacy_string_and_empties_others():
    assert extract_content_text("평문") == "평문"
    assert extract_content_text(None) == ""
