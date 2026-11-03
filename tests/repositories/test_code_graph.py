"""CodeGraphRepository 테스트: 인덱스, (repo, commit_sha) upsert, 최신 조회, 저장소당 보관 정리. 동기 Mongo 는 손으로 짠 가짜다."""

from datetime import UTC, datetime, timedelta
from typing import Any

from app.models.code_graph import CodeGraph, CodeGraphDiagnostics
from app.repositories.code_graph import CODE_GRAPHS_COLLECTION, CodeGraphRepository


class FakeCursor:
    def __init__(self, docs: list[dict[str, Any]]) -> None:
        self.docs = docs

    def sort(self, key: str, direction: int) -> "FakeCursor":
        return FakeCursor(sorted(self.docs, key=lambda doc: doc[key], reverse=direction < 0))

    def skip(self, count: int) -> "FakeCursor":
        return FakeCursor(self.docs[count:])

    def __iter__(self):
        return iter(self.docs)


class FakeDeleteResult:
    def __init__(self, deleted_count: int) -> None:
        self.deleted_count = deleted_count


class FakeCollection:
    def __init__(self) -> None:
        self.docs: list[dict[str, Any]] = []
        self.indexes: list[tuple[Any, dict[str, Any]]] = []

    def create_index(self, keys: Any, **options: Any) -> None:
        self.indexes.append((keys, options))

    def _matching(self, query: dict[str, Any]) -> list[dict[str, Any]]:
        return [doc for doc in self.docs if all(doc.get(key) == value for key, value in query.items())]

    def find_one(self, query: dict[str, Any], sort: list[tuple[str, int]] | None = None) -> dict[str, Any] | None:
        docs = self._matching(query)
        if sort:
            [(key, direction)] = sort
            docs = sorted(docs, key=lambda doc: doc[key], reverse=direction < 0)
        return docs[0] if docs else None

    def find(self, query: dict[str, Any], projection: dict[str, int]) -> FakeCursor:
        return FakeCursor(self._matching(query))

    def replace_one(self, query: dict[str, Any], doc: dict[str, Any], upsert: bool) -> None:
        existing = self.find_one(query)
        self.docs = [each for each in self.docs if each is not existing]
        self.docs.append({"_id": existing["_id"] if existing else f"id-{len(self.docs)}-{doc['commit_sha']}", **doc})

    def delete_many(self, query: dict[str, Any]) -> FakeDeleteResult:
        ids = set(query["_id"]["$in"])
        before = len(self.docs)
        self.docs = [doc for doc in self.docs if doc["_id"] not in ids]
        return FakeDeleteResult(before - len(self.docs))


class FakeDatabase:
    def __init__(self) -> None:
        self.collection = FakeCollection()
        self.opened: list[tuple[str, Any]] = []

    def get_collection(self, name: str, codec_options: Any = None) -> FakeCollection:
        self.opened.append((name, codec_options))
        return self.collection


BASE = datetime(2026, 11, 3, tzinfo=UTC)


def _graph(commit_sha: str, minutes: int = 0, repo: str = "demo") -> CodeGraph:
    return CodeGraph(
        repo=repo,
        commit_sha=commit_sha,
        analyzed_at=BASE + timedelta(minutes=minutes),
        tech_stack=[],
        layers=[],
        allowed_edges=[],
        nodes=[],
        edges=[],
        diagnostics=CodeGraphDiagnostics(),
    )


def test_repository_opens_collection_with_tz_aware_codec():
    database = FakeDatabase()

    CodeGraphRepository(database)

    [(name, codec_options)] = database.opened
    assert name == CODE_GRAPHS_COLLECTION
    assert codec_options.tz_aware


def test_ensure_indexes_creates_unique_commit_and_latest_lookup_indexes():
    database = FakeDatabase()

    CodeGraphRepository(database).ensure_indexes()

    assert database.collection.indexes == [
        ([("repo", 1), ("commit_sha", 1)], {"unique": True}),
        ([("repo", 1), ("analyzed_at", -1)], {}),
    ]


def test_upsert_replaces_document_of_same_commit():
    database = FakeDatabase()
    repository = CodeGraphRepository(database)

    repository.upsert(_graph("a" * 40))
    repository.upsert(_graph("a" * 40, minutes=5))

    assert len(database.collection.docs) == 1
    assert repository.get_by_commit("demo", "a" * 40).analyzed_at == BASE + timedelta(minutes=5)
    assert repository.get_by_commit("demo", "b" * 40) is None


def test_get_latest_returns_most_recent_analysis_of_repo():
    repository = CodeGraphRepository(FakeDatabase())
    repository.upsert(_graph("a" * 40, minutes=0))
    repository.upsert(_graph("b" * 40, minutes=10))
    repository.upsert(_graph("c" * 40, minutes=20, repo="other"))

    assert repository.get_latest("demo").commit_sha == "b" * 40
    assert repository.get_latest("missing") is None


def test_delete_stale_keeps_latest_documents_of_that_repo():
    database = FakeDatabase()
    repository = CodeGraphRepository(database)
    for minutes in range(7):
        repository.upsert(_graph(f"{minutes}" * 40, minutes=minutes))
    repository.upsert(_graph("f" * 40, repo="other"))

    deleted = repository.delete_stale("demo", keep=5)

    assert deleted == 2
    remaining = sorted(doc["commit_sha"][0] for doc in database.collection.docs if doc["repo"] == "demo")
    assert remaining == ["2", "3", "4", "5", "6"]
    assert repository.get_latest("other") is not None
