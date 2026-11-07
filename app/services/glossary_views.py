"""용어 사전 읽기 전용 조회와 엔티티 → 응답 변환 (설계 08 문서 2.6절). 표시는 뜻과 표제어가 모두 APPROVED 인 항목만."""

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
    return [DomainPublic(slug=slug, label=domain_label(slug)) for slug in slugs]


def _term_ko(sense: GlossarySense, headword: GlossaryHeadword) -> str:
    return sense.term_ko or headword.term_ko


class GlossaryViewService:
    def __init__(self, *, repository: GlossaryRepository, post_ref_repository: PostRefRepository | None = None) -> None:
        self._repo = repository
        self._refs = post_ref_repository

    async def get_post_glossary(self, post_id: str) -> PostGlossaryPublic:
        """그 글의 승인된 항목. 항목마다 같은 표제어의 다른 승인된 뜻을 함께 준다. post_glossary 가 없으면 404."""
        post_glossary = await self._repo.get_post_glossary(post_id)
        if post_glossary is None:
            raise NotFoundError(f"post {post_id} has no glossary")
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
        siblings: dict[str, list[GlossarySense]] = {}
        for sense in await self._repo.list_senses_by_headwords(headwords, _APPROVED):
            siblings.setdefault(sense.headword_key, []).append(sense)
        items = []
        for item in post_glossary.items:
            sense = senses.get(item.sense_key)
            headword = headwords.get(sense.headword_key) if sense is not None else None
            if sense is None or headword is None:
                continue
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
        headword = await self._repo.get_headword(headword_key)
        if headword is None or not headword.is_visible:
            raise NotFoundError(f"headword {headword_key} does not exist")
        [public] = await self._build_headwords([headword])
        return public

    async def list_terms(self, *, q: str | None = None, page: int = 1, size: int = 50) -> GlossaryTermList:
        """APPROVED 표제어와 뜻. q 는 정규화 키의 부분 일치. ES 없이 Mongo 만 (08 문서 2.9절)."""
        headwords = await self._repo.list_headwords(_APPROVED)
        if q:
            needle = normalize_key(q)
            headwords = [each for each in headwords if any(needle in key for key in each.lookup_keys)]
        headwords.sort(key=lambda each: (each.term_ko, each.headword_key))
        total = len(headwords)
        start = (page - 1) * size
        return GlossaryTermList(items=await self._build_headwords(headwords[start : start + size]), total=total)

    async def _build_headwords(self, headwords: list[GlossaryHeadword]) -> list[HeadwordPublic]:
        senses_by_headword: dict[str, list[GlossarySense]] = {}
        senses = await self._repo.list_senses_by_headwords((each.headword_key for each in headwords), _APPROVED)
        for sense in senses:
            senses_by_headword.setdefault(sense.headword_key, []).append(sense)
        refs = await self._post_refs(post_id for sense in senses for post_id in sense.post_ids)
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
        if self._refs is None:
            return {}
        return await self._refs.list_refs(post_ids)
