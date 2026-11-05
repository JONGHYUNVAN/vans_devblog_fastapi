"""code_graph_repos 컬렉션. 문서 하나 = 분석 대상 저장소 하나의 설정이고 _id 가 저장소 키다 (code_graph_design/README.md G-35).

검증은 services/code_graph 가 한다.
"""

from collections.abc import Mapping
from typing import Any

from pymongo import ASCENDING
from pymongo.database import Database

CODE_GRAPH_REPOS_COLLECTION = "code_graph_repos"


class CodeGraphRepoConfigRepository:
    def __init__(self, database: Database) -> None:
        self._repos = database[CODE_GRAPH_REPOS_COLLECTION]

    def list_all(self) -> dict[str, dict[str, Any]]:
        """저장소 키 → 설정. 키 순서다."""
        return {doc.pop("_id"): doc for doc in self._repos.find({}).sort("_id", ASCENDING)}

    def replace_all(self, repos: Mapping[str, Mapping[str, Any]]) -> None:
        """문서를 키별로 덮어쓰고 목록에 없는 저장소는 지운다. 분석 결과(code_graphs)는 건드리지 않는다."""
        for key, config in repos.items():
            self._repos.replace_one({"_id": key}, dict(config), upsert=True)
        self._repos.delete_many({"_id": {"$nin": list(repos)}})
