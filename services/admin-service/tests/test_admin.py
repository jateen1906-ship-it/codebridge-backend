"""Pytest integration tests for Admin Service strict RBAC, user suspension/restoration, and moderation."""

from __future__ import annotations

import uuid
from pathlib import Path

import httpx
import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient

from app.clients.service_clients import IdentityAdminClient, PlatformInspectionClient
from app.core.config import Settings
from app.main import create_app
from app.models import Base

OWNER_ID = uuid.UUID("11111111-1111-4111-8111-111111111111")
FREELANCER_ID = uuid.UUID("33333333-3333-4333-8333-333333333333")
ADMIN_ID = uuid.UUID("55555555-5555-4555-8555-555555555555")

OWNER_TOKEN = "tok-owner"
FREELANCER_TOKEN = "tok-freelancer"
ADMIN_TOKEN = "tok-admin"


@pytest_asyncio.fixture
async def client(tmp_path: Path):
    db_file = tmp_path / "admin_test.db"
    settings = Settings(
        APP_ENV="test",
        DATABASE_URL=f"sqlite+aiosqlite:///{db_file.as_posix()}",
    )
    application = create_app(settings_override=settings)
    users_by_token = {
        OWNER_TOKEN: {"id": str(OWNER_ID), "full_name": "Alice", "email": "alice@example.com", "role": "OWNER", "is_active": True},
        FREELANCER_TOKEN: {"id": str(FREELANCER_ID), "full_name": "Charlie", "email": "charlie@example.com", "role": "FREELANCER", "is_active": True},
        ADMIN_TOKEN: {"id": str(ADMIN_ID), "full_name": "Admin", "email": "admin@example.com", "role": "ADMIN", "is_active": True},
    }
    users_by_id = {OWNER_ID: users_by_token[OWNER_TOKEN], FREELANCER_ID: users_by_token[FREELANCER_TOKEN], ADMIN_ID: users_by_token[ADMIN_TOKEN]}

    import json

    def id_handler(req: httpx.Request) -> httpx.Response:
        tok = req.headers.get("authorization", "").removeprefix("Bearer ").strip()
        caller = users_by_token.get(tok)
        if caller is None:
            return httpx.Response(401)
        if req.url.path == "/api/v1/auth/me":
            return httpx.Response(200, json=caller)
        if req.url.path == "/api/v1/auth/admin/users":
            return httpx.Response(200, json={"items": list(users_by_id.values()), "total": 3, "page": 1, "page_size": 20})
        if req.method == "PATCH" and req.url.path.endswith("/status"):
            uid = uuid.UUID(req.url.path.split("/")[-2])
            if uid not in users_by_id:
                return httpx.Response(404)
            body = json.loads(req.content.decode("utf-8"))
            users_by_id[uid]["is_active"] = bool(body["is_active"])
            return httpx.Response(200, json=users_by_id[uid])
        return httpx.Response(404)

    application.state.identity_client = IdentityAdminClient(
        base_url="http://id.local",
        timeout_seconds=5.0,
        transport=httpx.MockTransport(id_handler),
    )
    application.state.inspection_client = PlatformInspectionClient(
        project_url="http://proj.local",
        audit_url="http://audit.local",
        notification_url="http://notif.local",
        internal_service_key=settings.INTERNAL_SERVICE_KEY,
        timeout_seconds=5.0,
        project_transport=httpx.MockTransport(lambda _r: httpx.Response(200, json={"items": [{"id": str(uuid.uuid4()), "title": "P1"}], "total": 1})),
        audit_transport=httpx.MockTransport(lambda req: httpx.Response(201, json={"ok": True}) if req.method == "POST" else httpx.Response(200, json={"total": 1, "items": [{"action": "LOGIN"}]})),
    )

    async with application.state.engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    async with AsyncClient(transport=ASGITransport(app=application), base_url="http://testserver") as c:
        yield c
    await application.state.engine.dispose()


@pytest.mark.asyncio
async def test_admin_rbac_user_suspension_reports_and_cross_service_inspection(client: AsyncClient) -> None:
    assert (await client.get("/health")).status_code == 200
    assert (await client.get("/ready")).status_code == 200

    # Strict ADMIN-only check: OWNER and FREELANCER receive 403 across all admin endpoints
    for tok in (OWNER_TOKEN, FREELANCER_TOKEN):
        assert (await client.get("/api/v1/admin/users", headers={"Authorization": f"Bearer {tok}"})).status_code == 403
        assert (await client.get("/api/v1/admin/projects", headers={"Authorization": f"Bearer {tok}"})).status_code == 403
        assert (await client.get("/api/v1/admin/audit-logs", headers={"Authorization": f"Bearer {tok}"})).status_code == 403
        assert (
            await client.post(
                f"/api/v1/admin/users/{FREELANCER_ID}/suspend",
                json={"reason": "Unauthorized attempt"},
                headers={"Authorization": f"Bearer {tok}"},
            )
        ).status_code == 403

    # Admin cannot self-suspend
    assert (
        await client.post(
            f"/api/v1/admin/users/{ADMIN_ID}/suspend",
            json={"reason": "Self lock test"},
            headers={"Authorization": f"Bearer {ADMIN_TOKEN}"},
        )
    ).status_code == 409

    # Admin suspends & restores target freelancer
    susp = await client.post(
        f"/api/v1/admin/users/{FREELANCER_ID}/suspend",
        json={"reason": "Violation of repository NDA policy."},
        headers={"Authorization": f"Bearer {ADMIN_TOKEN}"},
    )
    assert susp.status_code == 200
    assert susp.json()["is_active"] is False

    rest = await client.post(
        f"/api/v1/admin/users/{FREELANCER_ID}/restore",
        json={"reason": "Appeal approved after manual review."},
        headers={"Authorization": f"Bearer {ADMIN_TOKEN}"},
    )
    assert rest.status_code == 200
    assert rest.json()["is_active"] is True

    # Moderation report create & resolve
    rep = await client.post(
        "/api/v1/admin/reports",
        json={
            "target_type": "USER",
            "target_id": str(FREELANCER_ID),
            "reason": "Suspicious large archive upload",
            "severity": "HIGH",
        },
        headers={"Authorization": f"Bearer {ADMIN_TOKEN}"},
    )
    assert rep.status_code == 201
    rep_id = rep.json()["id"]

    resolved = await client.post(
        f"/api/v1/admin/reports/{rep_id}/resolve",
        json={"status": "RESOLVED", "resolution_notes": "Verified benign test fixture."},
        headers={"Authorization": f"Bearer {ADMIN_TOKEN}"},
    )
    assert resolved.status_code == 200
    assert resolved.json()["status"] == "RESOLVED"

    # Inspection routes & action history
    assert (await client.get("/api/v1/admin/users", headers={"Authorization": f"Bearer {ADMIN_TOKEN}"})).json()["total"] == 3
    assert (await client.get("/api/v1/admin/projects", headers={"Authorization": f"Bearer {ADMIN_TOKEN}"})).json()["total"] == 1
    assert (await client.get("/api/v1/admin/audit-logs", headers={"Authorization": f"Bearer {ADMIN_TOKEN}"})).json()["total"] == 1
    actions = await client.get("/api/v1/admin/actions", headers={"Authorization": f"Bearer {ADMIN_TOKEN}"})
    assert len(actions.json()) == 4
