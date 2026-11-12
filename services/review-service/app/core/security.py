"""Authenticated user models and role checks for Review Service."""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from typing import Literal

RoleType = Literal["OWNER", "FREELANCER", "ADMIN"]


@dataclass(frozen=True)
class AuthenticatedUser:
    """Validated user identity returned by Identity Service."""

    id: uuid.UUID
    full_name: str
    email: str
    role: RoleType
    is_active: bool
    is_verified: bool
    access_token: str

