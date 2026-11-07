"""agent_drafts 컬렉션. 글 한 편의 버전·리뷰·중재를 한 문서에 내장한다 (설계 02 문서 6절)."""

from datetime import datetime

from pymongo.asynchronous.database import AsyncDatabase
from pymongo.errors import DuplicateKeyError

from app.models.enums import PostStatus
from app.models.post_draft import PostDraft
from app.repositories.optimistic_lock import replace_with_revision

AGENT_DRAFTS_COLLECTION = "agent_drafts"


class PostDraftRepository:
    """agent_drafts 조회·생성·저장. 생성은 결정론적 ID 로 멱등하고, 저장은 revision 낙관적 잠금이다."""

    def __init__(self, database: AsyncDatabase) -> None:
        self._drafts = database[AGENT_DRAFTS_COLLECTION]

    async def ensure_indexes(self) -> None:
        """round_id(라운드의 글 3편), status(러너 스캔), (status, updated_at)(오래 안 건드린 순 목록). lifespan 과 `agents run` 이 부른다."""
        # _id 가 결정론적 "{round_id}:{slot}" 이라 중복 방지용 unique 인덱스는 따로 없다(add_if_missing 이 _id 충돌로 판단한다).
        # 아래는 목록 조회 셋(라운드의 글, 상태별, 상태+오래된 순)이 쓰는 인덱스뿐이다
        await self._drafts.create_index("round_id")
        await self._drafts.create_index("status")
        await self._drafts.create_index([("status", 1), ("updated_at", 1)])

    async def get(self, draft_id: str) -> PostDraft | None:
        """글 하나. 없으면 None."""
        doc = await self._drafts.find_one({"_id": draft_id})
        return PostDraft.model_validate(doc) if doc else None

    async def list_by_round(self, round_id: str) -> list[PostDraft]:
        """슬롯 순. _id 가 "{round_id}:{slot}" 이라 _id 정렬이 슬롯 순이다."""
        docs = await self._drafts.find({"round_id": round_id}).sort("_id", 1).to_list()
        return [PostDraft.model_validate(doc) for doc in docs]

    async def list_by_status(self, *statuses: PostStatus) -> list[PostDraft]:
        """오래 안 건드린 순."""
        query = {"status": {"$in": [status.value for status in statuses]}}
        docs = await self._drafts.find(query).sort("updated_at", 1).to_list()
        return [PostDraft.model_validate(doc) for doc in docs]

    async def list_published_without_derived(self) -> list[PostDraft]:
        """PUBLISHED 인데 글 단위 파생 문서(post_glossary·post_provenance)를 아직 다 쓰지 않은 글. 정리가 마저 쓴다 (설계 08 문서 2.2절)."""
        query = {"status": PostStatus.PUBLISHED.value, "publication.derived_at": None}
        docs = await self._drafts.find(query).sort("updated_at", 1).to_list()
        return [PostDraft.model_validate(doc) for doc in docs]

    async def set_derived_at(self, draft_id: str, derived_at: datetime) -> None:
        """파생 문서를 다 썼다는 표시. revision 을 올리지 않는 단일 필드 갱신이다(PUBLISHED 글은 코루틴이 돌지 않는다)."""
        await self._drafts.update_one({"_id": draft_id}, {"$set": {"publication.derived_at": derived_at}})

    async def add_if_missing(self, draft: PostDraft) -> bool:
        """같은 _id 가 없을 때만 넣는다. 넣었으면 True. 있으면 건드리지 않고 False (중단으로 정리된 글을 되살리지 않는다)."""
        try:
            await self._drafts.insert_one(draft.model_dump(by_alias=True))
        except DuplicateKeyError:
            return False
        return True

    async def save(self, draft: PostDraft) -> None:
        """revision 이 읽었을 때와 같을 때만 저장한다. 아니면 ConcurrentUpdateError."""
        await replace_with_revision(self._drafts, draft.draft_id, draft)
