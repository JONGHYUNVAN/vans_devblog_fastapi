"""서비스 그래프·e2e 흐름 조회 (code_graph_design/README.md "서비스 그래프", G-08·G-13).

호출 지점은 분석 때 저장된 것(CodeGraph.outbound_calls)을 읽고, OpenAPI 매칭만 조회 때 한다. OpenAPI 문서는 실패까지
OPENAPI_TTL 동안 캐시하고 서비스 그래프 결과는 CODE_GRAPH_TTL 동안 캐시한다(분석 저장이 지운다).
"""

import logging
from collections.abc import Callable
from typing import Any

from app.clients.openapi import OpenApiClient
from app.core.cache import CODE_GRAPH_TTL, OPENAPI_TTL, SERVICE_GRAPH_CACHE_KEY, cache
from app.core.exceptions import NotFoundError, ServiceUnavailableError
from app.models.code_graph import CodeGraphRepo, OpenApiSource
from app.models.service_graph import FLOWS, OpenApiStatus, ServiceGraph, ServiceSource
from app.repositories.code_graph import CodeGraphRepository
from app.schemas.code_graph import FlowPublic, ServiceGraphPublic

logger = logging.getLogger(__name__)


class ServiceGraphService:
    """self_openapi 는 이 앱의 OpenAPI 문서(HTTP 없이 app.openapi), setting_value 는 Settings 필드 이름 → 값이다."""

    def __init__(
        self,
        repository: CodeGraphRepository,
        openapi_client: OpenApiClient,
        repos: dict[str, Any],
        self_openapi: Callable[[], dict[str, Any]],
        setting_value: Callable[[str], str],
    ) -> None:
        self._repository = repository
        self._openapi_client = openapi_client
        self._repos = repos
        self._self_openapi = self_openapi
        self._setting_value = setting_value

    def get_service_graph(self) -> ServiceGraphPublic:
        return ServiceGraphPublic.model_validate(self._build().model_dump())

    def get_flow(self, flow: str) -> FlowPublic:
        """흐름의 hop 마다 스키마 이름·호출 지점과 mermaid sequenceDiagram. 없는 흐름은 NotFoundError."""
        if flow not in FLOWS:
            raise NotFoundError(f"flow {flow} is not defined (one of {', '.join(FLOWS)})")
        return FlowPublic.model_validate(self._build().trace(flow, FLOWS[flow]).model_dump())

    def _build(self) -> ServiceGraph:
        cached: ServiceGraph | None = cache.get(SERVICE_GRAPH_CACHE_KEY)
        if cached is not None:
            return cached
        sources = []
        for repo, raw in self._repos.items():
            spec = CodeGraphRepo.model_validate(raw)
            openapi, status = self._openapi(repo, spec)
            sources.append(
                ServiceSource(
                    repo=repo,
                    spec=spec,
                    code_graph=self._repository.get_latest(repo),
                    openapi=openapi,
                    openapi_status=status,
                )
            )
        service_graph = ServiceGraph.build(sources)
        cache.set(SERVICE_GRAPH_CACHE_KEY, service_graph, CODE_GRAPH_TTL)
        return service_graph

    def _openapi(self, repo: str, spec: CodeGraphRepo) -> tuple[dict[str, Any] | None, OpenApiStatus]:
        if spec.openapi is None:
            return None, "none"
        if spec.openapi == "self":
            return self._self_openapi(), "ok"
        cache_key = f"code_graph:openapi:{repo}"
        cached: dict[str, Any] | None = cache.get(cache_key)
        if cached is None:
            cached = {"document": self._fetch_openapi(repo, spec.openapi)}
            cache.set(cache_key, cached, OPENAPI_TTL)
        document = cached["document"]
        return document, "ok" if document is not None else "unavailable"

    def _fetch_openapi(self, repo: str, source: OpenApiSource) -> dict[str, Any] | None:
        # 실패는 스키마 정보만 비운다(G-08). 호출 토폴로지는 저장된 호출 지점으로 그대로 나온다
        base_url = self._setting_value(source.base_setting).rstrip("/")
        if not base_url:
            logger.info("OpenAPI 건너뜀 (%s): %s 가 비어 있다", repo, source.base_setting.upper())
            return None
        login_url, password = None, ""
        if source.login is not None:
            password = self._setting_value(source.login.password_setting)
            if not password:
                logger.info("OpenAPI 건너뜀 (%s): %s 가 비어 있다", repo, source.login.password_setting.upper())
                return None
            login_url = base_url + source.login.path
        try:
            return self._openapi_client.fetch(base_url + source.path, login_url=login_url, password=password)
        except ServiceUnavailableError as exc:
            logger.warning("OpenAPI 조회 실패 (%s): %s", repo, exc.message)
            return None
