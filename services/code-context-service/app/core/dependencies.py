"""Reusable FastAPI authentication, RBAC, and service dependency getters."""

from __future__ import annotations

from collections.abc import Callable, Coroutine
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from fastapi import Depends, Request
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from app.core.config import Settings
from app.core.exceptions import ForbiddenError, UnauthorizedError
from app.core.logging import MetricsRegistry
from app.core.security import (
    AuthenticatedUser,
    UserRole,
    extract_bearer_token,
    get_request_id,
)

if TYPE_CHECKING:
    from app.clients.github_client import GitHubServiceClient
    from app.clients.identity_client import IdentityClient
    from app.clients.project_client import ProjectClient
    from app.services.embedding_service import EmbeddingService
    from app.services.rag_service import LLMClient
    from app.services.vector_store_service import VectorStoreService

bearer_scheme = HTTPBearer(
    auto_error=False,
    description="JWT access token verified synchronously via Identity Service.",
)


@dataclass(frozen=True, slots=True)
class AuthContext:
    """Verified Identity user paired with the original bearer token for downstream forwarding."""

    user: AuthenticatedUser
    bearer_token: str


def get_settings(request: Request) -> Settings:
    """Retrieve validated Settings instance from FastAPI app state."""
    return request.app.state.settings


def get_metrics_registry(request: Request) -> MetricsRegistry:
    """Retrieve thread-safe MetricsRegistry from FastAPI app state."""
    return request.app.state.metrics


def get_identity_client(request: Request) -> IdentityClient:
    """Retrieve lifespan-managed IdentityClient from FastAPI app state."""
    return request.app.state.identity_client


def get_project_client(request: Request) -> ProjectClient:
    """Retrieve lifespan-managed ProjectClient from FastAPI app state."""
    return request.app.state.project_client


def get_github_service_client(request: Request) -> GitHubServiceClient:
    """Retrieve lifespan-managed GitHubServiceClient from FastAPI app state."""
    return request.app.state.github_service_client


def get_embedding_service(request: Request) -> EmbeddingService:
    """Retrieve lifespan-managed EmbeddingService from FastAPI app state."""
    return request.app.state.embedding_service


def get_vector_store_service(request: Request) -> VectorStoreService:
    """Retrieve lifespan-managed VectorStoreService from FastAPI app state."""
    return request.app.state.vector_store_service


def get_llm_client(request: Request) -> LLMClient:
    """Retrieve lifespan-managed LLMClient from FastAPI app state."""
    return request.app.state.llm_client


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
require_owner_or_freelancer = require_roles(UserRole.OWNER, UserRole.FREELANCER)
require_any_authenticated = require_roles(
    UserRole.OWNER,
    UserRole.FREELANCER,
    UserRole.ADMIN,
)

