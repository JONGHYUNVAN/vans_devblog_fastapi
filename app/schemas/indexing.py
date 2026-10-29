"""내부 색인 API 요청·응답. Post BE(NestJS)가 게시글을 만들거나 고치거나 지울 때 보낸다."""

from typing import Any, Literal

from pydantic import BaseModel, Field


class SearchIndexPayload(BaseModel):
    """색인할 게시글. 이름과 필드는 NestJS 의 SearchIndexPayload 와 같다.

    post_id 외에는 빠져도 받는다. 빠진 필드의 기본값은 Django 버전과 같다.
    """

    post_id: str = Field(min_length=1)
    title: str | None = ""
    description: str | None = ""
    content: dict[str, Any] | str | None = Field(None, description="Tiptap JSON. 평문으로 바꿔 색인한다")
    topic: str | None = ""
    tags: list[str] | None = Field(default_factory=list)
    main_category: str | None = Field("", alias="mainCategory")
    sub_category: str | None = Field("", alias="subCategory")
    language: str | None = "ko"
    author: str | None = Field("", description="작성자 닉네임")
    created_at: str | None = Field(None, alias="createdAt", description="ISO 8601. 받은 문자열 그대로 저장한다")
    updated_at: str | None = Field(None, alias="updatedAt", description="ISO 8601. 받은 문자열 그대로 저장한다")


class IndexingResult(BaseModel):
    status: Literal["indexed", "deleted"]
    post_id: str
