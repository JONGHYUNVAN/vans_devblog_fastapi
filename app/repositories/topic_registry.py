"""agent_topics 컬렉션. 라운드 간 주제 중복을 막는 레지스트리 (설계 02 문서 6절)."""

from datetime import datetime

from pymongo.asynchronous.database import AsyncDatabase
from pymongo.errors import DuplicateKeyError

from app.models.enums import TopicRegistryStatus
from app.models.topic import TopicRegistryEntry

AGENT_TOPICS_COLLECTION = "agent_topics"
_ACTIVE_STATUSES = [TopicRegistryStatus.RESERVED.value, TopicRegistryStatus.PUBLISHED.value]


class TopicRegistryRepository:
    """agent_topics 추가·조회·상태 변경. 항목 _id 는 그 글의 draft_id 라 추가가 멱등하다."""

    def __init__(self, database: AsyncDatabase) -> None:
        self._topics = database[AGENT_TOPICS_COLLECTION]

    async def ensure_indexes(self) -> None:
        await self._topics.create_index([("status", 1), ("keywords", 1)])
        await self._topics.create_index("round_id")
        await self._topics.create_index("published_at")

    async def get(self, entry_id: str) -> TopicRegistryEntry | None:
        doc = await self._topics.find_one({"_id": entry_id})
        return TopicRegistryEntry.model_validate(doc) if doc else None

    async def add_if_missing(self, entry: TopicRegistryEntry) -> bool:
        """같은 _id 가 없을 때만 넣는다. 넣었으면 True. 있으면 건드리지 않고 False."""
        try:
            await self._topics.insert_one(entry.model_dump(by_alias=True))
        except DuplicateKeyError:
            return False
        return True

    async def list_by_round(self, round_id: str) -> list[TopicRegistryEntry]:
        docs = await self._topics.find({"round_id": round_id}).sort("_id", 1).to_list()
        return [TopicRegistryEntry.model_validate(doc) for doc in docs]

    async def list_active_since(self, since: datetime) -> list[TopicRegistryEntry]:
        """since 이후 등록된 RESERVED·PUBLISHED 항목, 최근 순. 주제 제안의 avoid_topics 입력이다 (설계 05 문서 2.1절)."""
        query = {"status": {"$in": _ACTIVE_STATUSES}, "created_at": {"$gte": since}}
        docs = await self._topics.find(query).sort("created_at", -1).to_list()
        return [TopicRegistryEntry.model_validate(doc) for doc in docs]

    async def set_status(
        self, entry_id: str, status: TopicRegistryStatus, published_at: datetime | None = None
    ) -> bool:
        """상태를 바꾼다. 항목이 있었으면 True. 정리(reconcile)가 되풀이 호출해도 같은 결과다."""
        result = await self._topics.update_one(
            {"_id": entry_id}, {"$set": {"status": status.value, "published_at": published_at}}
        )
        return result.matched_count > 0
