"""Authenticated user and internal-service-key verification for Notification Service."""

from __future__ import annotations

import hmac
import uuid
from dataclasses import dataclass
from typing import Literal

from fastapi import Depends, Header, Request
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from app.core.exceptions import ForbiddenError, UnauthorizedError

RoleType = Literal["OWNER", "FREELANCER", "ADMIN"]
bearer_scheme = HTTPBearer(auto_error=False)


@dataclass(frozen=True)
class AuthenticatedUser:
    id: uuid.UUID
    full_name: str
    email: str
    role: RoleType
    is_active: bool
    is_verified: bool
    access_token: str


async def get_current_user(
    request: Request,
    credentials: HTTPAuthorizationCredentials | None = Depends(bearer_scheme),
) -> AuthenticatedUser:
    if credentials is None or not credentials.credentials:
        raise UnauthorizedError("Authentication credentials were not provided.")
    if credentials.scheme.lower() != "bearer":
        raise UnauthorizedError("Authorization header must use Bearer authentication.")
    token = credentials.credentials.strip()
    if not token:
        raise UnauthorizedError("Bearer access token must not be empty.")
    return await request.app.state.identity_client.get_current_user(
        access_token=token,
        request_id=getattr(request.state, "request_id", None),
    )


async def require_internal_or_admin(
    request: Request,
    x_service_key: str | None = Header(default=None, alias="X-Service-Key"),
    credentials: HTTPAuthorizationCredentials | None = Depends(bearer_scheme),
) -> str:
    """Authenticate internal microservice emission or ADMIN user."""
    expected_key = request.app.state.settings.INTERNAL_SERVICE_KEY
    if x_service_key:
        if hmac.compare_digest(x_service_key, expected_key):
            return "internal-service"
        raise ForbiddenError("Invalid internal service credential.")

    if credentials is not None and credentials.credentials:
        user = await request.app.state.identity_client.get_current_user(
            access_token=credentials.credentials.strip(),
            request_id=getattr(request.state, "request_id", None),
        )
        if user.role != "ADMIN":
            raise ForbiddenError("Only internal services or ADMIN users may publish notification events.")
        return f"admin:{user.id}"

    raise UnauthorizedError("Internal service key or ADMIN Bearer credentials required.")
