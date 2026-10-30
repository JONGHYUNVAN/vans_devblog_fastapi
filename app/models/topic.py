"""주제 제안·카테고리 선택지와 라운드 간 중복 방지 레지스트리 (설계 02 문서 2~3절)."""

from datetime import UTC, datetime

from pydantic import BaseModel, ConfigDict, Field

from app.models.enums import TopicRegistryStatus


class TopicProposal(BaseModel):
    title: str = Field(max_length=200)  # 발행 때 Post BE topic 으로도 보낸다
    audience: str  # 핵심 독자층
    keywords: list[str] = Field(min_length=3, max_length=3)  # 발행 때 Post BE tags
    outline: list[str] = Field(min_length=3)  # 목차 초안
    rationale: str  # 왜 이 주제를 이 페르소나가 쓰는지
    main_category_id: str  # 라운드 categories 의 한 쌍. 파서가 목록에 있는지 검사한다
    sub_category_id: str


class CategoryOption(BaseModel):
    """Post BE 서브 카테고리 하나와 그 메인 카테고리. 라운드 생성 때 GET /api/v1/categories 로 만든다."""

    main_category_id: str  # parentId 가 null 인 Category
    main_label: str
    sub_category_id: str  # parentId 가 main_category_id 인 Category
    sub_label: str


class TopicRegistryEntry(BaseModel):
    """agent_topics 컬렉션 문서. 확정된 주제 하나. _id 는 그 글의 draft_id 다(결정론적 ID, 설계 02 문서 6절)."""

    model_config = ConfigDict(validate_by_name=True)

    entry_id: str = Field(alias="_id")
    title: str
    keywords: list[str]
    embedding: list[float] | None = None  # 설계 07 문서 Q-05. 1차는 쓰지 않는다
    draft_id: str
    round_id: str
    status: TopicRegistryStatus = TopicRegistryStatus.RESERVED
    published_at: datetime | None = None
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
