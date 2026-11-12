"""Extended tests for Admin Service: 401/403/404/409/422/503, report filters, & /ready failure."""

from __future__ import annotations

import json
import uuid
from dataclasses import dataclass, field
from pathlib import Path

import httpx
import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import create_async_engine

from app.clients.service_clients import IdentityAdminClient, PlatformInspectionClient
from app.core.config import Settings
from app.main import create_app
from app.models import Base

OWNER_ID = uuid.UUID("11111111-1111-4111-8111-111111111111")
FREELANCER_ID = uuid.UUID("33333333-3333-4333-8333-333333333333")
ADMIN_ID = uuid.UUID("55555555-5555-4555-8555-555555555555")
MISSING_USER_ID = uuid.UUID("99999999-9999-4999-8999-999999999999")

OWNER_TOKEN = "tok-owner"
ADMIN_TOKEN = "tok-admin"


@dataclass
class AdminTestState:
    fail_identity: bool = False
    fail_project: bool = False
    fail_audit_read: bool = False
    fail_audit_write: bool = False
    users_by_id: dict[uuid.UUID, dict] = field(default_factory=dict)


@pytest_asyncio.fixture
async def admin_harness(tmp_path: Path):
    db_file = tmp_path / "admin_ext.db"
    settings = Settings(APP_ENV="test", DATABASE_URL=f"sqlite+aiosqlite:///{db_file.as_posix()}")
    application = create_app(settings_override=settings)
    state = AdminTestState(
        users_by_id={
            OWNER_ID: {"id": str(OWNER_ID), "full_name": "Alice", "email": "alice@example.com", "role": "OWNER", "is_active": True},
            FREELANCER_ID: {"id": str(FREELANCER_ID), "full_name": "Charlie", "email": "charlie@example.com", "role": "FREELANCER", "is_active": True},
            ADMIN_ID: {"id": str(ADMIN_ID), "full_name": "Admin", "email": "admin@example.com", "role": "ADMIN", "is_active": True},
        }
    )
    tokens = {OWNER_TOKEN: state.users_by_id[OWNER_ID], ADMIN_TOKEN: state.users_by_id[ADMIN_ID]}

    def id_handler(req: httpx.Request) -> httpx.Response:
        tok = req.headers.get("authorization", "").removeprefix("Bearer ").strip()
        caller = tokens.get(tok)
        if req.url.path == "/api/v1/auth/me":
            return httpx.Response(200, json=caller) if caller else httpx.Response(401)
        if state.fail_identity:
            return httpx.Response(503, json={"detail": "identity down"})
        if req.url.path == "/api/v1/auth/admin/users":
            return httpx.Response(200, json={"items": list(state.users_by_id.values()), "total": len(state.users_by_id), "page": 1, "page_size": 20})
        if req.method == "PATCH" and req.url.path.endswith("/status"):
            uid = uuid.UUID(req.url.path.split("/")[-2])
            if uid not in state.users_by_id:
                return httpx.Response(404)
            body = json.loads(req.content.decode("utf-8"))
            state.users_by_id[uid]["is_active"] = bool(body["is_active"])
            return httpx.Response(200, json=state.users_by_id[uid])
        return httpx.Response(404)

    def proj_handler(_req: httpx.Request) -> httpx.Response:
        if state.fail_project:
            raise httpx.ConnectError("project down")
        return httpx.Response(200, json={"items": [{"id": str(uuid.uuid4()), "title": "P1"}], "total": 1})

    def audit_handler(req: httpx.Request) -> httpx.Response:
        if req.method == "POST":
            if state.fail_audit_write:
                raise httpx.ConnectError("audit write down")
            return httpx.Response(201, json={"ok": True})
        if state.fail_audit_read:
            return httpx.Response(503, json={"detail": "audit read down"})
        return httpx.Response(200, json={"total": 2, "items": [{"action": "A"}, {"action": "B"}]})

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
        project_transport=httpx.MockTransport(proj_handler),
        audit_transport=httpx.MockTransport(audit_handler),
    )

    async with application.state.engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    async with AsyncClient(transport=ASGITransport(app=application), base_url="http://testserver") as client:
        yield client, application, state
    await application.state.engine.dispose()


@pytest.mark.asyncio
async def test_admin_unauthenticated_and_validation_errors(admin_harness) -> None:
    client, _, _ = admin_harness
    assert (await client.get("/api/v1/admin/users")).status_code == 401
    assert (await client.get("/api/v1/admin/reports")).status_code == 401

    bad_report = await client.post(
        "/api/v1/admin/reports",
        json={"target_type": "UNKNOWN_ENTITY", "target_id": str(OWNER_ID), "reason": "bad", "severity": "LOW"},
        headers={"Authorization": f"Bearer {ADMIN_TOKEN}"},
    )
    assert bad_report.status_code == 422

    bad_suspend = await client.post(
        f"/api/v1/admin/users/{FREELANCER_ID}/suspend",
        json={"reason": ""},
        headers={"Authorization": f"Bearer {ADMIN_TOKEN}"},
    )
    assert bad_suspend.status_code == 422


@pytest.mark.asyncio
async def test_admin_404_409_double_resolve_and_report_filtering(admin_harness) -> None:
    client, _, _ = admin_harness
    h = {"Authorization": f"Bearer {ADMIN_TOKEN}"}

    # Suspending unknown user -> 404 from Identity upstream mapping
    missing_user = await client.post(
        f"/api/v1/admin/users/{MISSING_USER_ID}/suspend",
        json={"reason": "Non-existent account check."},
        headers=h,
    )
    assert missing_user.status_code == 404

    # Resolving missing report -> 404
    missing_rep = await client.post(
        f"/api/v1/admin/reports/{uuid.uuid4()}/resolve",
        json={"status": "RESOLVED", "resolution_notes": "None."},
        headers=h,
    )
    assert missing_rep.status_code == 404

    # Create two reports; resolve one -> double resolve rejected 409; filter OPEN vs RESOLVED
    r1 = (
        await client.post(
            "/api/v1/admin/reports",
            json={"target_type": "USER", "target_id": str(FREELANCER_ID), "reason": "Spam comments", "severity": "LOW"},
            headers=h,
        )
    ).json()
    await client.post(
        "/api/v1/admin/reports",
        json={"target_type": "PROJECT", "target_id": str(uuid.uuid4()), "reason": "Unverified scope", "severity": "MEDIUM"},
        headers=h,
    )

    assert (
        await client.post(
            f"/api/v1/admin/reports/{r1['id']}/resolve",
            json={"status": "DISMISSED", "resolution_notes": "False alarm."},
            headers=h,
        )
    ).status_code == 200

    # Double resolution strictly rejected (409)
    assert (
        await client.post(
            f"/api/v1/admin/reports/{r1['id']}/resolve",
            json={"status": "RESOLVED", "resolution_notes": "Try again."},
            headers=h,
        )
    ).status_code == 409

    open_reports = (await client.get("/api/v1/admin/reports?status=OPEN", headers=h)).json()
    dismissed_reports = (await client.get("/api/v1/admin/reports?status=DISMISSED", headers=h)).json()
    assert len(open_reports) == 1
    assert len(dismissed_reports) == 1


@pytest.mark.asyncio
async def test_admin_upstream_503_fail_soft_audit_and_ready_503(admin_harness) -> None:
    client, application, state = admin_harness
    h = {"Authorization": f"Bearer {ADMIN_TOKEN}"}

    # Fail-soft audit write: suspension still succeeds when Audit Service fails
    state.fail_audit_write = True
    susp = await client.post(
        f"/api/v1/admin/users/{FREELANCER_ID}/suspend",
        json={"reason": "Testing fail-soft audit."},
        headers=h,
    )
    assert susp.status_code == 200
    assert susp.json()["is_active"] is False

    # Hard upstream outages on inspection / user management return 503
    state.fail_identity = True
    assert (await client.get("/api/v1/admin/users", headers=h)).status_code == 503
    state.fail_identity = False

    state.fail_project = True
    assert (await client.get("/api/v1/admin/projects", headers=h)).status_code == 503
    state.fail_project = False

    state.fail_audit_read = True
    assert (await client.get("/api/v1/admin/audit-logs", headers=h)).status_code == 503

    # /ready returns 503 when DB engine cannot connect
    orig_engine = application.state.engine
    broken_engine = create_async_engine("postgresql+asyncpg://bad:bad@127.0.0.1:1/bad")
    application.state.engine = broken_engine
    try:
        ready = await client.get("/ready")
        assert ready.status_code == 503
        assert ready.json()["status"] == "not_ready"
    finally:
        application.state.engine = orig_engine
        await broken_engine.dispose()

