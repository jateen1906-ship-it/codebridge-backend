"""Tests for authenticated profile endpoints and privilege escalation protections."""

from __future__ import annotations

import uuid

import httpx
import pytest
from fastapi import FastAPI
from sqlalchemy import select

from app.models.user import User, UserRole
from tests.conftest import login_user, register_user


@pytest.mark.asyncio
async def test_get_profile_for_owner_and_freelancer(client: httpx.AsyncClient) -> None:
    await register_user(
        client,
        full_name="Owner Profile",
        email="owner.prof@example.com",
        password="OwnerPassword123!",
        role="OWNER",
    )
    await register_user(
        client,
        full_name="Freelancer Profile",
        email="free.prof@example.com",
        password="FreePassword123!",
        role="FREELANCER",
    )

    owner_token = (
        await login_user(client, email="owner.prof@example.com", password="OwnerPassword123!")
    ).json()["access_token"]
    free_token = (
        await login_user(client, email="free.prof@example.com", password="FreePassword123!")
    ).json()["access_token"]

    owner_prof = await client.get(
        "/api/v1/profile",
        headers={"Authorization": f"Bearer {owner_token}"},
    )
    assert owner_prof.status_code == 200
    assert owner_prof.json()["freelancer_profile"] is None
    assert "password_hash" not in owner_prof.json()

    free_prof = await client.get(
        "/api/v1/profile",
        headers={"Authorization": f"Bearer {free_token}"},
    )
    assert free_prof.status_code == 200
    assert free_prof.json()["freelancer_profile"] is not None
    assert free_prof.json()["freelancer_profile"]["skills"] == []


@pytest.mark.asyncio
async def test_patch_profile_updates_permitted_full_name_only(
    client: httpx.AsyncClient,
    identity_app: FastAPI,
) -> None:
    await register_user(
        client,
        full_name="Original Name",
        email="updatable@example.com",
        password="UpdatePassword123!",
        role="OWNER",
    )
    token = (
        await login_user(client, email="updatable@example.com", password="UpdatePassword123!")
    ).json()["access_token"]

    patch_res = await client.patch(
        "/api/v1/profile",
        headers={"Authorization": f"Bearer {token}"},
        json={"full_name": "  Updated   Full Name  "},
    )
    assert patch_res.status_code == 200
    assert patch_res.json()["full_name"] == "Updated Full Name"

    async with identity_app.state.session_factory() as session:
        user = (
            await session.execute(select(User).where(User.email == "updatable@example.com"))
        ).scalar_one()
        assert user.full_name == "Updated Full Name"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "tamper_payload",
    [
        {"full_name": "Valid Name", "role": "ADMIN"},
        {"full_name": "Valid Name", "is_verified": True},
        {"full_name": "Valid Name", "is_active": False},
        {"full_name": "Valid Name", "id": str(uuid.uuid4())},
        {"full_name": "Valid Name", "email": "hacked@example.com"},
    ],
)
async def test_patch_profile_rejects_privilege_escalation_and_flag_tampering(
    client: httpx.AsyncClient,
    identity_app: FastAPI,
    tamper_payload: dict[str, object],
) -> None:
    await register_user(
        client,
        full_name="Target User",
        email="target.tamper@example.com",
        password="TargetPassword123!",
        role="OWNER",
    )
    token = (
        await login_user(client, email="target.tamper@example.com", password="TargetPassword123!")
    ).json()["access_token"]

    res = await client.patch(
        "/api/v1/profile",
        headers={"Authorization": f"Bearer {token}"},
        json=tamper_payload,
    )
    assert res.status_code == 422
    assert res.json()["error"]["code"] == "VALIDATION_ERROR"

    async with identity_app.state.session_factory() as session:
        user = (
            await session.execute(select(User).where(User.email == "target.tamper@example.com"))
        ).scalar_one()
        assert user.role == UserRole.OWNER
        assert user.is_verified is False
        assert user.is_active is True
        assert user.full_name == "Target User"
