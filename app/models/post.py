"""ES posts 인덱스의 게시글 문서·매핑과 검색 조건·결과."""

from dataclasses import dataclass
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from app.models.analyzers import BASE_INDEX_SETTINGS

PostSort = Literal["relevance", "date_desc", "date_asc"]

_TEXT_KO_EN = {"type": "text", "analyzer": "korean_analyzer"}
_ENGLISH = {"english": {"type": "text", "analyzer": "english_analyzer"}}
_SUGGEST = {"suggest": {"type": "completion"}}

# 운영 posts_v2 인덱스의 설정·매핑. 인덱스를 새로 만들 때 쓴다 (Django migrate_search_index 의 정의와 같다).
POST_INDEX_MAPPING: dict[str, Any] = {
    "settings": BASE_INDEX_SETTINGS,
    "mappings": {
        "properties": {
            "post_id": {"type": "keyword"},
            "title": {**_TEXT_KO_EN, "fields": {**_ENGLISH, "raw": {"type": "keyword"}, **_SUGGEST}},
            "description": {**_TEXT_KO_EN, "fields": {**_ENGLISH, **_SUGGEST}},
            "content_text": {**_TEXT_KO_EN, "fields": _ENGLISH},
            "topic": {**_TEXT_KO_EN, "fields": {**_ENGLISH, "raw": {"type": "keyword"}, **_SUGGEST}},
            "mainCategory": {"type": "keyword", "fields": _SUGGEST},
            "subCategory": {"type": "keyword", "fields": _SUGGEST},
            "tags": {"type": "keyword", "fields": _SUGGEST},
            "author": {"type": "keyword"},
            "language": {"type": "keyword"},
            "createdAt": {"type": "date"},
            "updatedAt": {"type": "date"},
        }
    },
}


class Post(BaseModel):
    """posts 인덱스에 저장된 게시글 문서.

    오래된 문서에 빠진 필드가 있을 수 있어 모두 선택이다 (Django 버전도 없으면 null 로 응답했다).
    content_text 는 색인할 때만 채운다. 검색은 이 필드를 읽지 않는다.
    """

    model_config = ConfigDict(validate_by_name=True)

    post_id: str | None = None
    title: str | None = None
    description: str | None = None
    content_text: str | None = None
    topic: str | None = None
    main_category: str | None = Field(None, alias="mainCategory")
    sub_category: str | None = Field(None, alias="subCategory")
    tags: list[str] | None = None
    author: str | None = None
    language: str | None = None
    # 날짜는 저장된 문자열 그대로 둔다. 검색 응답이 _source 의 날짜 문자열을 그대로 내보내는 것이 계약이다.
    created_at: str | None = Field(None, alias="createdAt")
    updated_at: str | None = Field(None, alias="updatedAt")


@dataclass(frozen=True)
class PostSearchCriteria:
    """게시글 검색 조건. 빈 문자열·빈 튜플·None 인 조건은 적용하지 않는다."""

    query: str
    main_category: str
    sub_category: str
    tags: tuple[str, ...]
    language: str | None
    page: int
    page_size: int
    sort: PostSort


@dataclass(frozen=True)
class PostHit:
    """검색에 걸린 게시글 하나와 점수·하이라이트. 날짜 정렬이면 점수는 None 이다."""

    post: Post
    score: float | None
    highlight: dict[str, list[str]]


@dataclass(frozen=True)
class PostSearchPage:
    """검색 결과 한 페이지와 전체 건수."""

    total: int
    hits: list[PostHit]


def extract_content_text(content: object) -> str:
    """본문을 검색용 평문으로. Tiptap JSON 이면 text 노드를 공백으로 잇고, 문자열(레거시 데이터)이면 그대로 쓴다."""
    if isinstance(content, dict):
        return " ".join(_tiptap_texts(content))
    if isinstance(content, str):
        return content
    return ""


def _tiptap_texts(node: object) -> list[str]:
    if not isinstance(node, dict):
        return []
    texts = [node["text"]] if node.get("type") == "text" and isinstance(node.get("text"), str) else []
    children = node.get("content")
    if isinstance(children, list):
        for child in children:
            texts += _tiptap_texts(child)
    return texts
