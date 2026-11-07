"""post_provenance 컬렉션. 글마다 하나인 제작 기록 (설계 08 문서 3절)."""

from pymongo.asynchronous.database import AsyncDatabase
from pymongo.errors import DuplicateKeyError

from app.models.provenance import PostProvenance

POST_PROVENANCE_COLLECTION = "post_provenance"


class PostProvenanceRepository:
    def __init__(self, database: AsyncDatabase) -> None:
        self._records = database[POST_PROVENANCE_COLLECTION]

    async def ensure_indexes(self) -> None:
        await self._records.create_index("draft_id", unique=True)
        await self._records.create_index("round_id")

    async def get(self, post_id: str) -> PostProvenance | None:
        doc = await self._records.find_one({"_id": post_id})
        return PostProvenance.model_validate(doc) if doc else None

    async def get_by_draft(self, draft_id: str) -> PostProvenance | None:
        doc = await self._records.find_one({"draft_id": draft_id})
        return PostProvenance.model_validate(doc) if doc else None

    async def add_if_missing(self, provenance: PostProvenance) -> bool:
        """발행 따라 쓰기. 같은 게시글(또는 같은 글)의 기록이 있으면 건드리지 않는다. 넣었으면 True."""
        try:
            await self._records.insert_one(provenance.model_dump(by_alias=True))
        except DuplicateKeyError:
            return False
        return True

    async def save(self, provenance: PostProvenance) -> None:
        """다시 만든 기록으로 덮어쓴다 (--rebuild)."""
        await self._records.replace_one({"_id": provenance.post_id}, provenance.model_dump(by_alias=True), upsert=True)
