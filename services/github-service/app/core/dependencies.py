"""Reusable FastAPI authentication, RBAC, and service-client dependencies for GitHub Service."""

from __future__ import annotations

from collections.abc import Callable, Coroutine
from dataclasses import dataclass
from typing import Any

from fastapi import Depends, Request
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from app.clients.github_client import GitHubHTTPClient
from app.clients.identity_client import IdentityClient
from app.clients.project_client import ProjectClient
from app.core.config import Settings
from app.core.encryption import TokenEncryptionService
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


@dataclass(frozen=True, slots=True)
class AuthContext:
    """Verified Identity user paired with raw bearer token for downstream Project Service forwarding."""

    user: AuthenticatedUser
    bearer_token: str


def get_settings(request: Request) -> Settings:
    """Retrieve validated Settings instance from FastAPI app state."""
    return request.app.state.settings


def get_encryption_service(request: Request) -> TokenEncryptionService:
    """Retrieve initialized TokenEncryptionService from FastAPI app state."""
    return request.app.state.encryption_service


def get_identity_client(request: Request) -> IdentityClient:
    """Retrieve lifespan-managed IdentityClient from FastAPI app state."""
    return request.app.state.identity_client


def get_project_client(request: Request) -> ProjectClient:
    """Retrieve lifespan-managed ProjectClient from FastAPI app state."""
    return request.app.state.project_client


def get_github_client(request: Request) -> GitHubHTTPClient:
    """Retrieve lifespan-managed GitHubHTTPClient from FastAPI app state."""
    return request.app.state.github_client


async def get_auth_context(
    request: Request,
    credentials: HTTPAuthorizationCredentials | None = Depends(bearer_scheme),
    identity_client: IdentityClient = Depends(get_identity_client),
) -> AuthContext:
    """Authenticate caller strictly via Identity Service GET /api/v1/auth/me."""
    token = extract_bearer_token(credentials)
    if token is None:
        raise UnauthorizedError("Authentication credentials were not provided.")

    request_id = get_request_id(request)
    user = await identity_client.authenticate_token(token, request_id=request_id)
    return AuthContext(user=user, bearer_token=token)


async def get_optional_auth_context(
    request: Request,
    credentials: HTTPAuthorizationCredentials | None = Depends(bearer_scheme),
    identity_client: IdentityClient = Depends(get_identity_client),
) -> AuthContext | None:
    """Authenticate caller ONLY if Authorization header is supplied (used on OAuth callback)."""
    raw_auth = request.headers.get("authorization")
    if raw_auth is None:
        return None
    token = extract_bearer_token(credentials)
    if token is None:
        raise UnauthorizedError("Invalid authentication scheme.")
    request_id = get_request_id(request)
    user = await identity_client.authenticate_token(token, request_id=request_id)
    return AuthContext(user=user, bearer_token=token)


def require_roles(
    *allowed_roles: UserRole,
) -> Callable[[AuthContext], Coroutine[Any, Any, AuthContext]]:
    """Build an RBAC dependency permitting only active users with allowed roles."""
    allowed_set = frozenset(allowed_roles)

    async def _role_guard(
        auth_ctx: AuthContext = Depends(get_auth_context),
    ) -> AuthContext:
        if auth_ctx.user.role not in allowed_set:
            allowed_names = ", ".join(sorted(role.value for role in allowed_set))
            raise ForbiddenError(f"Access denied. Required role(s): {allowed_names}.")
        return auth_ctx

    return _role_guard


require_owner = require_roles(UserRole.OWNER)
require_any_authenticated = require_roles(
    UserRole.OWNER,
    UserRole.FREELANCER,
    UserRole.ADMIN,
)

