"""Synchronous HTTP/REST client for Identity Service token verification."""

from __future__ import annotations

import uuid
from typing import TYPE_CHECKING

import httpx
from pydantic import BaseModel, ConfigDict, ValidationError as PydanticValidationError

from app.core.exceptions import (
    ForbiddenError,
    UnauthorizedError,
    UpstreamServiceUnavailableError,
)
from app.core.logging import get_logger
from app.core.security import REQUEST_ID_HEADER, AuthenticatedUser, UserRole

if TYPE_CHECKING:
    from app.core.config import Settings

logger = get_logger("clients.identity")

_AUTH_ME_PATH = "/api/v1/auth/me"


class _AuthMePayload(BaseModel):
    """Schema matching Identity Service GET /api/v1/auth/me UserPublicResponse."""

    model_config = ConfigDict(extra="ignore")

    id: uuid.UUID
    role: UserRole
    is_active: bool
    is_verified: bool = False


class IdentityClient:
    """Reusable lifespan-managed HTTPX client communicating strictly with Identity Service."""

    def __init__(
        self,
        settings: Settings,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self._base_url = settings.IDENTITY_SERVICE_URL.rstrip("/")
        self._timeout_seconds = settings.SERVICE_CLIENT_TIMEOUT_SECONDS
        self._transport = transport
        self._client: httpx.AsyncClient | None = None

    @property
    def is_open(self) -> bool:
        return self._client is not None and not self._client.is_closed

    async def startup(self) -> None:
        """Initialize the pooled HTTPX AsyncClient."""
        if self._client is None or self._client.is_closed:
            timeout = httpx.Timeout(
                timeout=self._timeout_seconds,
                connect=min(2.0, self._timeout_seconds),
            )
            limits = httpx.Limits(max_connections=100, max_keepalive_connections=20)
            client_kwargs: dict[str, object] = {
                "base_url": self._base_url,
                "timeout": timeout,
                "limits": limits,
                "follow_redirects": False,
            }
            if self._transport is not None:
                client_kwargs["transport"] = self._transport
            self._client = httpx.AsyncClient(**client_kwargs)  # type: ignore[arg-type]

    async def close(self) -> None:
        """Close the HTTPX AsyncClient cleanly on shutdown."""
        if self._client is not None and not self._client.is_closed:
            await self._client.aclose()
        self._client = None

    def _require_client(self) -> httpx.AsyncClient:
        if self._client is None or self._client.is_closed:
            raise UpstreamServiceUnavailableError(
                "Identity verification service is unavailable.",
                error_code="IDENTITY_SERVICE_UNAVAILABLE",
            )
        return self._client

    async def authenticate_token(
        self,
        token: str,
        *,
        request_id: str,
    ) -> AuthenticatedUser:
        """Validate bearer access token via Identity Service GET /api/v1/auth/me."""
        client = self._require_client()
        try:
            response = await client.get(
                _AUTH_ME_PATH,
                headers={
                    "Authorization": f"Bearer {token}",
                    REQUEST_ID_HEADER: request_id,
                    "Accept": "application/json",
                },
            )
        except httpx.TimeoutException as exc:
            logger.warning(
                "Identity Service authentication request timed out",
                extra={
                    "event": "identity_auth_timeout",
                    "target_service": "identity-service",
                    "error_code": "IDENTITY_SERVICE_UNAVAILABLE",
                },
            )
            raise UpstreamServiceUnavailableError(
                "Identity verification service is temporarily unavailable.",
                error_code="IDENTITY_SERVICE_UNAVAILABLE",
            ) from exc
        except httpx.RequestError as exc:
            logger.warning(
                "Failed to connect to Identity Service for authentication",
                extra={
                    "event": "identity_auth_connect_error",
                    "target_service": "identity-service",
                    "error_code": "IDENTITY_SERVICE_UNAVAILABLE",
                },
            )
            raise UpstreamServiceUnavailableError(
                "Identity verification service is temporarily unavailable.",
                error_code="IDENTITY_SERVICE_UNAVAILABLE",
            ) from exc

        if response.status_code == 401:
            raise UnauthorizedError("Invalid or expired authentication token.")
        if response.status_code == 403:
            raise ForbiddenError("User account is inactive or forbidden.")
        if response.status_code != 200:
            logger.warning(
                "Identity Service returned unexpected status during token verification",
                extra={
                    "event": "identity_auth_unexpected_status",
                    "target_service": "identity-service",
                    "status_code": response.status_code,
                    "error_code": "IDENTITY_SERVICE_UNAVAILABLE",
                },
            )
            raise UpstreamServiceUnavailableError(
                "Identity verification service is temporarily unavailable.",
                error_code="IDENTITY_SERVICE_UNAVAILABLE",
            )

        try:
            raw_json = response.json()
            parsed = _AuthMePayload.model_validate(raw_json)
        except (ValueError, PydanticValidationError) as exc:
            logger.warning(
                "Malformed response from Identity Service during authentication",
                extra={
                    "event": "identity_auth_malformed_response",
                    "target_service": "identity-service",
                    "error_code": "IDENTITY_SERVICE_UNAVAILABLE",
                },
            )
            raise UpstreamServiceUnavailableError(
                "Identity verification returned an invalid response.",
                error_code="IDENTITY_SERVICE_UNAVAILABLE",
            ) from exc

        if not parsed.is_active:
            raise ForbiddenError("User account is inactive.")

        return AuthenticatedUser(
            id=parsed.id,
            role=parsed.role,
            is_active=parsed.is_active,
            is_verified=parsed.is_verified,
        )

