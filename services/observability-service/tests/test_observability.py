"""Pytest tests for Observability Service health aggregation, RBAC, telemetry ingestion & scrubbing."""

from __future__ import annotations

import uuid
from pathlib import Path

import httpx
import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient

from app.clients.service_clients import IdentityClient, ServiceProbeClient
from app.core.config import Settings
from app.main import create_app
from app.models import Base

OWNER_ID = uuid.UUID("11111111-1111-4111-8111-111111111111")
ADMIN_ID = uuid.UUID("55555555-5555-4555-8555-555555555555")
OWNER_TOKEN = "tok-owner"
ADMIN_TOKEN = "tok-admin"


@pytest_asyncio.fixture
async def client(tmp_path: Path):
    db_file = tmp_path / "obs_test.db"
    settings = Settings(
        APP_ENV="test",
        DATABASE_URL=f"sqlite+aiosqlite:///{db_file.as_posix()}",
    )
    application = create_app(settings_override=settings)
    users = {
        OWNER_TOKEN: {"id": str(OWNER_ID), "full_name": "Alice", "email": "alice@example.com", "role": "OWNER", "is_active": True},
        ADMIN_TOKEN: {"id": str(ADMIN_ID), "full_name": "Admin", "email": "admin@example.com", "role": "ADMIN", "is_active": True},
    }

    def id_handler(req: httpx.Request) -> httpx.Response:
        tok = req.headers.get("authorization", "").removeprefix("Bearer ").strip()
        caller = users.get(tok)
        return httpx.Response(200, json=caller) if caller else httpx.Response(401)

    application.state.identity_client = IdentityClient(
        base_url="http://id.local",
        timeout_seconds=5.0,
        transport=httpx.MockTransport(id_handler),
    )

    def healthy_handler(req: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"status": "ok"})

    def failing_handler(req: httpx.Request) -> httpx.Response:
        if req.url.path == "/ready":
            return httpx.Response(503, json={"status": "not_ready"})
        raise httpx.ConnectError("connection refused", request=req)

    # Simulate 11 healthy peers + 1 unreachable service (audit-service) -> degraded overall
    transport_map = {
        svc: httpx.MockTransport(failing_handler if svc == "audit-service" else healthy_handler)
        for svc in (
            "api-gateway",
            "identity-service",
            "project-service",
            "github-service",
            "code-context-service",
            "workspace-service",
            "review-service",
            "verification-service",
            "payment-service",
            "notification-service",
            "admin-service",
            "audit-service",
        )
    }
    application.state.probe_client = ServiceProbeClient(settings=settings, transport_map=transport_map)

    async with application.state.engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    async with AsyncClient(transport=ASGITransport(app=application), base_url="http://testserver") as c:
        yield c, settings.INTERNAL_SERVICE_KEY
    await application.state.engine.dispose()


@pytest.mark.asyncio
async def test_observability_probes_metrics_errors_and_secret_scrubbing(client) -> None:
    http, internal_key = client
    assert (await http.get("/health")).status_code == 200
    assert (await http.get("/ready")).status_code == 200

    # Non-admin blocked
    assert (await http.get("/api/v1/observability/services", headers={"Authorization": f"Bearer {OWNER_TOKEN}"})).status_code == 403
    assert (await http.get("/api/v1/observability/metrics", headers={"Authorization": f"Bearer {OWNER_TOKEN}"})).status_code == 403

    # Platform service aggregation reports 13 services (gateway + 12 microservices), degraded due to audit-service down
    status_resp = await http.get("/api/v1/observability/services", headers={"Authorization": f"Bearer {ADMIN_TOKEN}"})
    assert status_resp.status_code == 200
    body = status_resp.json()
    assert body["total_services"] == 13
    assert body["healthy_count"] == 12
    assert body["unreachable_count"] == 1
    assert body["overall_status"] == "degraded"

    # Unauthenticated telemetry rejected
    assert (
        await http.post(
            "/api/v1/observability/telemetry",
            json={
                "service_name": "payment-service",
                "route_path": "/api/v1/payment/release",
                "method": "POST",
                "status_code": 200,
                "duration_ms": 42.5,
            },
        )
    ).status_code == 401

    # Valid internal telemetry ingestion (success + error with secret)
    r1 = await http.post(
        "/api/v1/observability/telemetry",
        headers={"X-Service-Key": internal_key},
        json={
            "service_name": "payment-service",
            "route_path": "/api/v1/payment/escrow",
            "method": "POST",
            "status_code": 201,
            "duration_ms": 20.0,
        },
    )
    assert r1.status_code == 201

    r2 = await http.post(
        "/api/v1/observability/telemetry",
        headers={"X-Service-Key": internal_key},
        json={
            "service_name": "payment-service",
            "route_path": "/api/v1/payment/release",
            "method": "POST",
            "status_code": 502,
            "duration_ms": 120.0,
            "error_code": "UPSTREAM_ERROR",
            "error_summary": "Failed with Bearer secret-jwt-123456 and db=postgresql+asyncpg://u:pw@host/db",
        },
    )
    assert r2.status_code == 201
    assert "secret-jwt-123456" not in r2.json()["error_summary"]
    assert "u:pw@" not in r2.json()["error_summary"]
    assert "[REDACTED]" in r2.json()["error_summary"]

    metrics = await http.get("/api/v1/observability/metrics", headers={"Authorization": f"Bearer {ADMIN_TOKEN}"})
    assert metrics.status_code == 200
    m = metrics.json()
    assert m["total_requests"] == 2
    assert m["total_errors"] == 1
    assert m["services"][0]["p95_latency_ms"] == 120.0

    errors = await http.get("/api/v1/observability/errors", headers={"Authorization": f"Bearer {ADMIN_TOKEN}"})
    assert errors.status_code == 200
    assert errors.json()["total"] == 1
