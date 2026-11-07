"""용어 사전 컬렉션 셋(glossary_headwords·glossary_senses·post_glossary)과 뜻 키 순번 (설계 08 문서 2.1절)."""

from collections.abc import Iterable

from pymongo import ReturnDocument
from pymongo.asynchronous.database import AsyncDatabase
from pymongo.errors import DuplicateKeyError

from app.models.enums import GlossaryStatus
from app.models.glossary import GlossaryHeadword, GlossarySense, PostGlossary

GLOSSARY_HEADWORDS_COLLECTION = "glossary_headwords"
GLOSSARY_SENSES_COLLECTION = "glossary_senses"
POST_GLOSSARY_COLLECTION = "post_glossary"
GLOSSARY_COUNTERS_COLLECTION = "glossary_counters"
_SENSE_COUNTER_ID = "sense"


class GlossaryRepository:
    """세 컬렉션의 조회·저장. 별칭이 표제어 하나에만 속하는 것은 lookup_keys 의 unique 다중키 인덱스가 강제한다."""

    def __init__(self, database: AsyncDatabase) -> None:
        self._headwords = database[GLOSSARY_HEADWORDS_COLLECTION]
        self._senses = database[GLOSSARY_SENSES_COLLECTION]
        self._posts = database[POST_GLOSSARY_COLLECTION]
        self._counters = database[GLOSSARY_COUNTERS_COLLECTION]

    async def ensure_indexes(self) -> None:
        await self._headwords.create_index("lookup_keys", unique=True, name="lookup_keys_unique")
        await self._headwords.create_index("status")
        await self._senses.create_index("headword_key")
        await self._senses.create_index([("status", 1), ("headword_key", 1)])
        await self._senses.create_index("post_ids")
        await self._posts.create_index("draft_id", unique=True)
        await self._posts.create_index("items.sense_key")

    # ── 표제어 ──────────────────────────────────────────────────────

    async def get_headword(self, headword_key: str) -> GlossaryHeadword | None:
        doc = await self._headwords.find_one({"_id": headword_key})
        return GlossaryHeadword.model_validate(doc) if doc else None

    async def find_headword_by_lookup(self, lookup_key: str) -> GlossaryHeadword | None:
        """정규화 키가 표기·별칭 중 하나와 정확히 같은 표제어."""
        doc = await self._headwords.find_one({"lookup_keys": lookup_key})
        return GlossaryHeadword.model_validate(doc) if doc else None

    async def list_headwords(self, statuses: Iterable[GlossaryStatus] | None = None) -> list[GlossaryHeadword]:
        """키 순."""
        query = {"status": {"$in": [status.value for status in statuses]}} if statuses is not None else {}
        docs = await self._headwords.find(query).sort("_id", 1).to_list()
        return [GlossaryHeadword.model_validate(doc) for doc in docs]

    async def list_headwords_by_keys(self, headword_keys: Iterable[str]) -> list[GlossaryHeadword]:
        keys = list(dict.fromkeys(headword_keys))
        if not keys:
            return []
        docs = await self._headwords.find({"_id": {"$in": keys}}).sort("_id", 1).to_list()
        return [GlossaryHeadword.model_validate(doc) for doc in docs]

    async def add_headword_if_missing(self, headword: GlossaryHeadword) -> bool:
        """같은 키나 같은 조회 키가 없을 때만 넣는다. 넣었으면 True."""
        try:
            await self._headwords.insert_one(headword.model_dump(by_alias=True))
        except DuplicateKeyError:
            return False
        return True

    async def save_headword(self, headword: GlossaryHeadword) -> None:
        """덮어쓴다. 다른 표제어가 가진 조회 키를 넣으면 DuplicateKeyError 가 그대로 난다."""
        await self._headwords.replace_one(
            {"_id": headword.headword_key}, headword.model_dump(by_alias=True), upsert=True
        )

    # ── 뜻 ───────────────────────────────────────────────────────────

    async def next_sense_key(self) -> str:
        """순번 발급. 단어도 영역도 담지 않는 의미의 식별자다 (08 문서 D-63)."""
        doc = await self._counters.find_one_and_update(
            {"_id": _SENSE_COUNTER_ID},
            {"$inc": {"seq": 1}},
            upsert=True,
            return_document=ReturnDocument.AFTER,
        )
        return f"s{doc['seq']:05d}"

    async def get_sense(self, sense_key: str) -> GlossarySense | None:
        doc = await self._senses.find_one({"_id": sense_key})
        return GlossarySense.model_validate(doc) if doc else None

    async def list_senses(
        self, headword_key: str | None = None, statuses: Iterable[GlossaryStatus] | None = None
    ) -> list[GlossarySense]:
        """생성 순."""
        query: dict = {}
        if headword_key is not None:
            query["headword_key"] = headword_key
        if statuses is not None:
            query["status"] = {"$in": [status.value for status in statuses]}
        docs = await self._senses.find(query).sort("created_at", 1).to_list()
        return [GlossarySense.model_validate(doc) for doc in docs]

    async def list_senses_by_headwords(
        self, headword_keys: Iterable[str], statuses: Iterable[GlossaryStatus] | None = None
    ) -> list[GlossarySense]:
        """여러 표제어의 뜻을 한 번에. 생성 순."""
        keys = list(dict.fromkeys(headword_keys))
        if not keys:
            return []
        query: dict = {"headword_key": {"$in": keys}}
        if statuses is not None:
            query["status"] = {"$in": [status.value for status in statuses]}
        docs = await self._senses.find(query).sort("created_at", 1).to_list()
        return [GlossarySense.model_validate(doc) for doc in docs]

    async def list_senses_by_keys(self, sense_keys: Iterable[str]) -> list[GlossarySense]:
        keys = list(dict.fromkeys(sense_keys))
        if not keys:
            return []
        docs = await self._senses.find({"_id": {"$in": keys}}).sort("created_at", 1).to_list()
        return [GlossarySense.model_validate(doc) for doc in docs]

    async def add_sense(self, sense: GlossarySense) -> None:
        await self._senses.insert_one(sense.model_dump(by_alias=True))

    async def save_sense(self, sense: GlossarySense) -> None:
        await self._senses.replace_one({"_id": sense.sense_key}, sense.model_dump(by_alias=True), upsert=True)

    async def add_post_to_senses(self, sense_keys: Iterable[str], post_id: str) -> None:
        """발행 글을 뜻의 글 목록에 더한다. 되풀이해도 한 번만 들어간다."""
        keys = list(dict.fromkeys(sense_keys))
        if keys:
            await self._senses.update_many({"_id": {"$in": keys}}, {"$addToSet": {"post_ids": post_id}})

    # ── 글→뜻 매핑 ───────────────────────────────────────────────────

    async def get_post_glossary(self, post_id: str) -> PostGlossary | None:
        doc = await self._posts.find_one({"_id": post_id})
        return PostGlossary.model_validate(doc) if doc else None

    async def get_post_glossary_by_draft(self, draft_id: str) -> PostGlossary | None:
        doc = await self._posts.find_one({"draft_id": draft_id})
        return PostGlossary.model_validate(doc) if doc else None

    async def add_post_glossary_if_missing(self, post_glossary: PostGlossary) -> bool:
        """발행 따라 쓰기. 같은 게시글(또는 같은 글)의 문서가 있으면 건드리지 않는다."""
        try:
            await self._posts.insert_one(post_glossary.model_dump(by_alias=True))
        except DuplicateKeyError:
            return False
        return True

    async def save_post_glossary(self, post_glossary: PostGlossary) -> None:
        await self._posts.replace_one(
            {"_id": post_glossary.post_id}, post_glossary.model_dump(by_alias=True), upsert=True
        )

    async def list_post_glossaries_by_sense(self, sense_key: str) -> list[PostGlossary]:
        docs = await self._posts.find({"items.sense_key": sense_key}).sort("_id", 1).to_list()
        return [PostGlossary.model_validate(doc) for doc in docs]

    async def list_post_glossaries(self) -> list[PostGlossary]:
        docs = await self._posts.find({}).sort("_id", 1).to_list()
        return [PostGlossary.model_validate(doc) for doc in docs]
