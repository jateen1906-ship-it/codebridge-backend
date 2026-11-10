"""Tests for RBAC enforcement (OWNER, FREELANCER, ADMIN) and admin user controls."""

from __future__ import annotations

import httpx
import pytest
from fastapi import Depends, FastAPI

from app.core.dependencies import require_owner
from app.models.user import User, UserRole
from tests.conftest import login_user, provision_admin_account, register_user


@pytest.mark.asyncio
async def test_unauthenticated_requests_rejected_401(client: httpx.AsyncClient) -> None:
    endpoints = [
        ("GET", "/api/v1/auth/me"),
        ("GET", "/api/v1/profile"),
        ("PATCH", "/api/v1/profile"),
        ("PATCH", "/api/v1/freelancers/me"),
        ("GET", "/api/v1/auth/admin/users"),
        ("POST", "/api/v1/auth/admin/users"),
    ]
    for method, path in endpoints:
        res = await client.request(method, path)
        assert res.status_code == 401, f"Expected 401 for {method} {path}, got {res.status_code}"
        assert res.json()["error"]["code"] == "UNAUTHORIZED"


@pytest.mark.asyncio
async def test_require_owner_allows_owner_and_blocks_other_roles(
    client: httpx.AsyncClient,
    identity_app: FastAPI,
) -> None:
    @identity_app.get("/api/v1/_test-owner-only")
    async def owner_only_probe(owner: User = Depends(require_owner)) -> dict[str, str]:
        return {"user_id": str(owner.id), "role": owner.role.value}

    await register_user(
        client,
        full_name="Owner Account",
        email="owner.rbac@example.com",
        password="OwnerPassword123!",
        role="OWNER",
    )
    await register_user(
        client,
        full_name="Freelancer Account",
        email="free.rbac@example.com",
        password="FreePassword123!",
        role="FREELANCER",
    )
    await provision_admin_account(
        identity_app,
        email="admin.rbac@codebridge.dev",
        password="AdminPassword123!",
    )

    owner_token = (
        await login_user(client, email="owner.rbac@example.com", password="OwnerPassword123!")
    ).json()["access_token"]
    freelancer_token = (
        await login_user(client, email="free.rbac@example.com", password="FreePassword123!")
    ).json()["access_token"]
    admin_token = (
        await login_user(client, email="admin.rbac@codebridge.dev", password="AdminPassword123!")
    ).json()["access_token"]

    owner_res = await client.get(
        "/api/v1/_test-owner-only",
        headers={"Authorization": f"Bearer {owner_token}"},
    )
    assert owner_res.status_code == 200
    assert owner_res.json()["role"] == "OWNER"

    for blocked_token in (freelancer_token, admin_token):
        blocked_res = await client.get(
            "/api/v1/_test-owner-only",
            headers={"Authorization": f"Bearer {blocked_token}"},
        )
        assert blocked_res.status_code == 403
        assert blocked_res.json()["error"]["code"] == "FORBIDDEN"


@pytest.mark.asyncio
async def test_require_freelancer_blocks_owner_and_admin(
    client: httpx.AsyncClient,
    identity_app: FastAPI,
) -> None:
    await register_user(
        client,
        full_name="Owner User",
        email="owner2@example.com",
        password="OwnerPassword123!",
        role="OWNER",
    )
    await provision_admin_account(
        identity_app,
        email="admin2@codebridge.dev",
        password="AdminPassword123!",
    )

    owner_token = (
        await login_user(client, email="owner2@example.com", password="OwnerPassword123!")
    ).json()["access_token"]
    admin_token = (
        await login_user(client, email="admin2@codebridge.dev", password="AdminPassword123!")
    ).json()["access_token"]

    for token in (owner_token, admin_token):
        res = await client.patch(
            "/api/v1/freelancers/me",
            headers={"Authorization": f"Bearer {token}"},
            json={"bio": "Not allowed"},
        )
        assert res.status_code == 403
        assert res.json()["error"]["code"] == "FORBIDDEN"


@pytest.mark.asyncio
async def test_admin_endpoints_restricted_and_deactivation_revokes_sessions(
    client: httpx.AsyncClient,
    identity_app: FastAPI,
) -> None:
    owner = (
        await register_user(
            client,
            full_name="Regular Owner",
            email="regular.owner@example.com",
            password="OwnerPassword123!",
            role="OWNER",
        )
    ).json()
    await register_user(
        client,
        full_name="Regular Freelancer",
        email="regular.freelancer@example.com",
        password="FreePassword123!",
        role="FREELANCER",
    )
    await provision_admin_account(
        identity_app,
        email="root.admin@codebridge.dev",
        password="AdminPassword123!",
    )

    owner_tokens = (
        await login_user(client, email="regular.owner@example.com", password="OwnerPassword123!")
    ).json()
    freelancer_tokens = (
        await login_user(
            client, email="regular.freelancer@example.com", password="FreePassword123!"
        )
    ).json()
    admin_tokens = (
        await login_user(client, email="root.admin@codebridge.dev", password="AdminPassword123!")
    ).json()

    # Non-admins cannot call admin routes
    for non_admin_token in (owner_tokens["access_token"], freelancer_tokens["access_token"]):
        res = await client.get(
            "/api/v1/auth/admin/users",
            headers={"Authorization": f"Bearer {non_admin_token}"},
        )
        assert res.status_code == 403
        assert res.json()["error"]["code"] == "FORBIDDEN"

        create_res = await client.post(
            "/api/v1/auth/admin/users",
            headers={"Authorization": f"Bearer {non_admin_token}"},
            json={
                "full_name": "Rogue Admin",
                "email": "rogue@example.com",
                "password": "RoguePassword123!",
                "role": UserRole.ADMIN.value,
            },
        )
        assert create_res.status_code == 403

    # Admin can provision a second ADMIN account
    created_admin = await client.post(
        "/api/v1/auth/admin/users",
        headers={"Authorization": f"Bearer {admin_tokens['access_token']}"},
        json={
            "full_name": "Second Admin",
            "email": "second.admin@codebridge.dev",
            "password": "SecondAdminPassword123!",
            "role": UserRole.ADMIN.value,
            "is_verified": True,
        },
    )
    assert created_admin.status_code == 201
    assert created_admin.json()["role"] == "ADMIN"

    # Admin can list and filter users
    list_admins = await client.get(
        "/api/v1/auth/admin/users",
        params={"role": "ADMIN"},
        headers={"Authorization": f"Bearer {admin_tokens['access_token']}"},
    )
    assert list_admins.status_code == 200
    assert list_admins.json()["total"] == 2

    # Admin deactivates owner -> owner access and refresh tokens stop working immediately
    deactivate = await client.patch(
        f"/api/v1/auth/admin/users/{owner['id']}/status",
        headers={"Authorization": f"Bearer {admin_tokens['access_token']}"},
        json={"is_active": False},
    )
    assert deactivate.status_code == 200
    assert deactivate.json()["is_active"] is False

    blocked_me = await client.get(
        "/api/v1/auth/me",
        headers={"Authorization": f"Bearer {owner_tokens['access_token']}"},
    )
    assert blocked_me.status_code == 403
    assert blocked_me.json()["error"]["code"] == "FORBIDDEN"

    blocked_refresh = await client.post(
        "/api/v1/auth/refresh",
        json={"refresh_token": owner_tokens["refresh_token"]},
    )
    assert blocked_refresh.status_code == 401
    assert blocked_refresh.json()["error"]["code"] == "TOKEN_REVOKED"
