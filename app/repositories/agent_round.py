"""agent_rounds 컬렉션. 주제 선정 중인 라운드는 부분 unique 인덱스로 하나만 둔다 (설계 02 문서 6절)."""

from pymongo.asynchronous.database import AsyncDatabase
from pymongo.errors import DuplicateKeyError

from app.core.exceptions import InvalidTransitionError
from app.models.enums import RoundStatus
from app.models.publishing_round import PublishingRound
from app.repositories.optimistic_lock import replace_with_revision

AGENT_ROUNDS_COLLECTION = "agent_rounds"
ONE_TOPIC_SELECTION_INDEX = "one_topic_selection_round"


class PublishingRoundRepository:
    """agent_rounds 조회·생성·저장. 저장은 revision 낙관적 잠금이다."""

    def __init__(self, database: AsyncDatabase) -> None:
        self._rounds = database[AGENT_ROUNDS_COLLECTION]

    async def ensure_indexes(self) -> None:
        await self._rounds.create_index([("status", 1), ("created_at", 1)])
        await self._rounds.create_index(
            "status",
            unique=True,
            partialFilterExpression={"status": RoundStatus.TOPIC_SELECTION.value},
            name=ONE_TOPIC_SELECTION_INDEX,
        )

    async def get(self, round_id: str) -> PublishingRound | None:
        doc = await self._rounds.find_one({"_id": round_id})
        return PublishingRound.model_validate(doc) if doc else None

    async def list_by_status(self, status: RoundStatus) -> list[PublishingRound]:
        """만든 순."""
        docs = await self._rounds.find({"status": status.value}).sort("created_at", 1).to_list()
        return [PublishingRound.model_validate(doc) for doc in docs]

    async def add(self, publishing_round: PublishingRound) -> None:
        """새 라운드를 넣는다. 주제 선정 중인 라운드가 이미 있으면 InvalidTransitionError(409) 이고 문서는 남지 않는다."""
        try:
            await self._rounds.insert_one(publishing_round.model_dump(by_alias=True))
        except DuplicateKeyError as exc:
            raise InvalidTransitionError(
                f"round {publishing_round.round_id} not created: another round is in TOPIC_SELECTION or the id exists"
            ) from exc

    async def save(self, publishing_round: PublishingRound) -> None:
        """revision 이 읽었을 때와 같을 때만 저장한다. 아니면 ConcurrentUpdateError."""
        await replace_with_revision(self._rounds, publishing_round.round_id, publishing_round)
