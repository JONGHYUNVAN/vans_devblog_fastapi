"""용어 사전 도메인: 표제어·뜻·글→뜻 매핑과 추출 기록 (설계 08 문서 2.1절, 07 문서 D-63).

사전의 단위는 뜻이다. 툴팁이 "이 글에서 쓰인 뜻"과 "같은 표제어의 다른 뜻"을 함께 보이려면 의미 자체가 식별자여야 해서,
뜻 키는 단어도 영역도 담지 않는 순번이고 영역은 뜻의 속성(domains)이다("트랜잭션"은 DB 글에서나 백엔드 글에서나 같은 뜻이다,
2026-11-07 사용자 결정). 같은 단어의 다른 의미는 다른 뜻 키, 다른 단어(별칭·번역·음차)의 같은 의미는 같은 뜻 키다.
추출(services/glossary.py)이 PROPOSED 로 만들고 사람이 CLI 로 승인하며, 읽기 API(services/glossary_views.py)는 뜻과
표제어가 모두 APPROVED 인 것만 보인다. 저장은 상태와 무관하게 하므로 "승인 전 표시 금지"는 읽는 쪽의 책임이다.
"""

import re
from datetime import UTC, datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from app.models.enums import GlossaryStatus

# 정의는 사전체 명사구다(D-88): "…객체들의 묶음" 처럼 명사로 끝나고 "~이다."·"~한다." 같은 문장 어미로 끝나지 않는다.
# 끝 글자 "다" 로 잰다. 한국어 문장 어미는 거의 다 "다" 로 끝나고, "람다" 처럼 다로 끝나는 명사로 정의를 맺는 일은 드물다.
# 걸리면 고쳐 쓰게 한다(모델은 재시도 안내로, 사람은 승인 오류로). 어미를 정규식으로 바꾸지 않는다: "한다"→? 는 뜻을 모른다
_SENTENCE_ENDING = re.compile(r"다[.!?]?$")
_INNER_SENTENCE_END = re.compile(r"다[.!?]\s")  # "…묶음이다. 루트로…" 처럼 두 문장으로 쓴 정의


def normalize_definition(definition: str) -> str:
    """앞뒤 공백과 끝 마침표를 뗀다. 사전체 명사구는 마침표로 닫지 않는다(D-88). 어미는 건드리지 않는다."""
    return definition.strip().rstrip(".").rstrip()


def dictionary_style_problem(definition: str) -> str | None:
    """정의가 사전체 명사구가 아니면 그 사유, 맞으면 None. 사유는 모델의 재시도 안내와 CLI 오류에 그대로 쓰인다."""
    text = normalize_definition(definition)
    if not text:
        return "definition is empty"
    if _SENTENCE_ENDING.search(text) or _INNER_SENTENCE_END.search(text):
        return (
            "definition must be one Korean dictionary-style noun phrase that ends with a noun, "
            "for example '하나의 일관성 경계를 이루고 외부에서 루트를 통해서만 접근하는 객체들의 묶음', "
            f"not a sentence ending in '~다' such as '~이다' or '~한다' (got '…{text[-24:]}')"
        )
    return None


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
    """영역 슬러그의 표시 이름. 모르는 슬러그는 그대로 돌려줘 옛 데이터가 응답을 깨지 않게 한다."""
    return DOMAINS.get(slug, slug)


class ProposalSource(BaseModel):
    """어느 글의 어느 호출이 제안했는지. `agents glossary pending <draft_id>` 와 approve_all 이 글 단위로 거르는 근거다."""

    draft_id: str
    call_id: str


class AliasProposal(BaseModel):
    """승인 전 별칭 제안. same_as 로 맞춘 표기가 표제어의 조회 키에 없을 때 생기고, 승인되면 aliases 로 옮겨 간다."""

    alias: str
    source: ProposalSource


class GlossaryHeadword(BaseModel):
    """glossary_headwords 문서. 표기와 별칭을 묶고 뜻은 품지 않는다.

    headword_key 는 식별자라 사람이 term_en 을 고쳐도 바뀌지 않고 lookup_keys 만 다시 계산된다(08 문서 2.1절 불변식).
    한 조회 키는 표제어 하나에만 속한다: 리포지토리의 unique 다중키 인덱스가 강제하고 저장 때 DuplicateKeyError 로 드러난다.
    """

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
        """읽기 API 가 보여도 되는 상태인지. 항목은 뜻과 표제어 둘 다 참이어야 보인다."""
        return self.status == GlossaryStatus.APPROVED


class GlossarySense(BaseModel):
    """glossary_senses 문서. 사전의 단위다.

    뜻 하나는 의미 하나다. 영역이 다른 글이 같은 정의로 쓰면 같은 뜻이고 domains 에 영역을 더한다. 표제어에 APPROVED 뜻이
    둘 이상이면 모든 뜻에 distinguisher 가 있어야 한다(승인 때 검사). post_ids 는 발행 따라 쓰기가 $addToSet 으로 더한다.
    """

    model_config = ConfigDict(validate_by_name=True)

    sense_key: str = Field(alias="_id")  # 의미의 식별자. 순번 "s00042". 단어·영역을 담지 않는다
    headword_key: str
    domains: list[str] = Field(min_length=1)  # DOMAINS 의 슬러그. 첫째가 주 영역
    term_ko: str | None = None  # 뜻마다 표기가 다를 때만. 없으면 표제어 값
    # 사전체 명사구(D-88). 추출 계약과 승인이 검사한다. 규칙 전 정의는 "~다" 문장일 수 있다(check 가 알린다)
    definition: str
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
        """읽기 API 가 보여도 되는 상태인지. REJECTED·PROPOSED 뜻은 글이 가리켜도 보이지 않는다."""
        return self.status == GlossaryStatus.APPROVED


# 뜻이 어떻게 정해졌는지. EXACT·SAME_AS·NEW 는 등록 확인 ①②④(08 문서 2.2절), HUMAN 은 CLI resolve 다. 지표(D-65)용이라 동작에는 안 쓴다
ItemResolutionKind = Literal["EXACT", "SAME_AS", "NEW", "HUMAN"]


class PostGlossaryItem(BaseModel):
    """글 하나에서 뜻 하나를 가리키는 항목. 글 안에서 sense_key 도 surface 도 중복이 없다(추출 코드와 resolve 가 지킨다)."""

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
    """추출이 버린 후보와 사유. 되살리는 길은 없고(보류는 PendingTerm) 프롬프트 튜닝 지표로만 남긴다."""

    surface: str
    reason: str


class GlossaryRecord(BaseModel):
    """PostDraft.glossary. 추출 단계의 결과. 호출이 실패해도 남는다 (PolishRecord 와 같은 역할).

    post_review_stage 와 한 번에 저장돼 재개 때 추출을 되풀이하지 않는다(D-49). 발행 뒤 build_post_glossary 가 게시글 id 로 옮긴다.
    """

    target_version: int
    call_id: str | None = None
    items: list[PostGlossaryItem] = Field(default_factory=list)
    pending: list[PendingTerm] = Field(default_factory=list)
    discarded: list[DiscardedTerm] = Field(default_factory=list)
    skipped: str | None = None  # 호출 실패·라우트 없음 사유. 있으면 items 는 비어 있다
    known_headwords_sent: int = 0  # 입력에 넣은 표제어 수
    extracted_at: datetime = Field(default_factory=_now)


class PostGlossary(BaseModel):
    """post_glossary 문서. 발행 뒤 GlossaryRecord 를 게시글 id 로 옮긴 것. 사람의 resolve·drop 은 여기에만 쓴다.

    _id 가 Post BE 게시글 id 인 것은 프런트가 글 id 하나로 읽기 위해서다. discarded 는 옮기지 않는다(글 문서에 남는다).
    """

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
