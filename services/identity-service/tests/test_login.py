"""Tests for credential login, generic errors, inactive accounts, and token issuance."""

from __future__ import annotations

import httpx
import pytest
from fastapi import FastAPI
from sqlalchemy import select, update

from app.models.refresh_token import RefreshToken
from app.models.user import User
from tests.conftest import login_user, register_user


@pytest.mark.asyncio
async def test_login_success_returns_tokens_and_public_user(
    client: httpx.AsyncClient,
    identity_app: FastAPI,
) -> None:
    reg = await register_user(
        client,
        full_name="Carlos Owner",
        email="carlos@example.com",
        password="CarlosPassword123!",
        role="OWNER",
    )
    assert reg.status_code == 201

    login_res = await login_user(
        client,
        email="Carlos@Example.COM",
        password="CarlosPassword123!",
    )
    assert login_res.status_code == 200
    body = login_res.json()
    assert body["token_type"] == "bearer"
    assert body["expires_in"] == 900
    assert body["refresh_expires_in"] == 7 * 86400
    assert isinstance(body["access_token"], str) and len(body["access_token"]) > 20
    assert isinstance(body["refresh_token"], str) and len(body["refresh_token"]) > 20
    assert body["user"]["email"] == "carlos@example.com"
    assert body["user"]["role"] == "OWNER"
    assert "password_hash" not in body["user"]

    async with identity_app.state.session_factory() as session:
        tokens = list((await session.execute(select(RefreshToken))).scalars().all())
        assert len(tokens) == 1
        assert len(tokens[0].jti_hash) == 64
        assert tokens[0].jti_hash != body["refresh_token"]


@pytest.mark.asyncio
async def test_login_wrong_password_and_unknown_email_return_identical_generic_401(
    client: httpx.AsyncClient,
) -> None:
    reg = await register_user(
        client,
        full_name="Diana Freelancer",
        email="diana@example.com",
        password="DianaPassword123!",
        role="FREELANCER",
    )
    assert reg.status_code == 201

    wrong_pw = await login_user(
        client,
        email="diana@example.com",
        password="WrongPassword123!",
    )
    unknown_email = await login_user(
        client,
        email="nonexistent@example.com",
        password="WrongPassword123!",
    )

    assert wrong_pw.status_code == 401
    assert unknown_email.status_code == 401
    assert wrong_pw.json()["error"] == unknown_email.json()["error"]
    assert wrong_pw.json()["error"]["code"] == "INVALID_CREDENTIALS"
    assert wrong_pw.json()["error"]["message"] == "Invalid email or password."


@pytest.mark.asyncio
async def test_login_inactive_user_rejected_with_403(
    client: httpx.AsyncClient,
    identity_app: FastAPI,
) -> None:
    reg = await register_user(
        client,
        full_name="Inactive User",
        email="inactive@example.com",
        password="InactivePassword123!",
        role="OWNER",
    )
    assert reg.status_code == 201

    async with identity_app.state.session_factory() as session:
        await session.execute(
            update(User).where(User.email == "inactive@example.com").values(is_active=False)
        )
        await session.commit()

    login_res = await login_user(
        client,
        email="inactive@example.com",
        password="InactivePassword123!",
    )
    assert login_res.status_code == 403
    assert login_res.json()["error"]["code"] == "FORBIDDEN"
