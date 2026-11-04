"""Post BE(vans_story_be_post) 클라이언트 계약 (설계 06 문서 2절·3절 clients/publisher).

clients 는 models 를 import 하지 않으므로 카테고리·발행 타입을 여기 둔다. 메인·서브 쌍(CategoryOption)으로 묶는 일과
CreatePostDto 매핑(본문 Tiptap 변환, 요약 노트)은 서비스가 한다.
"""

from typing import Any, Protocol

from pydantic import BaseModel, ConfigDict, Field, SecretStr


class PublisherAccount(BaseModel):
    """vans_story_be 관리자 계정 하나. 이 계정으로 로그인해 만든 글의 작성자가 된다 (설계 07 문서 D-45)."""

    email: str
    password: SecretStr


class PostCategory(BaseModel):
    """GET /api/v1/categories 응답 한 건. parent_id 가 None 이면 메인 카테고리다."""

    id: str
    parent_id: str | None = None
    value: str
    label: str
    is_active: bool = True


class PostCreatePayload(BaseModel):
    """POST /api/v1/posts 의 CreatePostDto. 전송 JSON 의 필드 이름은 DTO 그대로(camelCase)다 (설계 04 문서 8절 매핑표).

    thumbnail 은 보내지 않는다.
    """

    model_config = ConfigDict(validate_by_name=True)

    title: str
    content: dict[str, Any]  # Tiptap JSON (본문 + 요약 노트)
    description: str
    tags: list[str]
    topic: str
    main_category_id: str = Field(alias="mainCategoryId")
    sub_category_id: str = Field(alias="subCategoryId")
    language: str

    def to_dto(self) -> dict[str, Any]:
        return self.model_dump(by_alias=True)


class PublishResult(BaseModel):
    external_post_id: str  # 응답의 _id


class Publisher(Protocol):
    async def list_categories(self) -> list[PostCategory]:
        """활성 카테고리 전부. 실패는 PublishFailedError (라운드를 만들지 않는다)."""
        ...

    def has_account(self, provider: str) -> bool:
        """이 벤더의 발행 계정으로 로그인할 설정이 있는가. 발행 의도를 저장하기 전에 검사한다."""
        ...

    async def publish(self, payload: PostCreatePayload, provider: str) -> PublishResult:
        """작성 벤더의 계정으로 로그인한 뒤 글을 만든다. 결과가 확정된 실패는 PublishFailedError, 결과 불명은 PublishUncertainError (설계 04 문서 8절)."""
        ...
