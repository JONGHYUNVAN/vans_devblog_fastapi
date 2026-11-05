"""CodeGraphRepoConfigRepository 테스트: 키 순서 읽기, 통째로 교체, 빌더가 Mongo 값을 서비스에 넘기는지. 동기 Mongo 는 손으로 짠 가짜다."""

from typing import Any

import pytest

from app.core.config import Settings
from app.core.exceptions import NotFoundError
from app.dependencies import build_code_graph_service
from app.repositories.code_graph_repo_config import CODE_GRAPH_REPOS_COLLECTION, CodeGraphRepoConfigRepository


class FakeCursor:
    def __init__(self, docs: list[dict[str, Any]]) -> None:
        self.docs = docs

    def sort(self, key: str, direction: int) -> "FakeCursor":
        return FakeCursor(sorted(self.docs, key=lambda doc: doc[key], reverse=direction < 0))

    def __iter__(self):
        return iter(self.docs)


class FakeCollection:
    def __init__(self) -> None:
        self.docs: dict[str, dict[str, Any]] = {}

    def find(self, query: dict[str, Any]) -> FakeCursor:
        return FakeCursor([{"_id": key, **doc} for key, doc in self.docs.items()])

    def replace_one(self, query: dict[str, Any], doc: dict[str, Any], upsert: bool) -> None:
        assert upsert
        self.docs[query["_id"]] = doc

    def delete_many(self, query: dict[str, Any]) -> None:
        keep = query["_id"]["$nin"]
        self.docs = {key: doc for key, doc in self.docs.items() if key in keep}


class FakeDatabase(dict):
    def __missing__(self, name: str) -> FakeCollection:
        self[name] = FakeCollection()
        return self[name]

    def get_collection(self, name: str, **_: Any) -> FakeCollection:
        return self[name]


def test_list_all_returns_configs_by_key_without_mongo_id():
    database = FakeDatabase()
    repository = CodeGraphRepoConfigRepository(database)

    repository.replace_all({"b": {"owner": "x"}, "a": {"owner": "y"}})

    assert list(repository.list_all().items()) == [("a", {"owner": "y"}), ("b", {"owner": "x"})]
    assert set(database[CODE_GRAPH_REPOS_COLLECTION].docs) == {"a", "b"}


def test_replace_all_overwrites_and_removes_repos_not_in_the_new_set():
    repository = CodeGraphRepoConfigRepository(FakeDatabase())
    repository.replace_all({"keep": {"owner": "old"}, "drop": {"owner": "x"}})

    repository.replace_all({"keep": {"owner": "new"}})

    assert repository.list_all() == {"keep": {"owner": "new"}}


def test_build_code_graph_service_reads_repos_from_mongo_on_every_build():
    database = FakeDatabase()
    mongo_client = {"blog": database}
    settings = Settings(_env_file=None, mongodb_database="blog")
    build = lambda: build_code_graph_service(mongo_client, settings, run_in_background=lambda f, *a: f(*a))  # noqa: E731

    with pytest.raises(NotFoundError):
        build().get_analysis_status("demo")

    CodeGraphRepoConfigRepository(database).replace_all({"demo": {"owner": "x"}})

    assert build().get_analysis_status("demo").state == "idle"
