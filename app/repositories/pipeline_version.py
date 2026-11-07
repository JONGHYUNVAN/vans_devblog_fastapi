"""agent_pipeline_versions 컬렉션 (설계 09 문서 2.1절). insert 만 한다. 갱신·삭제 메서드를 두지 않는다(append-only, D-71).

활성 버전은 seq 가 가장 큰 문서다(D-72). 순번은 agent_pipeline_counters 의 $inc 로 발급한다(glossary_counters 와 같은 방식).
"""

from pymongo import ReturnDocument
from pymongo.asynchronous.database import AsyncDatabase

from app.models.pipeline import PipelineVersion

PIPELINE_VERSIONS_COLLECTION = "agent_pipeline_versions"
PIPELINE_COUNTERS_COLLECTION = "agent_pipeline_counters"
_VERSION_COUNTER_ID = "version"


class PipelineVersionRepository:
    def __init__(self, database: AsyncDatabase) -> None:
        self._versions = database[PIPELINE_VERSIONS_COLLECTION]
        self._counters = database[PIPELINE_COUNTERS_COLLECTION]

    async def ensure_indexes(self) -> None:
        await self._versions.create_index("seq", unique=True)
        await self._versions.create_index("content_hash")
        await self._versions.create_index("created_at")

    async def next_seq(self) -> int:
        doc = await self._counters.find_one_and_update(
            {"_id": _VERSION_COUNTER_ID},
            {"$inc": {"seq": 1}},
            upsert=True,
            return_document=ReturnDocument.AFTER,
        )
        return int(doc["seq"])

    async def add(self, version: PipelineVersion) -> None:
        await self._versions.insert_one(version.model_dump(by_alias=True))

    async def get(self, version_id: str) -> PipelineVersion | None:
        doc = await self._versions.find_one({"_id": version_id})
        return PipelineVersion.model_validate(doc) if doc else None

    async def latest(self) -> PipelineVersion | None:
        """순번이 가장 큰 버전. 없으면 None."""
        docs = await self._versions.find({}).sort("seq", -1).to_list()
        return PipelineVersion.model_validate(docs[0]) if docs else None

    async def list_all(self) -> list[PipelineVersion]:
        """순번 순."""
        docs = await self._versions.find({}).sort("seq", 1).to_list()
        return [PipelineVersion.model_validate(doc) for doc in docs]

    async def find_by_hash(self, content_hash: str) -> list[PipelineVersion]:
        docs = await self._versions.find({"content_hash": content_hash}).sort("seq", 1).to_list()
        return [PipelineVersion.model_validate(doc) for doc in docs]
