"""Authenticated user model and cryptographic HMAC signing helper for Verification Service."""

from __future__ import annotations

import hashlib
import hmac
import json
import secrets
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


def generate_public_verification_id() -> str:
    """Generate opaque, URL-safe, non-sequential public verification ID."""
    return f"cbv_{secrets.token_hex(12)}"


def canonicalize_payload(payload: dict[str, Any]) -> str:
    """Serialize verification payload deterministically for hashing and signing."""
    return json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=True)


def compute_sha256_hex(canonical_json: str) -> str:
    return hashlib.sha256(canonical_json.encode("utf-8")).hexdigest()


def sign_payload_hmac_sha256(canonical_json: str, secret: str) -> str:
    return hmac.new(
        secret.encode("utf-8"),
        canonical_json.encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()


def verify_payload_signature(
    *,
    canonical_json: str,
    expected_sha256: str,
    signature_hex: str,
    secret: str,
) -> tuple[bool, bool]:
    """Verify both SHA-256 integrity and HMAC-SHA256 signature in constant time."""
    actual_sha = compute_sha256_hex(canonical_json)
    hash_ok = hmac.compare_digest(actual_sha, expected_sha256)
    expected_sig = sign_payload_hmac_sha256(canonical_json, secret)
    sig_ok = hmac.compare_digest(expected_sig, signature_hex)
    return hash_ok, (hash_ok and sig_ok)
