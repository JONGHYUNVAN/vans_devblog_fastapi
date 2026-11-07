"""환경변수 설정. 이름은 Django 서비스와 같게 유지해 CloudType 환경변수를 그대로 옮길 수 있게 한다.

여기에는 환경마다 실제로 다른 값(비밀·주소·타임아웃·동시성)만 둔다. 파이프라인의 동작을 정하는 값(반복 상한·길이·승인 토글·
프롬프트·용어)은 Mongo 의 파이프라인 버전 문서로 옮겼다(09 문서 2.4절, 이행 1a): 라운드가 생성 때 버전을 고정하므로 환경 변수로
바꾸면 진행 중인 글과 새 글이 다른 규칙으로 돌게 되기 때문이다. 남은 publish_post_level·agent_main_categories 는 1c 에서 지운다(D-82·D-83).
"""

from functools import lru_cache
from urllib.parse import urlparse

from pydantic import SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """환경 변수 설정(.env). 비밀·주소·시간·동시성처럼 환경마다 실제로 다른 값만 둔다.

    파이프라인의 반복 상한·길이·승인 토글·프롬프트·용어 값은 Mongo 의 파이프라인 버전 문서다(09 문서 2.4절). 라운드가 버전을 고정하므로 환경 변수로 바꾸면 안 된다.
    """

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
    # 반복 상한·예산·주제 임계·길이 범위·승인 토글·프롬프트 버전·용어 값은 Mongo 의 파이프라인 버전 문서(parameters·prompts)로 옮겼다
    # (설계 09 문서 2.4절, 이행 1a). 라운드가 생성 때 버전을 고정하므로 환경 변수로는 바꾸지 않는다.
    # 평가 수준을 CreatePostDto.level 로 보낸다. Post BE 는 DTO 에 없는 필드를 400 으로 거부하므로 그쪽 배포 뒤에 켠다 (1c 에서 삭제, 09 문서 D-83)
    publish_post_level: bool = False
    # 주제 후보로 줄 메인 카테고리 값. vans_story POST_THEMES 에 없는 메인은 글이 발행돼도 블로그 주소가 404 다 (설계 07 문서 D-60. 1c 에서 삭제, 09 문서 D-82)
    agent_main_categories: str = "nextjs,nestjs,spring,jwt,database-theory,mariadb,mongodb,docker,algorithm"
    review_timeout_s: int = 300
    draft_timeout_s: int = 600  # PROPOSE_TOPIC, WRITE_DRAFT, REVISE_DRAFT
    scan_interval_s: int = 15
    llm_concurrency_per_provider: int = 2

    # 코드 그래프 (code_graph_design/README.md G-11). 분석 대상 저장소 설정은 Mongo code_graph_repos 다 (G-35)
    code_graph_download_timeout_s: int = 30
    code_graph_max_tarball_mb: int = 64
    code_graph_openapi_timeout_s: int = 10  # 서비스 그래프의 NestJS·Spring OpenAPI 조회 (G-13)
    # NestJS swagger-json 은 SwaggerAuthMiddleware 비밀번호 세션 뒤에 있다(그쪽 SWAGGER_ADMIN_PASSWORD). 비면 NestJS 스키마만 빈다
    post_be_swagger_password: SecretStr | None = None

    @property
    def elasticsearch_url(self) -> str:
        """ES 접속 URL. 호스트만 설정하고 https 를 고정한다(CloudType 변수 호환)."""
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
        """CORS 허용 출처: CORS_ALLOWED_ORIGINS 와 FRONTEND_HOSTS(https 접두)를 합친다."""
        origins = [o.strip() for o in self.cors_allowed_origins.split(",") if o.strip()]
        origins += [f"https://{h.strip()}" for h in self.frontend_hosts.split(",") if h.strip()]
        return origins

    @property
    def agent_main_category_values(self) -> frozenset[str]:
        """AGENT_MAIN_CATEGORIES 를 집합으로. 빈 항목은 뺀다. 1c 에서 없어진다(D-82)."""
        return frozenset(value.strip() for value in self.agent_main_categories.split(",") if value.strip())

    @property
    def kis_configured(self) -> bool:
        """KIS 키가 있는지. 없으면 KIS 엔드포인트가 degraded 응답을 낸다."""
        return bool(self.kis_app_key)


@lru_cache
def get_settings() -> Settings:
    """설정 싱글턴. lru_cache 라 테스트는 Settings(_env_file=None) 을 직접 만들거나 dependency_overrides 로 바꾼다."""
    return Settings()
