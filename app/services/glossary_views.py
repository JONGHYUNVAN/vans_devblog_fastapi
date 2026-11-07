"""용어 사전 읽기 전용 조회와 엔티티 → 응답 변환 (설계 08 문서 2.6절, D-66).

공개 라우터(routers/glossary.py)가 키 없이 부르는 유일한 서비스다. 저장은 상태와 무관하게 되므로 "승인 전 표시 금지" 불변식을
지키는 곳이 여기다: 뜻과 표제어가 모두 APPROVED 인 항목만 응답에 싣고, 그렇지 않은 항목은 글 문서를 건드리지 않고 조용히 뺀다.
글 단위 응답은 툴팁에 필요한 "이 글의 뜻 + 같은 표제어의 다른 뜻" 을 한 번에 담아 프런트가 뜻마다 다시 조회하지 않게 한다.
쓰기는 전혀 하지 않는다. 쓰기는 services/glossary.py(CLI) 뿐이다.
"""

from collections.abc import Iterable

from app.core.exceptions import NotFoundError
from app.models.enums import GlossaryStatus
from app.models.glossary import GlossaryHeadword, GlossarySense, PostRefRecord, domain_label
from app.repositories.glossary import GlossaryRepository
from app.repositories.post_ref import PostRefRepository
from app.schemas.glossary import (
    DomainPublic,
    GlossaryTermList,
    HeadwordPublic,
    OtherSensePublic,
    PostGlossaryItemPublic,
    PostGlossaryPublic,
    PostRef,
    SensePublic,
)
from app.services.glossary_matching import normalize_key

_APPROVED = (GlossaryStatus.APPROVED,)


def _domains(slugs: Iterable[str]) -> list[DomainPublic]:
    """슬러그에 표시 이름을 붙인다. 프런트가 DOMAINS 표를 복사하지 않게 서버가 이름을 준다."""
    return [DomainPublic(slug=slug, label=domain_label(slug)) for slug in slugs]


def _term_ko(sense: GlossarySense, headword: GlossaryHeadword) -> str:
    """뜻마다 표기가 다를 때만 뜻의 값, 아니면 표제어의 값."""
    return sense.term_ko or headword.term_ko


class GlossaryViewService:
    """공개 읽기 API 의 조회와 응답 변환. 저장 모델의 상태를 여기서 걸러 응답에는 승인된 것만 싣는다."""

    def __init__(self, *, repository: GlossaryRepository, post_ref_repository: PostRefRepository | None = None) -> None:
        """post_ref_repository 가 없으면(테스트, Post BE 컬렉션이 없는 DB) 전역 사전의 글 목록만 빈다. 나머지는 같다."""
        self._repo = repository
        self._refs = post_ref_repository

    async def get_post_glossary(self, post_id: str) -> PostGlossaryPublic:
        """그 글의 승인된 항목. 항목마다 같은 표제어의 다른 승인된 뜻을 함께 준다. post_glossary 가 없으면 404."""
        post_glossary = await self._repo.get_post_glossary(post_id)
        if post_glossary is None:
            raise NotFoundError(f"post {post_id} has no glossary")
        # 뜻과 표제어를 따로 거른다. 둘 다 APPROVED 여야 보인다(08 문서 2.1절 불변식). 거절된 표제어의 뜻은 뜻이 승인돼도 숨는다
        senses = {
            sense.sense_key: sense
            for sense in await self._repo.list_senses_by_keys(item.sense_key for item in post_glossary.items)
            if sense.is_visible
        }
        headwords = {
            headword.headword_key: headword
            for headword in await self._repo.list_headwords_by_keys(sense.headword_key for sense in senses.values())
            if headword.is_visible
        }
        # 같은 표제어의 다른 승인된 뜻이 툴팁 아래쪽 "다른 영역에서는" 이다(D-66). 글 페이지가 뜻마다 다시 조회하지 않게 여기서 다 싣는다
        siblings: dict[str, list[GlossarySense]] = {}
        for sense in await self._repo.list_senses_by_headwords(headwords, _APPROVED):
            siblings.setdefault(sense.headword_key, []).append(sense)
        items = []
        for item in post_glossary.items:
            sense = senses.get(item.sense_key)
            headword = headwords.get(sense.headword_key) if sense is not None else None
            if sense is None or headword is None:
                continue  # 보류·거절 중인 뜻을 가리키는 항목은 조용히 빠진다. 글 문서는 그대로라 승인되면 바로 보인다
            # 뜻이 하나면 다른 뜻 목록도 구분 문구도 비운다. 구분 문구는 "어느 뜻인지" 가를 때만 뜻이 있다(표시 규칙)
            others = [each for each in siblings.get(headword.headword_key, []) if each.sense_key != sense.sense_key]
            items.append(
                PostGlossaryItemPublic(
                    sense_key=sense.sense_key,
                    headword_key=headword.headword_key,
                    surface=item.surface,
                    section_id=item.section_id,
                    term_ko=_term_ko(sense, headword),
                    term_en=headword.term_en,
                    domains=_domains(sense.domains),
                    definition=sense.definition,
                    distinguisher=sense.distinguisher if others else "",
                    other_senses=[
                        OtherSensePublic(
                            sense_key=each.sense_key, domains=_domains(each.domains), definition=each.definition
                        )
                        for each in others
                    ],
                )
            )
        return PostGlossaryPublic(post_id=post_id, items=items, extracted_at=post_glossary.extracted_at)

    async def get_headword(self, headword_key: str) -> HeadwordPublic:
        """표제어와 승인된 뜻 전부. 없거나 승인 전이면 NotFoundError(라우터가 404). 키 기반 툴팁이 글 페이지 밖에서 쓴다."""
        headword = await self._repo.get_headword(headword_key)
        if headword is None or not headword.is_visible:
            raise NotFoundError(f"headword {headword_key} does not exist")  # 승인 전 표제어의 존재도 밖에 알리지 않는다
        [public] = await self._build_headwords([headword])
        return public

    async def list_terms(self, *, q: str | None = None, page: int = 1, size: int = 50) -> GlossaryTermList:
        """APPROVED 표제어와 뜻. q 는 정규화 키의 부분 일치. ES 없이 Mongo 만 (08 문서 2.9절)."""
        # 전부 읽어 메모리에서 거르고 자른다. 사전이 수백 개 규모라 ES 색인을 두지 않았다. 커지면 그때 파생 인덱스다(2.9절)
        headwords = await self._repo.list_headwords(_APPROVED)
        if q:
            needle = normalize_key(q)
            headwords = [each for each in headwords if any(needle in key for key in each.lookup_keys)]
        headwords.sort(key=lambda each: (each.term_ko, each.headword_key))
        total = len(headwords)
        start = (page - 1) * size
        return GlossaryTermList(items=await self._build_headwords(headwords[start : start + size]), total=total)

    async def _build_headwords(self, headwords: list[GlossaryHeadword]) -> list[HeadwordPublic]:
        """표제어 묶음을 승인된 뜻·글 목록과 함께 응답으로. 뜻과 글 참조를 한 번씩만 읽는다(표제어마다 왕복하지 않는다)."""
        senses_by_headword: dict[str, list[GlossarySense]] = {}
        senses = await self._repo.list_senses_by_headwords((each.headword_key for each in headwords), _APPROVED)
        for sense in senses:
            senses_by_headword.setdefault(sense.headword_key, []).append(sense)
        refs = await self._post_refs(post_id for sense in senses for post_id in sense.post_ids)
        # number 는 표제어 안 순번(생성 순)이다. 사람이 "뜻 2" 라 부르는 데 쓰고 식별자는 sense_key 다
        result = []
        for headword in headwords:
            own = senses_by_headword.get(headword.headword_key, [])
            result.append(
                HeadwordPublic(
                    key=headword.headword_key,
                    term_ko=headword.term_ko,
                    term_en=headword.term_en,
                    aliases=list(headword.aliases),
                    senses=[
                        SensePublic(
                            sense_key=sense.sense_key,
                            number=number,
                            domains=_domains(sense.domains),
                            term_ko=_term_ko(sense, headword),
                            definition=sense.definition,
                            distinguisher=sense.distinguisher if len(own) > 1 else "",
                            posts=[
                                PostRef(post_id=ref.post_id, title=ref.title, main_category=ref.main_category)
                                for ref in (refs.get(post_id) for post_id in sense.post_ids)
                                if ref is not None
                            ],
                        )
                        for number, sense in enumerate(own, start=1)
                    ],
                )
            )
        return result

    async def _post_refs(self, post_ids: Iterable[str]) -> dict[str, PostRefRecord]:
        """뜻의 post_ids 를 제목·카테고리로. Post BE 컬렉션을 못 읽는 구성이면 빈 dict 라 링크만 빠진다."""
        if self._refs is None:
            return {}
        return await self._refs.list_refs(post_ids)
