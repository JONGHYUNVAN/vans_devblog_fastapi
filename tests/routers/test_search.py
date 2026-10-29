"""검색 라우터 테스트. 응답 계약(golden)과 쿼리 파라미터 변환을 확인한다."""

from fastapi import BackgroundTasks
from fastapi.testclient import TestClient

from app.dependencies import get_search_service
from app.main import app
from app.models.popular_search import PopularSearch
from app.models.post import Post, PostHit, PostSearchPage
from app.services.search import SearchService
from tests.conftest import load_golden
from tests.fakes import FakePopularSearchRepository, FakePostIndexRepository


def _use_service(post_index: FakePostIndexRepository, popular: FakePopularSearchRepository | None = None) -> None:
    def build(background_tasks: BackgroundTasks) -> SearchService:
        return SearchService(
            post_index_repository=post_index,
            popular_search_repository=popular or FakePopularSearchRepository(),
            run_in_background=background_tasks.add_task,
        )

    app.dependency_overrides[get_search_service] = build


def test_search_posts_response_matches_golden(client):
    golden = load_golden("search_query")
    golden_hit = golden["results"][0]
    hit = PostHit(post=Post.model_validate(golden_hit), score=golden_hit["score"], highlight=golden_hit["highlight"])
    popular = FakePopularSearchRepository()
    _use_service(FakePostIndexRepository(page=PostSearchPage(total=1, hits=[hit])), popular)

    response = client.get("/api/v1/search/posts/?query=React&language=all&page=1&page_size=5&sort=relevance")

    assert response.status_code == 200
    body = response.json()
    assert set(body) == set(golden)
    assert body["results"][0] == golden_hit
    assert body["post_ids"] == golden["post_ids"]
    assert popular.upserted == ["React"]


def test_search_posts_converts_query_params(client):
    post_index = FakePostIndexRepository()
    _use_service(post_index)

    response = client.get(
        "/api/v1/search/posts/",
        params={"query": "  React ", "mainCategory": "fe", "theme": "ignored", "tags": "a, b,,c", "language": "ko"},
    )

    assert response.status_code == 200
    criteria = post_index.search_calls[0]
    assert (criteria.query, criteria.main_category, criteria.sub_category) == ("React", "fe", "")
    assert criteria.tags == ("a", "b", "c")
    assert criteria.language == "ko"


def test_search_posts_400_when_page_zero(client):
    golden = load_golden("search_invalid_page")
    _use_service(FakePostIndexRepository())

    response = client.get("/api/v1/search/posts/?page=0")

    assert response.status_code == 400
    body = response.json()
    assert body["error"] == golden["error"]
    assert set(body["details"]) == set(golden["details"])


def test_search_posts_400_when_date_from_after_date_to(client):
    _use_service(FakePostIndexRepository())

    response = client.get("/api/v1/search/posts/?date_from=2026-02-01&date_to=2026-01-01T00:00:00Z")

    assert response.status_code == 400
    assert set(response.json()["details"]) == {"non_field_errors"}


def test_autocomplete_response_matches_golden(client):
    golden = load_golden("autocomplete")
    _use_service(FakePostIndexRepository())

    response = client.get("/api/v1/search/autocomplete/?query=Re&language=all&limit=10")

    assert response.status_code == 200
    assert response.json() == golden


def test_autocomplete_400_when_query_missing(client):
    golden = load_golden("autocomplete_missing")
    _use_service(FakePostIndexRepository())

    response = client.get("/api/v1/search/autocomplete/")

    assert response.status_code == 400
    body = response.json()
    assert body["error"] == golden["error"]
    assert set(body["details"]) == set(golden["details"])


def test_popular_response_matches_golden(client):
    golden = load_golden("popular")
    popular = [PopularSearch(query=item["query"], search_count=item["count"]) for item in golden["popular_searches"]]
    _use_service(FakePostIndexRepository(), FakePopularSearchRepository(popular=popular))

    response = client.get("/api/v1/search/popular/")

    assert response.status_code == 200
    assert response.json() == golden


def test_categories_response_matches_golden(client):
    _use_service(FakePostIndexRepository())

    response = client.get("/api/v1/search/categories/")

    assert response.status_code == 200
    assert response.json() == load_golden("categories")


def test_search_posts_500_with_generic_body_when_es_fails():
    _use_service(FakePostIndexRepository(error=RuntimeError("es down at https://es.internal:9200")))
    client = TestClient(app, raise_server_exceptions=False)

    response = client.get("/api/v1/search/posts/?query=React")

    assert response.status_code == 500
    assert response.json() == {
        "error": "Internal server error",
        "message": "An unexpected error occurred. Please try again.",
    }
