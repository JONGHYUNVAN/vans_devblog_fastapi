"""expository_runs 컬렉션. 설명문 파이프라인 실행 하나의 단계 결과와 완성된 글 (09 문서 D-100).

호출 원문·토큰·오류는 여기 두지 않는다. 영수증(agent_calls, round_id 가 실행 id)에 있다.
"""

from pymongo.asynchronous.database import AsyncDatabase

from app.models.expository import ExpositoryRun

EXPOSITORY_RUNS_COLLECTION = "expository_runs"


class ExpositoryRunRepository:
    """expository_runs 조회·덮어쓰기. 실행 하나를 쓰는 쪽은 그 실행의 러너뿐이라 낙관적 잠금을 두지 않는다."""

    def __init__(self, database: AsyncDatabase) -> None:
        self._runs = database[EXPOSITORY_RUNS_COLLECTION]

    async def get(self, run_id: str) -> ExpositoryRun | None:
        """실행 하나. 없으면 None (새 실행인지 없는 id 인지는 서비스가 판단한다)."""
        doc = await self._runs.find_one({"_id": run_id})
        return ExpositoryRun.model_validate(doc) if doc else None

    async def upsert(self, run: ExpositoryRun) -> None:
        """실행 문서를 통째로 덮어쓴다. 없으면 만든다."""
        await self._runs.replace_one({"_id": run.run_id}, run.model_dump(by_alias=True), upsert=True)
