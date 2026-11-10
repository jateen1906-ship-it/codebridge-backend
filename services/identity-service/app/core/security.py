"""Argon2 password hashing, SHA-256 JTI hashing, and PyJWT token utilities."""

from __future__ import annotations

import hashlib
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Literal

import jwt
from pwdlib import PasswordHash
from pwdlib.hashers.argon2 import Argon2Hasher

from app.core.config import Settings
from app.core.exceptions import InvalidTokenError, TokenExpiredError

_ARGON2_HASHER = PasswordHash((Argon2Hasher(),))
_DUMMY_PASSWORD_HASH: str | None = None


def utc_now() -> datetime:
    """Return the current UTC timezone-aware datetime."""
    return datetime.now(timezone.utc)


def ensure_utc(dt: datetime) -> datetime:
    """Normalize naive or offset datetimes into UTC timezone-aware datetimes."""
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def hash_password(plain_password: str) -> str:
    """Hash a plaintext password using Argon2."""
    return _ARGON2_HASHER.hash(plain_password)


def verify_password(plain_password: str, password_hash: str) -> bool:
    """Verify a plaintext password against an Argon2 hash in constant time."""
    try:
        return bool(_ARGON2_HASHER.verify(plain_password, password_hash))
    except Exception:
        return False


def get_dummy_password_hash() -> str:
    """Return a cached Argon2 hash used to equalize timing on unknown email logins."""
    global _DUMMY_PASSWORD_HASH
    if _DUMMY_PASSWORD_HASH is None:
        _DUMMY_PASSWORD_HASH = hash_password("DummyTimingEqualizer#2026!")
    return _DUMMY_PASSWORD_HASH


def hash_jti(jti: uuid.UUID | str) -> str:
    """Compute a deterministic SHA-256 digest of a refresh token JTI."""
    return hashlib.sha256(str(jti).encode("utf-8")).hexdigest()


@dataclass(frozen=True, slots=True)
class TokenPayload:
    """Validated claims extracted from a signed access or refresh JWT."""

    sub: uuid.UUID
    jti: uuid.UUID
    token_type: Literal["access", "refresh"]
    iat: datetime
    exp: datetime


def _encode_token(
    *,
    user_id: uuid.UUID,
    token_type: Literal["access", "refresh"],
    expires_delta: timedelta,
    settings: Settings,
    jti: uuid.UUID | None = None,
) -> tuple[str, TokenPayload]:
    now = utc_now()
    exp = now + expires_delta
    token_jti = jti or uuid.uuid4()

    claims = {
        "sub": str(user_id),
        "jti": str(token_jti),
        "type": token_type,
        "iat": int(now.timestamp()),
        "exp": int(exp.timestamp()),
    }
    encoded = jwt.encode(
        claims,
        settings.JWT_SECRET_KEY.get_secret_value(),
        algorithm=settings.JWT_ALGORITHM,
    )
    payload = TokenPayload(
        sub=user_id,
        jti=token_jti,
        token_type=token_type,
        iat=now,
        exp=exp,
    )
    return encoded, payload


def create_access_token(
    *,
    user_id: uuid.UUID,
    settings: Settings,
    jti: uuid.UUID | None = None,
) -> tuple[str, TokenPayload]:
    """Create a signed short-lived access JWT."""
    return _encode_token(
        user_id=user_id,
        token_type="access",
        expires_delta=timedelta(minutes=settings.ACCESS_TOKEN_EXPIRE_MINUTES),
        settings=settings,
        jti=jti,
    )


def create_refresh_token(
    *,
    user_id: uuid.UUID,
    settings: Settings,
    jti: uuid.UUID | None = None,
) -> tuple[str, TokenPayload]:
    """Create a signed refresh JWT with a unique JTI."""
    return _encode_token(
        user_id=user_id,
        token_type="refresh",
        expires_delta=timedelta(days=settings.REFRESH_TOKEN_EXPIRE_DAYS),
        settings=settings,
        jti=jti,
    )


def decode_token(
    *,
    token: str,
    expected_type: Literal["access", "refresh"],
    settings: Settings,
) -> TokenPayload:
    """Verify JWT signature, expiration, required claims, and strict token type."""
    try:
        raw_claims = jwt.decode(
            token,
            settings.JWT_SECRET_KEY.get_secret_value(),
            algorithms=[settings.JWT_ALGORITHM],
            options={"require": ["sub", "jti", "type", "iat", "exp"]},
        )
    except jwt.ExpiredSignatureError as exc:
        raise TokenExpiredError() from exc
    except jwt.InvalidTokenError as exc:
        raise InvalidTokenError() from exc

    token_type = raw_claims.get("type")
    if token_type != expected_type:
        raise InvalidTokenError("Token type is invalid for this operation.")

    try:
        sub_uuid = uuid.UUID(str(raw_claims["sub"]))
        jti_uuid = uuid.UUID(str(raw_claims["jti"]))
        iat_dt = datetime.fromtimestamp(int(raw_claims["iat"]), tz=timezone.utc)
        exp_dt = datetime.fromtimestamp(int(raw_claims["exp"]), tz=timezone.utc)
    except (ValueError, TypeError) as exc:
        raise InvalidTokenError() from exc

    return TokenPayload(
        sub=sub_uuid,
        jti=jti_uuid,
        token_type=expected_type,
        iat=iat_dt,
        exp=exp_dt,
    )

