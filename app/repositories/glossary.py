"""용어 사전 세 컬렉션(glossary_headwords·glossary_senses·post_glossary)과 뜻 키 순번의 Mongo 접근 (설계 08 문서 2.1절, D-63).

공용 Mongo(D-43)를 배포된 러너와 나눠 쓴다. 쓰는 쪽은 추출·승인을 맡는 GlossaryService 와 발행 따라 쓰기 DerivedRecordsWriter
뿐이고 GlossaryViewService 는 읽기만 한다. 상태 판단(승인 전 표시 금지)은 서비스가 하고 여기서는 걸러 주지 않는다.
unique 인덱스 둘이 불변식을 강제한다: 한 조회 키는 표제어 하나에만(lookup_keys), 글 하나에 post_glossary 하나(draft_id).
뜻 키는 ObjectId 가 아니라 순번(s00042)이다: 발행 글의 TipTap JSON 과 툴팁 조회 URL 에 박히므로 짧고 영원히 바뀌지 않아야 한다.
"""

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
        """lifespan 이 기동 때 부른다. 되풀이해도 안전하다.

        lookup_keys 의 unique 다중키 인덱스가 "별칭은 표제어 하나에만" 을 강제한다. 나머지는 조회 경로용이다.
        """
        # 다중키라 배열 원소마다 유일하다. 다른 표제어가 가진 표기를 별칭으로 넣으면 저장이 DuplicateKeyError 로 실패한다
        await self._headwords.create_index("lookup_keys", unique=True, name="lookup_keys_unique")
        await self._headwords.create_index("status")  # pending·전역 사전이 상태로 거른다
        await self._senses.create_index("headword_key")  # 표제어의 뜻 목록 (뜻 선택, 툴팁의 다른 뜻)
        await self._senses.create_index([("status", 1), ("headword_key", 1)])  # 승인된 뜻만 표제어별로 (읽기 API)
        await self._senses.create_index("post_ids")  # 글 id → 뜻 역조회용
        # 글 하나에 문서 하나. 발행·CONFIRM_PUBLISHED·정리가 같은 글을 되풀이해 써도 하나만 남는다
        await self._posts.create_index("draft_id", unique=True)
        await self._posts.create_index("items.sense_key")  # merge-sense 가 뜻을 가리키는 글을 찾는다

    # ── 표제어 ──────────────────────────────────────────────────────

    async def get_headword(self, headword_key: str) -> GlossaryHeadword | None:
        """키로 하나. 없으면 None. MERGED 를 따라가지 않는다(서비스의 _follow_merge 가 한다)."""
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
        """여러 키를 한 번에. 없는 키는 조용히 빠진다. 키 순."""
        keys = list(dict.fromkeys(headword_keys))
        if not keys:
            return []
        docs = await self._headwords.find({"_id": {"$in": keys}}).sort("_id", 1).to_list()
        return [GlossaryHeadword.model_validate(doc) for doc in docs]

    async def add_headword_if_missing(self, headword: GlossaryHeadword) -> bool:
        """같은 키나 같은 조회 키가 없을 때만 넣는다. 넣었으면 True.

        $setOnInsert 대신 insert_one 인 것은 _id 충돌뿐 아니라 lookup_keys unique 충돌도 같은 DuplicateKeyError 로 받기 위해서다.
        동시 추출한 다른 글이 먼저 만든 경우라 호출자(등록 확인 ④)는 False 를 받으면 정확 일치로 다시 찾는다.
        """
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
        """순번 발급. 단어도 영역도 담지 않는 의미의 식별자다 (08 문서 D-63).

        카운터 문서 하나를 $inc 하는 것이라 동시 추출에도 겹치지 않는다. 발급만 하고 되돌리지 않으므로 번호에 빈칸이 생길 수 있고,
        그것은 문제가 아니다(키는 순서가 아니라 식별자다).
        """
        doc = await self._counters.find_one_and_update(
            {"_id": _SENSE_COUNTER_ID},
            {"$inc": {"seq": 1}},
            upsert=True,
            return_document=ReturnDocument.AFTER,
        )
        return f"s{doc['seq']:05d}"  # 5자리 0 채움은 정렬·가독성용이고 99999 를 넘어도 자릿수만 늘어난다

    async def get_sense(self, sense_key: str) -> GlossarySense | None:
        """키로 하나. 없으면 None."""
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
        """여러 뜻 키를 한 번에. 없는 키는 빠진다(글 항목이 지워진 뜻을 가리키면 읽기 API 가 그 항목을 건너뛴다). 생성 순."""
        keys = list(dict.fromkeys(sense_keys))
        if not keys:
            return []
        docs = await self._senses.find({"_id": {"$in": keys}}).sort("created_at", 1).to_list()
        return [GlossarySense.model_validate(doc) for doc in docs]

    async def add_sense(self, sense: GlossarySense) -> None:
        """새 뜻을 넣는다. 키는 next_sense_key 로 먼저 받는다. 같은 키가 있으면 DuplicateKeyError 가 그대로 난다(있을 수 없는 일)."""
        await self._senses.insert_one(sense.model_dump(by_alias=True))

    async def save_sense(self, sense: GlossarySense) -> None:
        """덮어쓴다 (승인·거절·병합·표제어 이동)."""
        await self._senses.replace_one({"_id": sense.sense_key}, sense.model_dump(by_alias=True), upsert=True)

    async def add_post_to_senses(self, sense_keys: Iterable[str], post_id: str) -> None:
        """발행 글을 뜻의 글 목록에 더한다. 되풀이해도 한 번만 들어간다."""
        keys = list(dict.fromkeys(sense_keys))
        if keys:
            await self._senses.update_many({"_id": {"$in": keys}}, {"$addToSet": {"post_ids": post_id}})

    # ── 글→뜻 매핑 ───────────────────────────────────────────────────

    async def get_post_glossary(self, post_id: str) -> PostGlossary | None:
        """게시글 id 로 하나. 없으면 None (사람 글, 발행 전 글, 추출이 아무것도 못 남긴 글)."""
        doc = await self._posts.find_one({"_id": post_id})
        return PostGlossary.model_validate(doc) if doc else None

    async def get_post_glossary_by_draft(self, draft_id: str) -> PostGlossary | None:
        """글(draft) id 로 하나. CLI 가 게시글 id 대신 글 id 를 받을 때 쓴다."""
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
        """덮어쓴다. 사람의 resolve·drop, merge-sense 의 항목 이동, 보충(backfill)이 쓴다."""
        await self._posts.replace_one(
            {"_id": post_glossary.post_id}, post_glossary.model_dump(by_alias=True), upsert=True
        )

    async def list_post_glossaries_by_sense(self, sense_key: str) -> list[PostGlossary]:
        """그 뜻을 가리키는 글 전부. merge-sense 가 항목을 옮길 때 쓴다."""
        docs = await self._posts.find({"items.sense_key": sense_key}).sort("_id", 1).to_list()
        return [PostGlossary.model_validate(doc) for doc in docs]

    async def list_post_glossaries(self) -> list[PostGlossary]:
        """전부. 사전이 작아 pending·check 가 메모리에서 거른다 (08 문서 2.9절, ES 없음)."""
        docs = await self._posts.find({}).sort("_id", 1).to_list()
        return [PostGlossary.model_validate(doc) for doc in docs]
