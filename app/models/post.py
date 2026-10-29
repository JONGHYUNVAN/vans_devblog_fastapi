"""ES posts 인덱스의 게시글 문서와 검색 조건·결과."""

from dataclasses import dataclass
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

PostSort = Literal["relevance", "date_desc", "date_asc"]


class Post(BaseModel):
    """posts 인덱스에 저장된 게시글 문서.

    오래된 문서에 빠진 필드가 있을 수 있어 모두 선택이다 (Django 버전도 없으면 null 로 응답했다).
    """

    model_config = ConfigDict(validate_by_name=True)

    post_id: str | None = None
    title: str | None = None
    description: str | None = None
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
