"""Pytest integration tests for append-only Audit Service."""

from __future__ import annotations

import uuid
from pathlib import Path

import httpx
import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient

from app.clients.service_clients import IdentityClient, ProjectClient
from app.core.config import Settings
from app.main import create_app
from app.models import Base

OWNER_ID = uuid.UUID("11111111-1111-4111-8111-111111111111")
OTHER_USER_ID = uuid.UUID("22222222-2222-4222-8222-222222222222")
ADMIN_ID = uuid.UUID("55555555-5555-4555-8555-555555555555")
PROJECT_ID = uuid.UUID("aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa")

OWNER_TOKEN = "tok-owner"
OTHER_TOKEN = "tok-other"
ADMIN_TOKEN = "tok-admin"
INTERNAL_KEY = "test-internal-audit-key"


@pytest_asyncio.fixture
async def client(tmp_path: Path):
    db_file = tmp_path / "audit_test.db"
    settings = Settings(
        APP_ENV="test",
        DATABASE_URL=f"sqlite+aiosqlite:///{db_file.as_posix()}",
        INTERNAL_SERVICE_KEY=INTERNAL_KEY,
    )
    application = create_app(settings_override=settings)
    users = {
        OWNER_TOKEN: {"id": str(OWNER_ID), "full_name": "Alice", "email": "a@example.com", "role": "OWNER"},
        OTHER_TOKEN: {"id": str(OTHER_USER_ID), "full_name": "Eve", "email": "e@example.com", "role": "FREELANCER"},
        ADMIN_TOKEN: {"id": str(ADMIN_ID), "full_name": "Admin", "email": "admin@example.com", "role": "ADMIN"},
    }
    application.state.identity_client = IdentityClient(
        base_url="http://id.local",
        timeout_seconds=5.0,
        transport=httpx.MockTransport(
            lambda req: httpx.Response(200, json=users[req.headers["authorization"].removeprefix("Bearer ").strip()])
            if req.headers.get("authorization", "").removeprefix("Bearer ").strip() in users
            else httpx.Response(401)
        ),
    )
    application.state.project_client = ProjectClient(
        base_url="http://proj.local",
        timeout_seconds=5.0,
        transport=httpx.MockTransport(
            lambda req: httpx.Response(
                200,
                json={"id": str(PROJECT_ID), "owner_id": str(OWNER_ID), "active_assignment": None},
            )
            if OWNER_TOKEN in req.headers.get("authorization", "") or ADMIN_TOKEN in req.headers.get("authorization", "")
            else httpx.Response(403)
        ),
    )
    async with application.state.engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    async with AsyncClient(transport=ASGITransport(app=application), base_url="http://testserver") as c:
        yield c
    await application.state.engine.dispose()


@pytest.mark.asyncio
async def test_audit_append_only_scrubbing_and_rbac_queries(client: AsyncClient) -> None:
    assert (await client.get("/health")).status_code == 200
    assert (await client.get("/ready")).status_code == 200

    # Non-admin user cannot append arbitrary audit logs
    assert (
        await client.post(
            "/api/v1/audit/events",
            json={
                "service_name": "spoof",
                "action": "SPOOF_ACTION",
                "resource_type": "user",
            },
            headers={"Authorization": f"Bearer {OWNER_TOKEN}"},
        )
    ).status_code == 403

    # Internal service records event with accidental secret fields -> recursively scrubbed!
    res = await client.post(
        "/api/v1/audit/events",
        json={
            "service_name": "github-service",
            "actor_id": str(OWNER_ID),
            "actor_role": "OWNER",
            "action": "REPOSITORY_CONNECTED",
            "resource_type": "github_connection",
            "resource_id": "conn-123",
            "project_id": str(PROJECT_ID),
            "outcome": "SUCCESS",
            "metadata": {
                "oauth_token": "ghp_secretvalue12345",
                "nested": {"authorization": "Bearer super.jwt.token"},
                "safe_note": "Used Bearer my-secret-tok and linked repo cleanly",
            },
        },
        headers={"X-Service-Key": INTERNAL_KEY},
    )
    assert res.status_code == 201
    created = res.json()
    assert created["metadata"]["oauth_token"] == "[REDACTED]"
    assert created["metadata"]["nested"]["authorization"] == "[REDACTED]"
    assert "ghp_secretvalue12345" not in res.text
    assert "my-secret-tok" not in res.text

    # Unrelated event for another actor
    await client.post(
        "/api/v1/audit/events",
        json={
            "service_name": "identity-service",
            "actor_id": str(OTHER_USER_ID),
            "actor_role": "FREELANCER",
            "action": "USER_LOGIN",
            "resource_type": "user",
        },
        headers={"X-Service-Key": INTERNAL_KEY},
    )

    # Admin sees global audit events
    admin_list = await client.get(
        "/api/v1/audit/events",
        headers={"Authorization": f"Bearer {ADMIN_TOKEN}"},
    )
    assert admin_list.status_code == 200
    assert admin_list.json()["total"] == 2

    # Non-admin sees ONLY own events on /events
    owner_list = await client.get(
        "/api/v1/audit/events",
        headers={"Authorization": f"Bearer {OWNER_TOKEN}"},
    )
    assert owner_list.json()["total"] == 1
    assert owner_list.json()["items"][0]["action"] == "REPOSITORY_CONNECTED"

    # Project-scoped audit log access: OWNER allowed, OTHER rejected 403
    assert (
        await client.get(
            f"/api/v1/audit/projects/{PROJECT_ID}",
            headers={"Authorization": f"Bearer {OWNER_TOKEN}"},
        )
    ).json()["total"] == 1
    assert (
        await client.get(
            f"/api/v1/audit/projects/{PROJECT_ID}",
            headers={"Authorization": f"Bearer {OTHER_TOKEN}"},
        )
    ).status_code == 403

    # Immutability check: no PUT/PATCH/DELETE allowed
    assert (
        await client.delete(
            f"/api/v1/audit/events/{created['id']}",
            headers={"Authorization": f"Bearer {ADMIN_TOKEN}"},
        )
    ).status_code in {404, 405}
