"""Strict ADMIN-only authentication and RBAC dependencies for Admin Service."""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from typing import Literal

from fastapi import Depends, Request
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
        raise UnauthorizedError()
    if credentials.scheme.lower() != "bearer":
        raise UnauthorizedError("Authorization header must use Bearer authentication.")
    token = credentials.credentials.strip()
    if not token:
        raise UnauthorizedError()
    return await request.app.state.identity_client.get_current_user(
        access_token=token,
        request_id=getattr(request.state, "request_id", None),
    )


def require_admin(user: AuthenticatedUser = Depends(get_current_user)) -> AuthenticatedUser:
    if user.role != "ADMIN":
        raise ForbiddenError("Only ADMIN accounts may access administrative endpoints.")
    return user
