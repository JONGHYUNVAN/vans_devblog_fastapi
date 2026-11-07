"""용어 사전 도메인: 표제어·뜻·글→뜻 매핑과 추출 기록 (설계 08 문서 2.1절).

사전의 단위는 뜻이다. 표제어는 표기와 별칭을 묶고, 뜻 키는 단어도 영역도 담지 않는 순번이다.
같은 단어의 다른 의미는 다른 뜻 키, 다른 단어(별칭·번역·음차)의 같은 의미는 같은 뜻 키, 영역이 다른 글의 같은 의미도 같은 뜻 키다.
"""

from datetime import UTC, datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from app.models.enums import GlossaryStatus

# 영역 태그 → 표시 이름. 뜻의 속성이지 키의 일부가 아니다. 표시 이름은 저장하지 않고 여기서 찾는다. 늘리는 것은 코드 변경이다 (08 문서 Q-30)
DOMAINS: dict[str, str] = {
    "db": "데이터베이스",
    "backend": "백엔드",
    "frontend": "프런트엔드",
    "auth": "인증",
    "llm": "언어 모델",
    "network": "네트워크",
    "os": "운영체제",
    "devops": "배포·운영",
    "search": "검색",
    "algorithm": "알고리즘",
    "lang": "언어·런타임",
    "other": "기타",
}
OTHER_DOMAIN = "other"


def _now() -> datetime:
    return datetime.now(UTC)


def domain_label(slug: str) -> str:
    return DOMAINS.get(slug, slug)


class ProposalSource(BaseModel):
    """어느 글의 어느 호출이 제안했는지."""

    draft_id: str
    call_id: str


class AliasProposal(BaseModel):
    alias: str
    source: ProposalSource


class GlossaryHeadword(BaseModel):
    """glossary_headwords 문서. 표기와 별칭. 뜻은 품지 않는다."""

    model_config = ConfigDict(validate_by_name=True)

    headword_key: str = Field(alias="_id")  # normalize_key(term_en or term_ko)
    term_ko: str  # 표준 한국어 표기. 작성자 표기 표준의 원천
    term_en: str | None = None  # 영어 원어. 없으면 한국어만 있는 용어
    aliases: list[str] = Field(default_factory=list)  # 승인된 다른 표기
    lookup_keys: list[str] = Field(default_factory=list)  # 정확 일치 조회와 unique 인덱스의 대상
    proposed_aliases: list[AliasProposal] = Field(default_factory=list)  # 승인 전 별칭. 조회에 쓰지 않는다
    similar_headwords: list[str] = Field(default_factory=list)  # 제안 때 코드가 찾은 비슷한 표제어 키. 사람 참고용
    status: GlossaryStatus = GlossaryStatus.PROPOSED
    merged_into: str | None = None
    proposed_by: ProposalSource | None = None
    approved_by: str | None = None
    approved_at: datetime | None = None
    created_at: datetime = Field(default_factory=_now)
    updated_at: datetime = Field(default_factory=_now)

    @property
    def is_visible(self) -> bool:
        return self.status == GlossaryStatus.APPROVED


class GlossarySense(BaseModel):
    """glossary_senses 문서. 사전의 단위."""

    model_config = ConfigDict(validate_by_name=True)

    sense_key: str = Field(alias="_id")  # 의미의 식별자. 순번 "s00042". 단어·영역을 담지 않는다
    headword_key: str
    domains: list[str] = Field(min_length=1)  # DOMAINS 의 슬러그. 첫째가 주 영역
    term_ko: str | None = None  # 뜻마다 표기가 다를 때만. 없으면 표제어 값
    definition: str  # 2문장 이하, "~다" 어미
    distinguisher: str = ""  # 한 줄. 표제어에 승인된 뜻이 둘 이상이면 필수
    status: GlossaryStatus = GlossaryStatus.PROPOSED
    post_ids: list[str] = Field(default_factory=list)  # 이 뜻을 가리키는 발행 글 (Post BE 게시글 id)
    proposed_by: ProposalSource | None = None
    approved_by: str | None = None
    approved_at: datetime | None = None
    created_at: datetime = Field(default_factory=_now)
    updated_at: datetime = Field(default_factory=_now)

    @property
    def is_visible(self) -> bool:
        return self.status == GlossaryStatus.APPROVED


ItemResolutionKind = Literal["EXACT", "SAME_AS", "NEW", "HUMAN"]


class PostGlossaryItem(BaseModel):
    sense_key: str
    surface: str  # 본문에 실제로 나온 표면형. 프런트가 정확 일치로 첫 등장을 감싼다
    section_id: str
    resolution: ItemResolutionKind  # 어떻게 뜻이 정해졌는지 (지표용)


class PendingTerm(BaseModel):
    """뜻을 정하지 못한 후보. 사람이 resolve 하거나 drop 한다. 표시하지 않는다."""

    term_ko: str
    term_en: str | None = None
    surface: str
    section_id: str
    headword_key: str | None = None  # 표제어는 정해졌는데 뜻을 못 고른 경우
    sense_candidates: list[str] = Field(default_factory=list)  # 그 표제어의 뜻 키만
    similar_headwords: list[str] = Field(default_factory=list)  # 사람 참고용
    reason: str


class DiscardedTerm(BaseModel):
    surface: str
    reason: str


class GlossaryRecord(BaseModel):
    """PostDraft.glossary. 추출 단계의 결과. 호출이 실패해도 남는다 (PolishRecord 와 같은 역할)."""

    target_version: int
    call_id: str | None = None
    items: list[PostGlossaryItem] = Field(default_factory=list)
    pending: list[PendingTerm] = Field(default_factory=list)
    discarded: list[DiscardedTerm] = Field(default_factory=list)
    skipped: str | None = None  # 호출 실패·라우트 없음 사유. 있으면 items 는 비어 있다
    known_headwords_sent: int = 0  # 입력에 넣은 표제어 수
    extracted_at: datetime = Field(default_factory=_now)


class PostGlossary(BaseModel):
    """post_glossary 문서. 발행 뒤 GlossaryRecord 를 게시글 id 로 옮긴 것. 사람의 resolve·drop 은 여기에만 쓴다."""

    model_config = ConfigDict(validate_by_name=True)

    post_id: str = Field(alias="_id")  # Post BE 게시글 id (= Publication.external_post_id)
    draft_id: str
    round_id: str
    content_version: int
    call_id: str | None = None
    prompt_template_version: str | None = None  # 옛 영수증의 프롬프트 디렉터리 이름. 1a 부터는 pipeline_version
    pipeline_version: str | None = None
    items: list[PostGlossaryItem] = Field(default_factory=list)
    pending: list[PendingTerm] = Field(default_factory=list)
    extracted_at: datetime = Field(default_factory=_now)
    updated_at: datetime = Field(default_factory=_now)


class PostRefRecord(BaseModel):
    """Post BE 의 posts 컬렉션에서 읽은 글 한 줄. 뜻의 글 목록 링크용 (/post/view/{main_category}/{post_id})."""

    post_id: str
    title: str
    main_category: str
