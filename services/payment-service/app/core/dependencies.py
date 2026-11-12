"""FastAPI authentication and RBAC dependencies for Payment Service."""

from __future__ import annotations

from fastapi import Depends, Request
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from app.core.config import Settings
from app.core.exceptions import ForbiddenError, UnauthorizedError
from app.core.security import AuthenticatedUser

bearer_scheme = HTTPBearer(auto_error=False)


def get_settings(request: Request) -> Settings:
    return request.app.state.settings


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

    request_id = getattr(request.state, "request_id", None)
    return await request.app.state.identity_client.get_current_user(
        access_token=token,
        request_id=request_id,
    )


def require_owner_or_admin(
    user: AuthenticatedUser = Depends(get_current_user),
) -> AuthenticatedUser:
    if user.role not in {"OWNER", "ADMIN"}:
        raise ForbiddenError("Only project owners or admins can perform financial escrow mutations.")
    return user


def require_authenticated(
    user: AuthenticatedUser = Depends(get_current_user),
) -> AuthenticatedUser:
    return user
