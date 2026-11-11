"""마크다운 → Tiptap JSON 변환 (설계 04 문서 8절 변환표). 순수 함수. 변환할 수 없는 문법은 ValueError.

노드·마크 이름과 attrs 는 블로그 에디터 스키마(vans_story EditorExtensions: StarterKit, Link, CodeBlockLowlight, Table)를 따른다.
파서는 markdown-it-py 의 commonmark 프리셋에 GFM 표·취소선을 더한 것이다.
"""

import re
from typing import Any

from markdown_it import MarkdownIt
from markdown_it.token import Token

from app.models.sections import strip_anchors

_FOOTNOTE_PATTERN = re.compile(r"\[\^[^\]]+\]")
_FORBIDDEN_LANGUAGE = "mermaid"
_SNIPPET_SUFFIX = "-snippet"  # python-snippet 은 게이트 전용 표시. 에디터에는 python 으로 보낸다
_LINK_ATTRS = {
    "target": "_blank",
    "rel": "noopener noreferrer",
    "class": None,
}  # Link.configure 의 HTMLAttributes 와 같다
_CELL_ATTRS = {"colspan": 1, "rowspan": 1, "colwidth": None}
_MARK_BY_TOKEN = {"strong": "bold", "em": "italic", "s": "strike"}

_parser = MarkdownIt("commonmark").enable(["table", "strikethrough"])


def parse_markdown(markdown: str) -> list[Token]:
    """변환기와 게이트(services/validation.py)가 같은 파서·같은 문법으로 본문을 본다."""
    return _parser.parse(markdown)


def top_level_paragraphs(markdown: str) -> list[tuple[int, int, Token]]:
    """최상위 문단마다 (시작 줄, 끝 줄(미포함), inline 토큰). 목록·표·인용·코드 안은 뺀다. 밀도 지표와 다듬기가 쓴다."""
    tokens = parse_markdown(markdown)
    return [
        (token.map[0], token.map[1], tokens[index + 1])
        for index, token in enumerate(tokens)
        if token.type == "paragraph_open" and token.level == 0 and token.map
    ]


def markdown_to_tiptap(markdown: str) -> dict[str, Any]:
    """앵커를 지우고 변환한다. HTML·이미지·각주·mermaid·그 밖의 변환표 밖 문법은 ValueError."""
    text = strip_anchors(markdown)
    if _FOOTNOTE_PATTERN.search(text):
        raise ValueError("각주([^n])는 변환하지 않는다")
    return {"type": "doc", "content": _Converter(parse_markdown(text)).blocks(0, None)}


def code_language(info: str) -> str | None:
    """펜스 info 문자열의 언어 태그. 없으면 None."""
    language = info.strip().split(" ")[0] if info.strip() else ""
    return language or None


class _Converter:
    def __init__(self, tokens: list[Token]) -> None:
        self._tokens = tokens

    def blocks(self, start: int, end: int | None) -> list[dict[str, Any]]:
        end = len(self._tokens) if end is None else end
        nodes: list[dict[str, Any]] = []
        index = start
        while index < end:
            token = self._tokens[index]
            if token.nesting == 1:
                close = self._close_index(index)
                nodes.extend(self._container(token, index + 1, close))
                index = close + 1
            else:
                nodes.append(self._leaf(token))
                index += 1
        return nodes

    def _close_index(self, open_index: int) -> int:
        depth = 0
        for index in range(open_index, len(self._tokens)):
            depth += self._tokens[index].nesting
            if depth == 0:
                return index
        raise ValueError(f"{self._tokens[open_index].type} 가 닫히지 않았다")

    def _container(self, token: Token, start: int, end: int) -> list[dict[str, Any]]:
        match token.type:
            case "paragraph_open":
                return [_with_content({"type": "paragraph"}, self._inline(start))]
            case "heading_open":
                level = int(token.tag[1])
                return [_with_content({"type": "heading", "attrs": {"level": level}}, self._inline(start))]
            case "bullet_list_open":
                return [{"type": "bulletList", "content": self.blocks(start, end)}]
            case "ordered_list_open":
                start_number = int(token.attrGet("start") or 1)
                return [{"type": "orderedList", "attrs": {"start": start_number}, "content": self.blocks(start, end)}]
            case "list_item_open":
                return [{"type": "listItem", "content": self.blocks(start, end)}]
            case "blockquote_open":
                return [{"type": "blockquote", "content": self.blocks(start, end)}]
            case "table_open":
                return [{"type": "table", "content": self.blocks(start, end)}]
            case "thead_open" | "tbody_open":
                return self.blocks(start, end)  # Tiptap 표에는 머리·몸통 구분이 없다
            case "tr_open":
                return [{"type": "tableRow", "content": self.blocks(start, end)}]
            case "th_open" | "td_open":
                cell_type = "tableHeader" if token.type == "th_open" else "tableCell"
                paragraph = _with_content({"type": "paragraph"}, self._inline(start))
                return [{"type": cell_type, "attrs": dict(_CELL_ATTRS), "content": [paragraph]}]
            case _:
                raise ValueError(f"변환표에 없는 블록이다: {token.type}")

    def _leaf(self, token: Token) -> dict[str, Any]:
        match token.type:
            case "fence":
                return _code_block(token)
            case "hr":
                return {"type": "horizontalRule"}
            case "html_block":
                raise ValueError(f"HTML 은 변환하지 않는다: {token.content.strip()[:40]}")
            case "code_block":
                raise ValueError("들여쓰기 코드 블록은 변환하지 않는다. 펜스(```언어)를 쓴다")
            case _:
                raise ValueError(f"변환표에 없는 블록이다: {token.type}")

    def _inline(self, index: int) -> list[dict[str, Any]]:
        token = self._tokens[index]
        if token.type != "inline":
            return []
        return _inline_nodes(token.children or [])


def _code_block(token: Token) -> dict[str, Any]:
    language = code_language(token.info)
    if language == _FORBIDDEN_LANGUAGE:
        raise ValueError("mermaid 코드 블록은 변환하지 않는다. 블로그 뷰어에 렌더러가 없다")
    if language and language.endswith(_SNIPPET_SUFFIX):
        language = language.removesuffix(_SNIPPET_SUFFIX)
    code = token.content.removesuffix("\n")
    node: dict[str, Any] = {"type": "codeBlock", "attrs": {"language": language}}
    if code:
        node["content"] = [{"type": "text", "text": code}]
    return node


def _inline_nodes(children: list[Token]) -> list[dict[str, Any]]:
    nodes: list[dict[str, Any]] = []
    marks: list[dict[str, Any]] = []
    for token in children:
        match token.type:
            case "text":
                _append_text(nodes, token.content, marks)
            case "softbreak":
                _append_text(nodes, " ", marks)  # 뷰어가 whitespace-pre-wrap 이라 줄바꿈을 그대로 두면 줄이 갈린다
            case "hardbreak":
                nodes.append({"type": "hardBreak"})
            case "code_inline":
                # 에디터의 Code 마크는 다른 마크를 모두 배제한다(excludes "_"). bold·link 안의 코드는 code 만 남는다
                _append_text(nodes, token.content, [{"type": "code"}])
            case "strong_open" | "em_open" | "s_open":
                marks.append({"type": _MARK_BY_TOKEN[token.type.removesuffix("_open")]})
            case "strong_close" | "em_close" | "s_close":
                marks.pop()
            case "link_open":
                marks.append({"type": "link", "attrs": {"href": token.attrGet("href"), **_LINK_ATTRS}})
            case "link_close":
                marks.pop()
            case "image":
                raise ValueError("이미지는 변환하지 않는다")
            case "html_inline":
                raise ValueError(f"HTML 은 변환하지 않는다: {token.content[:40]}")
            case _:
                raise ValueError(f"변환표에 없는 인라인 문법이다: {token.type}")
    return _rescue_unclosed_bold(nodes)


# CommonMark 는 닫는 ** 앞이 문장부호이고 뒤가 글자면 닫힘으로 보지 않는다. 한국어는 조사가 붙어
# "**작업 스레드(worker thread)**가" 가 별표째 남았다(2026-11-11 DeferredResult 글). 남은 쌍만 굵게로 되살린다
_UNCLOSED_BOLD = re.compile(r"\*\*(?=\S)(.+?)(?<=\S)\*\*")


def _rescue_unclosed_bold(nodes: list[dict[str, Any]]) -> list[dict[str, Any]]:
    rescued: list[dict[str, Any]] = []
    for node in nodes:
        marks = node.get("marks", [])
        if node["type"] != "text" or "**" not in node["text"] or any(mark["type"] == "code" for mark in marks):
            rescued.append(node)
            continue
        text, position = node["text"], 0
        for match in _UNCLOSED_BOLD.finditer(text):
            _append_text(rescued, text[position : match.start()], marks)
            bold = marks if any(mark["type"] == "bold" for mark in marks) else [*marks, {"type": "bold"}]
            _append_text(rescued, match.group(1), bold)
            position = match.end()
        _append_text(rescued, text[position:], marks)
    return rescued


def _append_text(nodes: list[dict[str, Any]], text: str, marks: list[dict[str, Any]]) -> None:
    if not text:
        return
    last = nodes[-1] if nodes else None
    if last is not None and last["type"] == "text" and last.get("marks", []) == marks:
        last["text"] += text
        return
    node: dict[str, Any] = {"type": "text", "text": text}
    if marks:
        node["marks"] = [dict(mark) for mark in marks]
    nodes.append(node)


def _with_content(node: dict[str, Any], content: list[dict[str, Any]]) -> dict[str, Any]:
    if content:
        node["content"] = content
    return node
