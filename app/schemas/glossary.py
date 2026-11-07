"""용어 사전 공개 읽기 API 응답 (설계 08 문서 2.6절, D-66).

vans_story 의 BFF·서버 컴포넌트가 키 없이 읽는 계약이다. 저장 모델(models/glossary.py)과 다른 점은 상태 필드가 없다는
것이다: 응답에 실린 것은 전부 뜻과 표제어가 APPROVED 라, 프런트가 상태를 다시 보지 않아도 된다. 영역은 슬러그와 표시 이름을
같이 주어 프런트가 DOMAINS 표를 복사하지 않게 한다. 변환은 services/glossary_views.py, 골든은 tests/golden/glossary_*.json.
필드를 빼거나 이름을 바꾸면 vans_story 의 src/types/glossary.ts 와 골든을 같이 고친다.
"""

from datetime import datetime

from pydantic import BaseModel


class DomainPublic(BaseModel):
    """영역 한 칸. 프런트는 label 만 보이고 slug 는 스타일·필터 키로 쓴다."""

    slug: str
    label: str  # DOMAINS 표의 표시 이름


class OtherSensePublic(BaseModel):
    """같은 표제어의 다른 승인된 뜻. 툴팁 아래쪽 "다른 영역에서는" 목록."""

    sense_key: str
    domains: list[DomainPublic]
    definition: str


class PostGlossaryItemPublic(BaseModel):
    """글 하나의 용어 항목. 툴팁 위쪽(이 글의 뜻)과 아래쪽(다른 뜻)을 한 번에 담아 글 페이지가 추가 조회 없이 그린다."""

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
    """GET /api/v1/glossary/posts/{post_id}/ 응답. items 가 비어 있을 수 있다(추출은 됐는데 승인된 뜻이 아직 없음)."""

    post_id: str
    items: list[PostGlossaryItemPublic]
    extracted_at: datetime


class PostRef(BaseModel):
    """뜻을 쓰는 발행 글 한 줄. 전역 사전에서 글로 잇는 링크 재료다."""

    post_id: str
    title: str
    main_category: str  # /post/view/{main_category}/{post_id} 링크용


class SensePublic(BaseModel):
    """전역 사전·표제어 조회의 뜻 한 칸. number 는 표제어 안 순번이라 사람이 "뜻 2" 로 부를 수 있다."""

    sense_key: str
    number: int  # 표제어 안 순번
    domains: list[DomainPublic]
    term_ko: str
    definition: str
    distinguisher: str
    posts: list[PostRef]


class HeadwordPublic(BaseModel):
    """표제어와 승인된 뜻 전부. 글 페이지 밖의 키 기반 툴팁과 전역 사전이 같은 모양을 쓴다."""

    key: str
    term_ko: str
    term_en: str | None
    aliases: list[str]
    senses: list[SensePublic]


class GlossaryTermList(BaseModel):
    """GET /api/v1/glossary/terms/ 응답. total 은 q 로 거른 뒤의 전체 수라 페이지 수를 계산할 수 있다."""

    items: list[HeadwordPublic]
    total: int
