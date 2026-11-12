"""Extended tests for Audit Service: filters, 401/403/503 upstream failures, pagination, & /ready 503."""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from pathlib import Path

import httpx
import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import create_async_engine

from app.clients.service_clients import IdentityClient, ProjectClient
from app.core.config import Settings
from app.main import create_app
from app.models import Base

OWNER_ID = uuid.UUID("11111111-1111-4111-8111-111111111111")
FREELANCER_ID = uuid.UUID("33333333-3333-4333-8333-333333333333")
ADMIN_ID = uuid.UUID("55555555-5555-4555-8555-555555555555")
PROJECT_ID = uuid.UUID("aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa")

OWNER_TOKEN = "tok-owner"
FREELANCER_TOKEN = "tok-freelancer"
ADMIN_TOKEN = "tok-admin"
INTERNAL_KEY = "test-internal-audit-key"


@dataclass
class AuditHarnessState:
    fail_project_upstream: bool = False


@pytest_asyncio.fixture
async def audit_harness(tmp_path: Path):
    db_file = tmp_path / "audit_ext.db"
    settings = Settings(
        APP_ENV="test",
        DATABASE_URL=f"sqlite+aiosqlite:///{db_file.as_posix()}",
        INTERNAL_SERVICE_KEY=INTERNAL_KEY,
    )
    application = create_app(settings_override=settings)
    state = AuditHarnessState()
    users = {
        OWNER_TOKEN: {"id": str(OWNER_ID), "full_name": "Alice", "email": "a@example.com", "role": "OWNER"},
        FREELANCER_TOKEN: {"id": str(FREELANCER_ID), "full_name": "Charlie", "email": "c@example.com", "role": "FREELANCER"},
        ADMIN_TOKEN: {"id": str(ADMIN_ID), "full_name": "Admin", "email": "admin@example.com", "role": "ADMIN"},
    }

    def id_handler(req: httpx.Request) -> httpx.Response:
        tok = req.headers.get("authorization", "").removeprefix("Bearer ").strip()
        user = users.get(tok)
        return httpx.Response(200, json=user) if user else httpx.Response(401)

    def proj_handler(req: httpx.Request) -> httpx.Response:
        if state.fail_project_upstream:
            raise httpx.ConnectError("project unavailable", request=req)
        tok = req.headers.get("authorization", "").removeprefix("Bearer ").strip()
        if tok in {OWNER_TOKEN, ADMIN_TOKEN}:
            return httpx.Response(200, json={"id": str(PROJECT_ID), "owner_id": str(OWNER_ID)})
        return httpx.Response(403)

    application.state.identity_client = IdentityClient(
        base_url="http://id.local",
        timeout_seconds=5.0,
        transport=httpx.MockTransport(id_handler),
    )
    application.state.project_client = ProjectClient(
        base_url="http://proj.local",
        timeout_seconds=5.0,
        transport=httpx.MockTransport(proj_handler),
    )
    async with application.state.engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    async with AsyncClient(transport=ASGITransport(app=application), base_url="http://testserver") as client:
        yield client, application, state
    await application.state.engine.dispose()


@pytest.mark.asyncio
async def test_audit_auth_filters_pagination_and_immutability(audit_harness) -> None:
    client, application, state = audit_harness

    # Unauthenticated access rejected
    assert (await client.get("/api/v1/audit/events")).status_code == 401
    assert (await client.post("/api/v1/audit/events", json={"service_name": "s", "action": "A", "resource_type": "r"})).status_code == 401
    assert (
        await client.post(
            "/api/v1/audit/events",
            json={"service_name": "s", "action": "A", "resource_type": "r"},
            headers={"X-Service-Key": "wrong-key"},
        )
    ).status_code == 403

    # Seed multiple events across services & outcomes
    events = [
        {"service_name": "payment-service", "actor_id": str(OWNER_ID), "action": "ESCROW_HELD", "resource_type": "escrow", "project_id": str(PROJECT_ID), "outcome": "SUCCESS"},
        {"service_name": "payment-service", "actor_id": str(OWNER_ID), "action": "ESCROW_RELEASED", "resource_type": "escrow", "project_id": str(PROJECT_ID), "outcome": "SUCCESS"},
        {"service_name": "review-service", "actor_id": str(FREELANCER_ID), "action": "SUBMISSION_CREATED", "resource_type": "submission", "project_id": str(PROJECT_ID), "outcome": "SUCCESS"},
        {"service_name": "workspace-service", "actor_id": str(FREELANCER_ID), "action": "TEST_RUN_FAILED", "resource_type": "workspace", "outcome": "ERROR"},
    ]
    created_ids: list[str] = []
    for ev in events:
        resp = await client.post("/api/v1/audit/events", json=ev, headers={"X-Service-Key": INTERNAL_KEY})
        assert resp.status_code == 201
        created_ids.append(resp.json()["id"])

    # Admin filtering by service_name, action, outcome, and pagination
    pay_events = (await client.get("/api/v1/audit/events?service_name=payment-service", headers={"Authorization": f"Bearer {ADMIN_TOKEN}"})).json()
    assert pay_events["total"] == 2

    fail_events = (await client.get("/api/v1/audit/events?outcome=ERROR", headers={"Authorization": f"Bearer {ADMIN_TOKEN}"})).json()
    assert fail_events["total"] == 1
    assert fail_events["items"][0]["action"] == "TEST_RUN_FAILED"

    paged = (await client.get("/api/v1/audit/events?limit=2&offset=1", headers={"Authorization": f"Bearer {ADMIN_TOKEN}"})).json()
    assert paged["total"] == 4
    assert len(paged["items"]) == 2

    # Non-admin spoofing actor_id filter still restricted to their own actor_id
    spoof = (
        await client.get(
            f"/api/v1/audit/events?actor_id={OWNER_ID}",
            headers={"Authorization": f"Bearer {FREELANCER_TOKEN}"},
        )
    ).json()
    assert all(item["actor_id"] == str(FREELANCER_ID) for item in spoof["items"])

    # Put / Patch not allowed on audit records
    for method in ("PUT", "PATCH", "DELETE"):
        r = await client.request(method, f"/api/v1/audit/events/{created_ids[0]}", headers={"Authorization": f"Bearer {ADMIN_TOKEN}"})
        assert r.status_code in {404, 405}

    # Upstream project outage -> 503 on project-scoped query for non-admin
    state.fail_project_upstream = True
    assert (
        await client.get(
            f"/api/v1/audit/projects/{PROJECT_ID}",
            headers={"Authorization": f"Bearer {OWNER_TOKEN}"},
        )
    ).status_code == 503
    state.fail_project_upstream = False

    # /ready 503 check on broken DB
    orig_engine = application.state.engine
    broken_engine = create_async_engine("postgresql+asyncpg://bad:bad@127.0.0.1:1/bad")
    application.state.engine = broken_engine
    try:
        assert (await client.get("/ready")).status_code == 503
    finally:
        application.state.engine = orig_engine
        await broken_engine.dispose()

