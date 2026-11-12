"""Reusable FastAPI authentication and RBAC dependencies for Project Service."""

from __future__ import annotations

from collections.abc import Callable, Coroutine
from typing import Any

from fastapi import Depends, Request
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from app.clients.identity_client import IdentityClient
from app.core.config import Settings
from app.core.exceptions import ForbiddenError, UnauthorizedError
from app.core.security import (
    AuthenticatedUser,
    UserRole,
    extract_bearer_token,
    get_request_id,
)

bearer_scheme = HTTPBearer(
    auto_error=False,
    description="JWT access token verified synchronously via Identity Service.",
)


def get_settings(request: Request) -> Settings:
    """Retrieve validated Settings instance from FastAPI app state."""
    return request.app.state.settings


def get_identity_client(request: Request) -> IdentityClient:
    """Retrieve the lifespan-managed IdentityClient from FastAPI app state."""
    return request.app.state.identity_client


async def get_current_user(
    request: Request,
    credentials: HTTPAuthorizationCredentials | None = Depends(bearer_scheme),
    identity_client: IdentityClient = Depends(get_identity_client),
) -> AuthenticatedUser:
    """Authenticate caller strictly via Identity Service GET /api/v1/auth/me."""
    token = extract_bearer_token(credentials)
    if token is None:
        raise UnauthorizedError("Authentication credentials were not provided.")

    request_id = get_request_id(request)
    return await identity_client.authenticate_token(token, request_id=request_id)


def require_roles(
    *allowed_roles: UserRole,
) -> Callable[[AuthenticatedUser], Coroutine[Any, Any, AuthenticatedUser]]:
    """Build an RBAC dependency permitting only active users with allowed roles."""
    allowed_set = frozenset(allowed_roles)

    async def _role_guard(
        current_user: AuthenticatedUser = Depends(get_current_user),
    ) -> AuthenticatedUser:
        if current_user.role not in allowed_set:
            allowed_names = ", ".join(sorted(role.value for role in allowed_set))
            raise ForbiddenError(f"Access denied. Required role(s): {allowed_names}.")
        return current_user

    return _role_guard


require_owner = require_roles(UserRole.OWNER)
require_freelancer = require_roles(UserRole.FREELANCER)
require_owner_or_freelancer = require_roles(UserRole.OWNER, UserRole.FREELANCER)
require_admin = require_roles(UserRole.ADMIN)
require_any_authenticated = require_roles(
    UserRole.OWNER,
    UserRole.FREELANCER,
    UserRole.ADMIN,
)

