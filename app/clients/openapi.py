"""다른 백엔드(NestJS·Spring)의 OpenAPI 문서 받기 (code_graph_design/README.md G-08·G-13).

NestJS 는 swagger-json 도 SwaggerAuthMiddleware 의 비밀번호 세션 뒤에 있어, login_url 이 있으면 먼저 로그인하고
같은 클라이언트(쿠키)로 문서를 받는다.
"""

from typing import Any

import httpx2

from app.core.exceptions import ServiceUnavailableError


class OpenApiClient:
    def __init__(self, timeout_s: float, transport: httpx2.BaseTransport | None = None) -> None:
        self._timeout_s = timeout_s
        self._transport = transport

    def fetch(self, url: str, *, login_url: str | None = None, password: str = "") -> dict[str, Any]:
        """OpenAPI 문서. 네트워크 오류·2xx 가 아닌 응답·`paths` 가 없는 본문은 ServiceUnavailableError."""
        try:
            with httpx2.Client(transport=self._transport, timeout=self._timeout_s) as client:
                if login_url is not None:
                    client.post(login_url, json={"password": password}).raise_for_status()
                response = client.get(url)
                response.raise_for_status()
                document = response.json()
        except (httpx2.HTTPError, ValueError) as exc:
            raise ServiceUnavailableError(f"openapi fetch failed: {url}: {type(exc).__name__}") from exc
        if not isinstance(document, dict) or not isinstance(document.get("paths"), dict):
            raise ServiceUnavailableError(f"openapi document has no paths: {url}")
        return document
