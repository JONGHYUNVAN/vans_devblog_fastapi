"""vans_story_be_post 호출: 카테고리 조회, 봇 계정 로그인(vans_story_be), 글 생성 (설계 06 문서 3절 clients/publisher, 07 문서 D-40).

실패 분류(04 문서 8절 10·11단계): 로그인 단계의 모든 실패와 글 생성의 4xx·연결 거부는 PublishFailedError(결과 확정),
글 생성의 응답 전 타임아웃·연결 끊김·5xx 는 PublishUncertainError(글이 생겼을 수 있다). 토큰은 저장하지 않고 발행마다 받는다.
"""

import httpx2

from app.clients.publisher.base import PostCategory, PostCreatePayload, PublishResult
from app.core.exceptions import PublishFailedError, PublishUncertainError

_BEARER = "Bearer "
# 요청이 서버에 닿기 전의 실패. 글이 생기지 않았다
_NOT_DELIVERED = (httpx2.ConnectError, httpx2.ConnectTimeout, httpx2.UnsupportedProtocol)


class PostBEPublisher:
    def __init__(
        self,
        post_be_url: str,
        timeout_s: float,
        *,
        user_service_url: str = "",
        email: str = "",
        password: str = "",
        transport: httpx2.AsyncBaseTransport | None = None,
    ) -> None:
        self._post_be_url = post_be_url.rstrip("/")
        self._user_service_url = user_service_url.rstrip("/")
        self._email = email
        self._password = password
        self._timeout_s = timeout_s
        self._transport = transport

    async def list_categories(self) -> list[PostCategory]:
        """GET /api/v1/categories?activeOnly=true. 어떤 실패든 PublishFailedError 다(조회에는 결과 불명이 없다)."""
        try:
            async with httpx2.AsyncClient(transport=self._transport, timeout=self._timeout_s) as client:
                response = await client.get(f"{self._post_be_url}/api/v1/categories", params={"activeOnly": "true"})
                response.raise_for_status()
                rows = response.json()
        except (httpx2.HTTPError, ValueError) as exc:
            raise PublishFailedError(f"category lookup failed: {type(exc).__name__}: {exc}") from exc
        if not isinstance(rows, list):
            raise PublishFailedError("category lookup returned a non-list body")
        return [
            PostCategory(
                id=row["id"],
                parent_id=row.get("parentId"),
                value=row["value"],
                label=row["label"],
                is_active=row.get("isActive", True),
            )
            for row in rows
        ]

    async def publish(self, payload: PostCreatePayload) -> PublishResult:
        """로그인 → POST /api/v1/posts. 계정 설정이 비어 있으면 ValueError(설정 오류, 러너가 INTERNAL_ERROR 로 멈춘다)."""
        if not (self._user_service_url and self._email and self._password):
            raise ValueError(
                "publisher account is not configured (USER_SERVICE_URL, PUBLISHER_EMAIL, PUBLISHER_PASSWORD)"
            )
        async with httpx2.AsyncClient(transport=self._transport, timeout=self._timeout_s) as client:
            token = await self._login(client)
            return await self._create_post(client, token, payload)

    async def _login(self, client: httpx2.AsyncClient) -> str:
        """POST /api/v1/auth/login. 액세스 토큰은 응답의 Authorization 헤더(Bearer)로 온다(vans_story_be AuthService.login)."""
        try:
            response = await client.post(
                f"{self._user_service_url}/api/v1/auth/login",
                json={"email": self._email, "password": self._password},
            )
        except httpx2.HTTPError as exc:
            raise PublishFailedError(f"login failed: {type(exc).__name__}: {exc}") from exc
        if response.status_code >= 400:
            raise PublishFailedError(f"login failed: HTTP {response.status_code}")
        header = response.headers.get("Authorization", "")
        if not header.startswith(_BEARER) or not header[len(_BEARER) :].strip():
            raise PublishFailedError("login response has no Bearer token in the Authorization header")
        return header[len(_BEARER) :].strip()

    async def _create_post(self, client: httpx2.AsyncClient, token: str, payload: PostCreatePayload) -> PublishResult:
        try:
            response = await client.post(
                f"{self._post_be_url}/api/v1/posts",
                json=payload.to_dto(),
                headers={"Authorization": f"{_BEARER}{token}"},
            )
        except _NOT_DELIVERED as exc:
            raise PublishFailedError(f"post creation not delivered: {type(exc).__name__}: {exc}") from exc
        except httpx2.HTTPError as exc:
            raise PublishUncertainError(f"post creation result unknown: {type(exc).__name__}: {exc}") from exc
        if response.status_code >= 500:
            raise PublishUncertainError(f"post creation result unknown: HTTP {response.status_code}")
        if response.status_code >= 400:
            raise PublishFailedError(f"post creation rejected (HTTP {response.status_code}): {response.text[:500]}")
        try:
            post_id = response.json()["_id"]
        except (ValueError, KeyError, TypeError) as exc:
            raise PublishUncertainError("post creation responded without _id") from exc
        return PublishResult(external_post_id=str(post_id))
