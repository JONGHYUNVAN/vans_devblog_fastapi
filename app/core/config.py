"""환경변수 설정. 이름은 Django 서비스와 같게 유지해 CloudType 환경변수를 그대로 옮길 수 있게 한다."""

from functools import lru_cache
from typing import Any
from urllib.parse import urlparse

from pydantic import SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    # Elasticsearch
    elasticsearch_host: str = "localhost:9200"
    elasticsearch_username: str = "elastic"
    elasticsearch_password: str = ""
    elasticsearch_verify_certs: bool = True
    elasticsearch_timeout: int = 30

    # MongoDB (URI 방식 우선, 없으면 개별 변수)
    mongodb_uri: str = ""
    mongodb_database: str = ""
    mongodb_host: str = "localhost"
    mongodb_port: int = 27017
    mongodb_username: str = ""
    mongodb_password: str = ""
    mongodb_auth_source: str = "admin"
    mongodb_direct_connection: bool = True

    # 내부 API (NestJS -> 검색 서비스)
    internal_api_key: str = ""
    # 에이전트 내부 API (/api/v1/agents/). 검색 내부 키와 나눈다 (설계 07 문서 D-44). 비어 있으면 모두 403
    agents_internal_api_key: str = ""

    # KIS OpenAPI
    kis_app_key: str = ""
    kis_app_secret: str = ""
    kis_is_real: bool = False

    # CORS
    cors_allowed_origins: str = ""
    frontend_hosts: str = ""

    # 에이전트 파이프라인 (설계 06 문서 4절). 벤더 키·발행 계정 비밀번호는 로그에 남기지 않는다.
    anthropic_api_key: SecretStr | None = None
    google_api_key: SecretStr | None = None
    xai_api_key: SecretStr | None = None
    post_be_url: str = ""  # vans_story_be_post. 카테고리 조회·발행
    user_service_url: str = ""  # vans_story_be. 발행 계정 로그인 (설계 07 문서 D-40)
    # 벤더별 발행 계정(관리자). 글을 쓴 슬롯의 벤더 계정이 게시글 작성자가 된다 (D-45)
    anthropic_publisher_email: str = ""
    anthropic_publisher_password: SecretStr | None = None
    google_publisher_email: str = ""
    google_publisher_password: SecretStr | None = None
    xai_publisher_email: str = ""
    xai_publisher_password: SecretStr | None = None
    publish_timeout_s: int = 30
    max_iterations: int = 2
    max_topic_attempts: int = 2
    max_llm_retries: int = 3
    max_validation_retries: int = 2
    max_publish_attempts: int = 3
    draft_token_limit: int = 300_000  # 글 소프트 상한. 운영 후 조정한다 (설계 07 문서 Q-07)
    topic_keyword_overlap: float = 0.5  # 주제 중복 판별 Jaccard 임계 (설계 04 문서 2절)
    topic_title_overlap: float = 0.5
    topic_avoid_lookback_days: int = 180
    publish_requires_approval: bool = True  # 켜져 있으면 READY_TO_PUBLISH 에서 사람 승인을 기다린다
    # 평가 수준을 CreatePostDto.level 로 보낸다. Post BE 는 DTO 에 없는 필드를 400 으로 거부하므로 그쪽 배포 뒤에 켠다
    publish_post_level: bool = False
    draft_min_chars: int = 4000  # 본문 길이 범위 (설계 07 문서 Q-08)
    draft_max_chars: int = 20000
    review_timeout_s: int = 300
    draft_timeout_s: int = 600  # PROPOSE_TOPIC, WRITE_DRAFT, REVISE_DRAFT
    scan_interval_s: int = 15
    prompt_version: str = "v3"
    llm_concurrency_per_provider: int = 2

    # 코드 그래프 (code_graph_design/README.md G-04·G-11). 저장소 키 → 저장소 설정. JSON 문자열, 예시는 code_graph_design/repos.example.json
    # 원문 그대로 둔다. 검증은 CodeGraphService 가 한다(core 는 models 를 import 하지 않는다). 비어 있으면 기능이 꺼진다
    code_graph_repos: dict[str, Any] = {}
    code_graph_download_timeout_s: int = 30
    code_graph_max_tarball_mb: int = 64
    code_graph_openapi_timeout_s: int = 10  # 서비스 그래프의 NestJS·Spring OpenAPI 조회 (G-13)
    # NestJS swagger-json 은 SwaggerAuthMiddleware 비밀번호 세션 뒤에 있다(그쪽 SWAGGER_ADMIN_PASSWORD). 비면 NestJS 스키마만 빈다
    post_be_swagger_password: SecretStr | None = None

    @property
    def elasticsearch_url(self) -> str:
        return f"https://{self.elasticsearch_host}"

    @property
    def mongodb_db_name(self) -> str:
        """MONGODB_DATABASE 가 비어 있으면 URI 경로에서 추출한다 (mongodb+srv://u:p@host/dbname?x → dbname)."""
        if self.mongodb_database:
            return self.mongodb_database
        if self.mongodb_uri:
            return urlparse(self.mongodb_uri).path.lstrip("/").split("?")[0]
        return ""

    @property
    def cors_origins(self) -> list[str]:
        origins = [o.strip() for o in self.cors_allowed_origins.split(",") if o.strip()]
        origins += [f"https://{h.strip()}" for h in self.frontend_hosts.split(",") if h.strip()]
        return origins

    @property
    def kis_configured(self) -> bool:
        return bool(self.kis_app_key)


@lru_cache
def get_settings() -> Settings:
    return Settings()
