"""Synchronous HTTP/REST client for Identity Service token validation and freelancer verification."""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from typing import TYPE_CHECKING, Literal

import httpx
from pydantic import BaseModel, ConfigDict, Field, ValidationError as PydanticValidationError

from app.core.exceptions import (
    ForbiddenError,
    FreelancerNotFoundError,
    IdentityServiceUnavailableError,
    InvalidFreelancerError,
    UnauthorizedError,
)
from app.core.logging import get_logger
from app.core.security import REQUEST_ID_HEADER, AuthenticatedUser, UserRole

if TYPE_CHECKING:
    from app.core.config import Settings

logger = get_logger("clients.identity")

_AUTH_ME_PATH = "/api/v1/auth/me"
_FREELANCER_BY_ID_TEMPLATE = "/api/v1/freelancers/{freelancer_id}"


class _AuthMePayload(BaseModel):
    """Strict schema matching Identity Service GET /api/v1/auth/me UserPublicResponse."""

    model_config = ConfigDict(extra="ignore")

    id: uuid.UUID
    role: UserRole
    is_active: bool
    is_verified: bool = False


class _FreelancerPublicPayload(BaseModel):
    """Strict schema matching Identity Service GET /api/v1/freelancers/{id} FreelancerPublicResponse."""

    model_config = ConfigDict(extra="ignore")

    id: uuid.UUID
    user_id: uuid.UUID
    full_name: str = Field(..., min_length=1)
    skills: list[str]
    experience_years: int = Field(..., ge=0)
    availability: Literal["AVAILABLE", "BUSY", "UNAVAILABLE"]
    is_verified: bool = False
    role: str | None = None
    is_active: bool | None = None


@dataclass(frozen=True, slots=True)
class VerifiedFreelancer:
    """Verified freelancer profile metadata retrieved from Identity Service."""

    profile_id: uuid.UUID
    user_id: uuid.UUID
    full_name: str
    availability: str
    is_verified: bool


class IdentityClient:
    """Reusable lifespan-managed HTTPX client communicating strictly with Identity Service."""

    def __init__(
        self,
        settings: Settings,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self._base_url = settings.IDENTITY_SERVICE_URL.rstrip("/")
        self._timeout_seconds = settings.IDENTITY_SERVICE_TIMEOUT_SECONDS
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
            }
            if self._transport is not None:
                client_kwargs["transport"] = self._transport
            self._client = httpx.AsyncClient(**client_kwargs)  # type: ignore[arg-type]

    async def close(self) -> None:
        """Close the HTTPX AsyncClient and release connection pool sockets."""
        if self._client is not None and not self._client.is_closed:
            await self._client.aclose()
        self._client = None

    def _require_client(self) -> httpx.AsyncClient:
        if self._client is None or self._client.is_closed:
            raise IdentityServiceUnavailableError("Identity client is not initialized.")
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
            raise IdentityServiceUnavailableError() from exc
        except httpx.RequestError as exc:
            logger.warning(
                "Failed to connect to Identity Service for authentication",
                extra={
                    "event": "identity_auth_connect_error",
                    "target_service": "identity-service",
                    "error_code": "IDENTITY_SERVICE_UNAVAILABLE",
                },
            )
            raise IdentityServiceUnavailableError() from exc

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
            raise IdentityServiceUnavailableError()

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
            raise IdentityServiceUnavailableError(
                "Identity verification returned an invalid response."
            ) from exc

        if not parsed.is_active:
            raise ForbiddenError("User account is inactive.")

        return AuthenticatedUser(
            id=parsed.id,
            role=parsed.role,
            is_active=parsed.is_active,
            is_verified=parsed.is_verified,
        )

    async def verify_freelancer(
        self,
        freelancer_id: uuid.UUID,
        *,
        request_id: str,
    ) -> VerifiedFreelancer:
        """Verify target freelancer exists and is eligible via Identity Service public endpoint."""
        client = self._require_client()
        canonical_id = str(uuid.UUID(str(freelancer_id)))
        target_path = _FREELANCER_BY_ID_TEMPLATE.format(freelancer_id=canonical_id)

        try:
            response = await client.get(
                target_path,
                headers={
                    REQUEST_ID_HEADER: request_id,
                    "Accept": "application/json",
                },
            )
        except httpx.TimeoutException as exc:
            logger.warning(
                "Identity Service freelancer verification timed out",
                extra={
                    "event": "identity_freelancer_timeout",
                    "target_service": "identity-service",
                    "freelancer_id": canonical_id,
                    "error_code": "IDENTITY_SERVICE_UNAVAILABLE",
                },
            )
            raise IdentityServiceUnavailableError() from exc
        except httpx.RequestError as exc:
            logger.warning(
                "Failed to connect to Identity Service for freelancer verification",
                extra={
                    "event": "identity_freelancer_connect_error",
                    "target_service": "identity-service",
                    "freelancer_id": canonical_id,
                    "error_code": "IDENTITY_SERVICE_UNAVAILABLE",
                },
            )
            raise IdentityServiceUnavailableError() from exc

        if response.status_code == 404:
            raise FreelancerNotFoundError("Target freelancer was not found.")
        if response.status_code != 200:
            logger.warning(
                "Identity Service returned unexpected status during freelancer verification",
                extra={
                    "event": "identity_freelancer_unexpected_status",
                    "target_service": "identity-service",
                    "freelancer_id": canonical_id,
                    "status_code": response.status_code,
                    "error_code": "IDENTITY_SERVICE_UNAVAILABLE",
                },
            )
            raise IdentityServiceUnavailableError()

        try:
            raw_json = response.json()
            parsed = _FreelancerPublicPayload.model_validate(raw_json)
        except (ValueError, PydanticValidationError) as exc:
            logger.warning(
                "Malformed freelancer profile response from Identity Service",
                extra={
                    "event": "identity_freelancer_malformed_response",
                    "target_service": "identity-service",
                    "freelancer_id": canonical_id,
                    "error_code": "IDENTITY_SERVICE_UNAVAILABLE",
                },
            )
            raise IdentityServiceUnavailableError(
                "Identity Service returned a malformed freelancer profile."
            ) from exc

        if parsed.role is not None and parsed.role.upper() != UserRole.FREELANCER.value:
            raise InvalidFreelancerError("Selected user is not a FREELANCER account.")
        if parsed.is_active is False:
            raise InvalidFreelancerError("Selected freelancer account is inactive.")
        if parsed.user_id != freelancer_id:
            raise InvalidFreelancerError(
                "freelancer_id must reference a valid Identity Service user UUID."
            )

        return VerifiedFreelancer(
            profile_id=parsed.id,
            user_id=parsed.user_id,
            full_name=parsed.full_name,
            availability=parsed.availability,
            is_verified=parsed.is_verified,
        )

