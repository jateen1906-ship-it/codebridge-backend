"""Tests for JWT validation, token type isolation, rotation, reuse detection, and logout."""

from __future__ import annotations

import uuid
from datetime import timedelta

import httpx
import jwt
import pytest
from fastapi import FastAPI
from sqlalchemy import select

from app.core.config import Settings
from app.core.security import utc_now
from app.models.refresh_token import RefreshToken
from tests.conftest import login_user, register_user


def _craft_custom_jwt(
    *,
    settings: Settings,
    sub: str,
    token_type: str,
    expires_delta: timedelta,
    secret_override: str | None = None,
) -> str:
    now = utc_now()
    claims = {
        "sub": sub,
        "jti": str(uuid.uuid4()),
        "type": token_type,
        "iat": int((now - timedelta(hours=1)).timestamp()),
        "exp": int((now + expires_delta).timestamp()),
    }
    return jwt.encode(
        claims,
        secret_override or settings.JWT_SECRET_KEY.get_secret_value(),
        algorithm=settings.JWT_ALGORITHM,
    )


@pytest.mark.asyncio
async def test_valid_access_token_fetches_auth_me(client: httpx.AsyncClient) -> None:
    await register_user(
        client,
        full_name="Evan Owner",
        email="evan@example.com",
        password="EvanPassword123!",
        role="OWNER",
    )
    tokens = (
        await login_user(client, email="evan@example.com", password="EvanPassword123!")
    ).json()

    me_res = await client.get(
        "/api/v1/auth/me",
        headers={"Authorization": f"Bearer {tokens['access_token']}"},
    )
    assert me_res.status_code == 200
    assert me_res.json()["email"] == "evan@example.com"
    assert me_res.json()["role"] == "OWNER"


@pytest.mark.asyncio
async def test_expired_and_tampered_access_tokens_rejected(
    client: httpx.AsyncClient,
    test_settings: Settings,
) -> None:
    reg = (
        await register_user(
            client,
            full_name="Fiona User",
            email="fiona@example.com",
            password="FionaPassword123!",
            role="FREELANCER",
        )
    ).json()

    expired_token = _craft_custom_jwt(
        settings=test_settings,
        sub=reg["id"],
        token_type="access",
        expires_delta=timedelta(minutes=-5),
    )
    expired_res = await client.get(
        "/api/v1/auth/me",
        headers={"Authorization": f"Bearer {expired_token}"},
    )
    assert expired_res.status_code == 401
    assert expired_res.json()["error"]["code"] == "TOKEN_EXPIRED"

    bad_sig_token = _craft_custom_jwt(
        settings=test_settings,
        sub=reg["id"],
        token_type="access",
        expires_delta=timedelta(minutes=15),
        secret_override="completely-different-signing-key-not-matching-server-secret",
    )
    bad_sig_res = await client.get(
        "/api/v1/auth/me",
        headers={"Authorization": f"Bearer {bad_sig_token}"},
    )
    assert bad_sig_res.status_code == 401
    assert bad_sig_res.json()["error"]["code"] == "INVALID_TOKEN"


@pytest.mark.asyncio
async def test_token_type_confusion_strictly_rejected(client: httpx.AsyncClient) -> None:
    await register_user(
        client,
        full_name="George User",
        email="george@example.com",
        password="GeorgePassword123!",
        role="OWNER",
    )
    tokens = (
        await login_user(client, email="george@example.com", password="GeorgePassword123!")
    ).json()

    # Refresh token cannot be used as Bearer access token.
    me_res = await client.get(
        "/api/v1/auth/me",
        headers={"Authorization": f"Bearer {tokens['refresh_token']}"},
    )
    assert me_res.status_code == 401
    assert me_res.json()["error"]["code"] == "INVALID_TOKEN"

    # Access token cannot be used on /auth/refresh.
    refresh_res = await client.post(
        "/api/v1/auth/refresh",
        json={"refresh_token": tokens["access_token"]},
    )
    assert refresh_res.status_code == 401
    assert refresh_res.json()["error"]["code"] == "INVALID_TOKEN"


@pytest.mark.asyncio
async def test_refresh_token_rotation_and_reuse_detection_revokes_entire_family(
    client: httpx.AsyncClient,
    identity_app: FastAPI,
) -> None:
    await register_user(
        client,
        full_name="Hannah Dev",
        email="hannah@example.com",
        password="HannahPassword123!",
        role="FREELANCER",
    )
    pair1 = (
        await login_user(client, email="hannah@example.com", password="HannahPassword123!")
    ).json()
    refresh_1 = pair1["refresh_token"]

    # Rotate R1 -> R2
    rotate_res = await client.post(
        "/api/v1/auth/refresh",
        json={"refresh_token": refresh_1},
    )
    assert rotate_res.status_code == 200
    pair2 = rotate_res.json()
    refresh_2 = pair2["refresh_token"]
    assert refresh_2 != refresh_1
    assert pair2["access_token"] != pair1["access_token"]

    async with identity_app.state.session_factory() as session:
        records = list(
            (await session.execute(select(RefreshToken).order_by(RefreshToken.created_at.asc())))
            .scalars()
            .all()
        )
        assert len(records) == 2
        assert records[0].family_id == records[1].family_id
        assert records[0].revoked_at is not None
        assert records[0].replaced_by_token_id == records[1].id
        assert records[1].revoked_at is None

    # Replay R1 -> reuse detected -> 401 TOKEN_REVOKED and entire family compromised
    replay_res = await client.post(
        "/api/v1/auth/refresh",
        json={"refresh_token": refresh_1},
    )
    assert replay_res.status_code == 401
    assert replay_res.json()["error"]["code"] == "TOKEN_REVOKED"

    # Subsequent use of R2 MUST also fail with 401 TOKEN_REVOKED because family was revoked
    pair2_after_reuse = await client.post(
        "/api/v1/auth/refresh",
        json={"refresh_token": refresh_2},
    )
    assert pair2_after_reuse.status_code == 401
    assert pair2_after_reuse.json()["error"]["code"] == "TOKEN_REVOKED"


@pytest.mark.asyncio
async def test_expired_refresh_token_rejected(
    client: httpx.AsyncClient,
    test_settings: Settings,
) -> None:
    reg = (
        await register_user(
            client,
            full_name="Ian User",
            email="ian@example.com",
            password="IanPassword123!",
            role="OWNER",
        )
    ).json()
    expired_refresh = _craft_custom_jwt(
        settings=test_settings,
        sub=reg["id"],
        token_type="refresh",
        expires_delta=timedelta(minutes=-1),
    )

    res = await client.post(
        "/api/v1/auth/refresh",
        json={"refresh_token": expired_refresh},
    )
    assert res.status_code == 401
    assert res.json()["error"]["code"] == "TOKEN_EXPIRED"


@pytest.mark.asyncio
async def test_logout_revokes_session_and_rejects_cross_user_revocation(
    client: httpx.AsyncClient,
) -> None:
    await register_user(
        client,
        full_name="User One",
        email="user1@example.com",
        password="UserOnePass123!",
        role="OWNER",
    )
    await register_user(
        client,
        full_name="User Two",
        email="user2@example.com",
        password="UserTwoPass123!",
        role="FREELANCER",
    )

    u1_tokens = (
        await login_user(client, email="user1@example.com", password="UserOnePass123!")
    ).json()
    u2_tokens = (
        await login_user(client, email="user2@example.com", password="UserTwoPass123!")
    ).json()

    # User Two cannot revoke User One's refresh token
    cross_logout = await client.post(
        "/api/v1/auth/logout",
        headers={"Authorization": f"Bearer {u2_tokens['access_token']}"},
        json={"refresh_token": u1_tokens["refresh_token"]},
    )
    assert cross_logout.status_code == 403
    assert cross_logout.json()["error"]["code"] == "FORBIDDEN"

    # User One logs out properly
    logout_res = await client.post(
        "/api/v1/auth/logout",
        headers={"Authorization": f"Bearer {u1_tokens['access_token']}"},
        json={"refresh_token": u1_tokens["refresh_token"]},
    )
    assert logout_res.status_code == 200
    assert logout_res.json()["success"] is True

    # Reusing revoked refresh token after logout fails with 401 TOKEN_REVOKED
    post_logout_refresh = await client.post(
        "/api/v1/auth/refresh",
        json={"refresh_token": u1_tokens["refresh_token"]},
    )
    assert post_logout_refresh.status_code == 401
    assert post_logout_refresh.json()["error"]["code"] == "TOKEN_REVOKED"
