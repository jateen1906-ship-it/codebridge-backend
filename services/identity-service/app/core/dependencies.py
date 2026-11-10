"""Reusable authentication and role-based access control (RBAC) FastAPI dependencies."""

from __future__ import annotations

from collections.abc import Callable, Coroutine
from typing import Any

from fastapi import Depends, Request
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings
from app.core.database import get_db_session
from app.core.exceptions import ForbiddenError, InvalidTokenError, UnauthorizedError
from app.core.security import decode_token
from app.models.user import User, UserRole
from app.repositories.user_repository import UserRepository

bearer_scheme = HTTPBearer(
    auto_error=False,
    description="JWT access token in Authorization: Bearer <token> header.",
)


def get_settings(request: Request) -> Settings:
    """Retrieve validated application settings from FastAPI app state."""
    return request.app.state.settings


async def get_current_user(
    credentials: HTTPAuthorizationCredentials | None = Depends(bearer_scheme),
    session: AsyncSession = Depends(get_db_session),
    settings: Settings = Depends(get_settings),
) -> User:
    """Authenticate an active user from a signed access JWT."""
    if credentials is None or credentials.scheme.lower() != "bearer" or not credentials.credentials:
        raise UnauthorizedError("Authentication credentials were not provided.")

    claims = decode_token(
        token=credentials.credentials,
        expected_type="access",
        settings=settings,
    )

    user_repo = UserRepository(session)
    user = await user_repo.get_by_id(claims.sub)
    if user is None:
        raise InvalidTokenError("Authenticated user no longer exists.")
    if not user.is_active:
        raise ForbiddenError("User account is inactive.")
    return user


def require_roles(
    *allowed_roles: UserRole,
) -> Callable[[User], Coroutine[Any, Any, User]]:
    """Create a reusable RBAC dependency permitting only users with allowed roles."""
    allowed_set = frozenset(allowed_roles)

    async def _role_guard(current_user: User = Depends(get_current_user)) -> User:
        if current_user.role not in allowed_set:
            allowed_names = ", ".join(sorted(role.value for role in allowed_set))
            raise ForbiddenError(
                f"Access denied. Required role(s): {allowed_names}."
            )
        return current_user

    return _role_guard


require_owner = require_roles(UserRole.OWNER)
require_freelancer = require_roles(UserRole.FREELANCER)
require_admin = require_roles(UserRole.ADMIN)
require_any_authenticated = require_roles(
    UserRole.OWNER,
    UserRole.FREELANCER,
    UserRole.ADMIN,
)

