"""용어 사전 공개 읽기 API 응답 (설계 08 문서 2.6절). 변환은 services/glossary_views.py 가 한다. 골든은 tests/golden/glossary_*.json."""

from datetime import datetime

from pydantic import BaseModel


class DomainPublic(BaseModel):
    slug: str
    label: str  # DOMAINS 표의 표시 이름


class OtherSensePublic(BaseModel):
    """같은 표제어의 다른 승인된 뜻. 툴팁 아래쪽 "다른 영역에서는" 목록."""

    sense_key: str
    domains: list[DomainPublic]
    definition: str


class PostGlossaryItemPublic(BaseModel):
    sense_key: str
    headword_key: str
    surface: str
    section_id: str
    term_ko: str  # 뜻의 term_ko 가 있으면 그것, 없으면 표제어 값
    term_en: str | None
    domains: list[DomainPublic]  # 첫째가 주 영역
    definition: str
    distinguisher: str  # 다른 뜻이 있을 때 영역 라벨 옆에 보인다
    other_senses: list[OtherSensePublic]  # 비어 있으면 뜻이 하나다


class PostGlossaryPublic(BaseModel):
    post_id: str
    items: list[PostGlossaryItemPublic]
    extracted_at: datetime


class PostRef(BaseModel):
    post_id: str
    title: str
    main_category: str  # /post/view/{main_category}/{post_id} 링크용


class SensePublic(BaseModel):
    sense_key: str
    number: int  # 표제어 안 순번
    domains: list[DomainPublic]
    term_ko: str
    definition: str
    distinguisher: str
    posts: list[PostRef]


class HeadwordPublic(BaseModel):
    key: str
    term_ko: str
    term_en: str | None
    aliases: list[str]
    senses: list[SensePublic]


class GlossaryTermList(BaseModel):
    items: list[HeadwordPublic]
    total: int
