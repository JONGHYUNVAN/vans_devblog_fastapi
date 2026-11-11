"""agent_pipeline_versions 컬렉션 (설계 09 문서 2.1절). insert 만 한다. 갱신·삭제 메서드를 두지 않는다(append-only, D-71).

활성 버전은 seq 가 가장 큰 문서다(D-72). 순번은 agent_pipeline_counters 의 $inc 로 발급한다(glossary_counters 와 같은 방식).
"""

from pymongo import ReturnDocument
from pymongo.asynchronous.database import AsyncDatabase

from app.models.pipeline import PipelineVersion

PIPELINE_VERSIONS_COLLECTION = "agent_pipeline_versions"
PIPELINE_COUNTERS_COLLECTION = "agent_pipeline_counters"
# 카운터 문서 하나가 모든 버전의 순번을 낸다. 버전 문서의 max(seq)+1 로 하면 동시 생성이 같은 번호를 받을 수 있다
_VERSION_COUNTER_ID = "version"


class PipelineVersionRepository:
    """agent_pipeline_versions·agent_pipeline_counters 조회·추가. 저장은 insert 뿐이고 갱신·삭제 메서드를 두지 않는다(D-71)."""

    def __init__(self, database: AsyncDatabase) -> None:
        self._versions = database[PIPELINE_VERSIONS_COLLECTION]
        self._counters = database[PIPELINE_COUNTERS_COLLECTION]

    async def ensure_indexes(self) -> None:
        """seq unique(최신 조회의 기준), content_hash(같은 내용 찾기), created_at. lifespan 과 `agents run` 이 부른다. 있으면 그대로 둔다."""
        await self._versions.create_index("seq", unique=True)
        await self._versions.create_index("content_hash")
        await self._versions.create_index("created_at")

    async def next_seq(self) -> int:
        """버전 순번 발급. $inc 라 동시에 불러도 겹치지 않는다. 발급만 하고 쓰지 않은 번호는 빈 번호로 남지만 해가 없다."""
        doc = await self._counters.find_one_and_update(
            {"_id": _VERSION_COUNTER_ID},
            {"$inc": {"seq": 1}},
            upsert=True,
            return_document=ReturnDocument.AFTER,
        )
        return int(doc["seq"])

    async def add(self, version: PipelineVersion) -> None:
        """새 버전을 넣는다. 같은 _id·seq 가 있으면 DuplicateKeyError. 검증과 해시 비교는 서비스가 먼저 한다."""
        # by_alias: version_id 는 문서의 _id 다. 같은 _id 가 있으면 Mongo 가 DuplicateKeyError 로 막는다
        await self._versions.insert_one(version.model_dump(by_alias=True))

    async def get(self, version_id: str) -> PipelineVersion | None:
        """버전 하나. 없으면 None (NotFoundError 판단은 서비스가 한다)."""
        doc = await self._versions.find_one({"_id": version_id})
        return PipelineVersion.model_validate(doc) if doc else None

    async def latest(self) -> PipelineVersion | None:
        """순번이 가장 큰 버전. 없으면 None."""
        # 한 건만 읽는다. 문서마다 프롬프트 전문이 들어 있어 전부 읽으면 버전이 20건을 넘었을 때 소켓 타임아웃(20초)에 걸렸다(2026-11-11)
        docs = await self._versions.find({}).sort("seq", -1).limit(1).to_list()
        return PipelineVersion.model_validate(docs[0]) if docs else None

    async def list_all(self) -> list[PipelineVersion]:
        """순번 순."""
        docs = await self._versions.find({}).sort("seq", 1).to_list()
        return [PipelineVersion.model_validate(doc) for doc in docs]

    async def find_by_hash(self, content_hash: str) -> list[PipelineVersion]:
        """같은 본문의 버전 전부(순번 순). 되돌리기(옛 내보내기 다시 들여오기)로 같은 내용이 여러 번 들어갈 수 있다."""
        docs = await self._versions.find({"content_hash": content_hash}).sort("seq", 1).to_list()
        return [PipelineVersion.model_validate(doc) for doc in docs]
