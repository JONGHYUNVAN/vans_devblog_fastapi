"""ES 인덱스 준비. 앱 기동(lifespan)과 CLI 가 쓴다."""

from app.repositories.popular_search import PopularSearchRepository


class IndexSetupService:
    """인기 검색어 인덱스를 만든다. posts 인덱스는 운영 중인 posts_v2 를 그대로 쓰므로 만들지 않는다."""

    def __init__(self, popular_search_repository: PopularSearchRepository) -> None:
        self._popular_search_repository = popular_search_repository

    def ensure_popular_index(self) -> bool:
        """인기 검색어 인덱스가 없으면 만든다. 만들었으면 True. ES 오류는 그대로 던진다."""
        return self._popular_search_repository.create_index_if_missing()

    def recreate_popular_index(self) -> None:
        """인기 검색어 인덱스를 지우고 새로 만든다. 쌓인 검색 횟수가 모두 사라진다. ES 오류는 그대로 던진다."""
        self._popular_search_repository.delete_index()
        self._popular_search_repository.create_index_if_missing()
