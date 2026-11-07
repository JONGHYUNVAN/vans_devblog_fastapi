"""post_provenance 컬렉션의 Mongo 접근. 글마다 하나인 제작 기록 (설계 08 문서 3.4절, D-67).

쓰는 쪽은 발행 따라 쓰기 DerivedRecordsWriter(add_if_missing)와 CLI `agents provenance build --rebuild`(save) 뿐이고,
공개 API(services/provenance.py)는 get 만 한다. _id 는 Post BE 게시글 id 고 draft_id 는 unique 라 발행·CONFIRM_PUBLISHED·정리가
같은 글을 되풀이해 써도 하나만 남는다. 영수증이 정리돼도 이 문서는 남아야 하므로 agent_calls 와 함께 지우는 경로를 두지 않는다.
"""

from pymongo.asynchronous.database import AsyncDatabase
from pymongo.errors import DuplicateKeyError

from app.models.provenance import PostProvenance

POST_PROVENANCE_COLLECTION = "post_provenance"


class PostProvenanceRepository:
    """post_provenance 의 조회·멱등 삽입·덮어쓰기. 문서 생성 자체는 services/provenance.py 의 순수 함수가 한다."""

    def __init__(self, database: AsyncDatabase) -> None:
        self._records = database[POST_PROVENANCE_COLLECTION]

    async def ensure_indexes(self) -> None:
        """lifespan 이 기동 때 부른다. 되풀이해도 안전하다."""
        # 글 하나에 기록 하나. 게시글 id(_id)가 다른 두 발행 시도가 같은 글을 가리키는 일을 막는다
        await self._records.create_index("draft_id", unique=True)
        await self._records.create_index("round_id")  # 라운드 단위 조회·훗날 영수증 정리 정책(Q-24)의 대조용

    async def get(self, post_id: str) -> PostProvenance | None:
        """게시글 id 로 하나. 없으면 None (사람 글, 아직 따라 쓰기가 돌지 않은 글)."""
        doc = await self._records.find_one({"_id": post_id})
        return PostProvenance.model_validate(doc) if doc else None

    async def get_by_draft(self, draft_id: str) -> PostProvenance | None:
        """글(draft) id 로 하나. CLI `agents provenance show` 가 게시글 id 대신 글 id 를 받을 때 쓴다."""
        doc = await self._records.find_one({"draft_id": draft_id})
        return PostProvenance.model_validate(doc) if doc else None

    async def add_if_missing(self, provenance: PostProvenance) -> bool:
        """발행 따라 쓰기. 같은 게시글(또는 같은 글)의 기록이 있으면 건드리지 않는다. 넣었으면 True.

        insert_one 인 것은 _id 와 draft_id 두 unique 충돌을 같은 DuplicateKeyError 로 받기 위해서다. 먼저 쓴 기록이 이긴다.
        """
        try:
            await self._records.insert_one(provenance.model_dump(by_alias=True))
        except DuplicateKeyError:
            return False
        return True

    async def save(self, provenance: PostProvenance) -> None:
        """다시 만든 기록으로 덮어쓴다 (`agents provenance build --rebuild`). 스키마가 바뀌었을 때만 쓴다."""
        await self._records.replace_one({"_id": provenance.post_id}, provenance.model_dump(by_alias=True), upsert=True)
