"""Authenticated user model and hashing utilities for Payment Service."""

from __future__ import annotations

import hashlib
import json
import uuid
from dataclasses import dataclass
from typing import Any, Literal

RoleType = Literal["OWNER", "FREELANCER", "ADMIN"]


@dataclass(frozen=True)
class AuthenticatedUser:
    id: uuid.UUID
    full_name: str
    email: str
    role: RoleType
    is_active: bool
    is_verified: bool
    access_token: str


def hash_request_payload(payload: dict[str, Any]) -> str:
    serialized = json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(serialized.encode("utf-8")).hexdigest()
