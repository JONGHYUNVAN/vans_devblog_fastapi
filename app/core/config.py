"""환경변수 설정. 이름은 Django 서비스와 같게 유지해 CloudType 환경변수를 그대로 옮길 수 있게 한다."""

from functools import lru_cache
from urllib.parse import urlparse

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

    # KIS OpenAPI
    kis_app_key: str = ""
    kis_app_secret: str = ""
    kis_is_real: bool = False

    # CORS
    cors_allowed_origins: str = ""
    frontend_hosts: str = ""

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
