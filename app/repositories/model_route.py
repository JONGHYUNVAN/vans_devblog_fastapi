"""agent_routes 컬렉션. 호출별 모델 후보, 슬롯 페르소나, 수준 기준표 문서를 담는다. 검증은 models/routing 이 한다."""

from collections.abc import Iterable, Mapping
from typing import Any

from pymongo.asynchronous.database import AsyncDatabase

AGENT_ROUTES_COLLECTION = "agent_routes"


class ModelRouteRepository:
    def __init__(self, database: AsyncDatabase) -> None:
        self._routes = database[AGENT_ROUTES_COLLECTION]

    async def list_documents(self) -> list[dict[str, Any]]:
        return await self._routes.find({}).to_list()

    async def replace_all(self, documents: Iterable[Mapping[str, Any]]) -> None:
        """문서를 _id 별로 덮어쓴다. 진행 중인 라운드는 스냅샷을 쓰므로 영향이 없다."""
        for document in documents:
            await self._routes.replace_one({"_id": document["_id"]}, dict(document), upsert=True)
