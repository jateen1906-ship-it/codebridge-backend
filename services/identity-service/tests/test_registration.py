"""Tests for public registration, role guardrails, email normalization, and validation."""

from __future__ import annotations

import httpx
import pytest
from fastapi import FastAPI
from sqlalchemy import select

from app.models.freelancer_profile import FreelancerProfile
from app.models.user import User, UserRole
from tests.conftest import register_user


@pytest.mark.asyncio
async def test_register_owner_success_omits_password_and_creates_no_freelancer_profile(
    client: httpx.AsyncClient,
    identity_app: FastAPI,
) -> None:
    response = await register_user(
        client,
        full_name="  Alice   Owner  ",
        email="Alice.Owner@Example.COM",
        password="OwnerPassword123!",
        role="OWNER",
    )

    assert response.status_code == 201
    assert "X-Request-ID" in response.headers
    body = response.json()
    assert body["full_name"] == "Alice Owner"
    assert body["email"] == "alice.owner@example.com"
    assert body["role"] == "OWNER"
    assert body["is_active"] is True
    assert body["is_verified"] is False
    assert "password_hash" not in body
    assert "password" not in body

    async with identity_app.state.session_factory() as session:
        profile = (await session.execute(select(FreelancerProfile))).scalar_one_or_none()
        assert profile is None


@pytest.mark.asyncio
async def test_register_freelancer_creates_empty_freelancer_profile(
    client: httpx.AsyncClient,
    identity_app: FastAPI,
) -> None:
    response = await register_user(
        client,
        full_name="Bob Developer",
        email="bob.dev@example.com",
        password="DevPassword123!",
        role="FREELANCER",
    )

    assert response.status_code == 201
    body = response.json()
    assert body["role"] == "FREELANCER"

    async with identity_app.state.session_factory() as session:
        profiles = list((await session.execute(select(FreelancerProfile))).scalars().all())
        assert len(profiles) == 1
        assert str(profiles[0].user_id) == body["id"]
        assert profiles[0].bio is None
        assert profiles[0].skills == []
        assert profiles[0].experience_years == 0
        assert profiles[0].availability.value == "AVAILABLE"


@pytest.mark.asyncio
async def test_register_duplicate_email_case_insensitive_returns_409(
    client: httpx.AsyncClient,
) -> None:
    first = await register_user(
        client,
        full_name="First User",
        email="Duplicate.Email@Example.com",
        password="StrongPassword123!",
        role="OWNER",
    )
    assert first.status_code == 201

    second = await register_user(
        client,
        full_name="Second User",
        email="duplicate.email@example.com",
        password="AnotherStrongPassword123!",
        role="FREELANCER",
    )
    assert second.status_code == 409
    err = second.json()
    assert err["success"] is False
    assert err["error"]["code"] == "EMAIL_ALREADY_EXISTS"
    assert "request_id" in err


@pytest.mark.asyncio
async def test_register_rejects_public_admin_role(
    client: httpx.AsyncClient,
    identity_app: FastAPI,
) -> None:
    response = await register_user(
        client,
        full_name="Attacker Admin",
        email="attacker@example.com",
        password="StrongPassword123!",
        role=UserRole.ADMIN.value,
    )

    assert response.status_code == 403
    err = response.json()
    assert err["success"] is False
    assert err["error"]["code"] == "FORBIDDEN"

    async with identity_app.state.session_factory() as session:
        user = (await session.execute(select(User))).scalar_one_or_none()
        assert user is None


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "invalid_email",
    [
        "not-an-email",
        "missing-domain@",
        "@missing-local.com",
        "spaces in@example.com",
    ],
)
async def test_register_invalid_email_rejected(
    client: httpx.AsyncClient,
    invalid_email: str,
) -> None:
    response = await register_user(
        client,
        full_name="Validation Test",
        email=invalid_email,
        password="StrongPassword123!",
        role="OWNER",
    )
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "VALIDATION_ERROR"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "weak_password",
    [
        "Short1!",  # < 8 chars
        "alllowercase123!",  # missing uppercase
        "ALLUPPERCASE123!",  # missing lowercase
        "NoDigitsHere!@#",  # missing digit
        "NoSpecialChars123",  # missing special char
    ],
)
async def test_register_weak_passwords_rejected(
    client: httpx.AsyncClient,
    weak_password: str,
) -> None:
    response = await register_user(
        client,
        full_name="Weak Password User",
        email="weakpass@example.com",
        password=weak_password,
        role="OWNER",
    )
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "VALIDATION_ERROR"


@pytest.mark.asyncio
async def test_register_rejects_extra_flag_fields(client: httpx.AsyncClient) -> None:
    response = await client.post(
        "/api/v1/auth/register",
        json={
            "full_name": "Flag Tamper",
            "email": "flagtamper@example.com",
            "password": "StrongPassword123!",
            "role": "OWNER",
            "is_verified": True,
        },
    )
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "VALIDATION_ERROR"
