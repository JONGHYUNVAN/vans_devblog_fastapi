"""code_graphs 컬렉션. 문서 하나 = (repo, commit_sha) 의 코드 그래프 (code_graph_design/README.md G-09)."""

from bson.codec_options import CodecOptions
from pymongo import DESCENDING
from pymongo.database import Database

from app.models.code_graph import CodeGraph

CODE_GRAPHS_COLLECTION = "code_graphs"


class CodeGraphRepository:
    """code_graphs 저장·최신 조회·보관 정리."""

    def __init__(self, database: Database) -> None:
        # 동기 클라이언트는 tz_aware 가 아니라 naive UTC 를 준다. analyzed_at 을 aware 로 받도록 이 컬렉션만 바꾼다
        self._graphs = database.get_collection(CODE_GRAPHS_COLLECTION, codec_options=CodecOptions(tz_aware=True))

    def ensure_indexes(self) -> None:
        self._graphs.create_index([("repo", 1), ("commit_sha", 1)], unique=True)
        self._graphs.create_index([("repo", 1), ("analyzed_at", DESCENDING)])

    def get_by_commit(self, repo: str, commit_sha: str) -> CodeGraph | None:
        doc = self._graphs.find_one({"repo": repo, "commit_sha": commit_sha})
        return CodeGraph.model_validate(doc) if doc else None

    def get_latest(self, repo: str) -> CodeGraph | None:
        doc = self._graphs.find_one({"repo": repo}, sort=[("analyzed_at", DESCENDING)])
        return CodeGraph.model_validate(doc) if doc else None

    def upsert(self, code_graph: CodeGraph) -> None:
        """같은 (repo, commit_sha) 문서를 바꾸거나 새로 넣는다."""
        self._graphs.replace_one(
            {"repo": code_graph.repo, "commit_sha": code_graph.commit_sha}, code_graph.model_dump(), upsert=True
        )

    def delete_stale(self, repo: str, keep: int) -> int:
        """analyzed_at 최신 keep 개만 남기고 그 저장소의 나머지를 지운다. 지운 수를 돌려준다."""
        stale_ids = [
            doc["_id"]
            for doc in self._graphs.find({"repo": repo}, {"_id": 1}).sort("analyzed_at", DESCENDING).skip(keep)
        ]
        if not stale_ids:
            return 0
        return self._graphs.delete_many({"_id": {"$in": stale_ids}}).deleted_count
