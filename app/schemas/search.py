"""검색 API 요청·응답. 키와 값 형식은 Django 서비스 응답(tests/golden/search_*.json 등)과 같다."""

from datetime import datetime
from typing import Any, Literal, Self
from zoneinfo import ZoneInfo

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

_MAX_TAGS = 10
_KST = ZoneInfo("Asia/Seoul")

Language = Literal["ko", "en", "all"]


class SearchParams(BaseModel):
    """게시물 검색 쿼리 파라미터."""

    # DRF CharField 처럼 문자열 앞뒤 공백을 지운다.
    model_config = ConfigDict(str_strip_whitespace=True)

    query: str = Field("", max_length=200, description="검색 키워드. 비우면 전체 게시물")
    main_category: str = Field("", alias="mainCategory", max_length=100, description="메인 카테고리")
    sub_category: str = Field("", alias="subCategory", max_length=100, description="서브 카테고리")
    tags: list[str] = Field(default_factory=list, description="태그. 쉼표로 구분, 최대 10개")
    language: Language = "all"
    date_from: datetime | None = Field(None, description="형식·순서만 검사하고 검색에는 쓰지 않는다")
    date_to: datetime | None = Field(None, description="형식·순서만 검사하고 검색에는 쓰지 않는다")
    page: int = Field(1, ge=1)
    page_size: int = Field(20, ge=1, le=100)
    sort: Literal["relevance", "date_desc", "date_asc"] = "relevance"

    @field_validator("tags", mode="before")
    @classmethod
    def split_tags(cls, value: Any) -> Any:
        if isinstance(value, str):
            value = [value]
        if not isinstance(value, list):
            return value
        tags = [tag.strip() for item in value for tag in str(item).split(",") if tag.strip()]
        return tags[:_MAX_TAGS]

    @field_validator("date_from", "date_to")
    @classmethod
    def assume_kst(cls, value: datetime | None) -> datetime | None:
        # Django(TIME_ZONE=Asia/Seoul)처럼 시간대 없는 값은 KST 로 본다. 순서를 비교할 때 naive·aware 가 섞이지 않는다.
        if value is not None and value.tzinfo is None:
            return value.replace(tzinfo=_KST)
        return value

    @model_validator(mode="after")
    def check_date_order(self) -> Self:
        if self.date_from and self.date_to and self.date_from > self.date_to:
            raise ValueError("시작 날짜는 종료 날짜보다 이전이어야 합니다.")
        return self


class AutocompleteParams(BaseModel):
    """자동완성 쿼리 파라미터."""

    model_config = ConfigDict(str_strip_whitespace=True)

    query: str = Field(min_length=1, max_length=100, description="자동완성할 검색어")
    language: Language = Field("all", description="받기만 하고 결과에는 영향이 없다")
    limit: int = Field(10, ge=1, le=20)


class SearchHit(BaseModel):
    """검색 결과 게시글 하나."""

    model_config = ConfigDict(validate_by_name=True, serialize_by_alias=True)

    post_id: str | None
    title: str | None
    description: str = Field(description="하이라이트된 설명 조각. 없으면 설명 앞 150자")
    topic: str | None
    main_category: str | None = Field(alias="mainCategory")
    sub_category: str | None = Field(alias="subCategory")
    tags: list[str] | None
    author: str | None
    language: str | None
    created_at: str | None = Field(alias="createdAt")
    updated_at: str | None = Field(alias="updatedAt")
    score: float | None = Field(description="관련도 점수. 날짜 정렬이면 null")
    highlight: dict[str, list[str]]


class SearchResult(BaseModel):
    """검색 결과 한 페이지."""

    total: int
    page: int
    page_size: int
    total_pages: int
    post_ids: list[str | None]
    results: list[SearchHit]
    aggregations: dict[str, Any] = Field(description="집계를 요청하지 않아 항상 빈 객체다 (계약상 키 유지)")


class AutocompleteResult(BaseModel):
    suggestions: list[str]
    query: str


class PopularKeyword(BaseModel):
    query: str
    count: int


class PopularKeywords(BaseModel):
    popular_searches: list[PopularKeyword]


class CategoryList(BaseModel):
    categories: list[str]
