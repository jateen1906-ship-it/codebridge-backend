"""Extended tests for Observability Service: filters, 401/403/422, latency percentiles, & /ready 503."""

from __future__ import annotations

import uuid
from pathlib import Path

import httpx
import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.clients.service_clients import IdentityClient, ServiceProbeClient
from app.core.config import Settings
from app.main import create_app
from app.models import Base

OWNER_ID = uuid.UUID("11111111-1111-4111-8111-111111111111")
ADMIN_ID = uuid.UUID("55555555-5555-4555-8555-555555555555")
OWNER_TOKEN = "tok-owner"
ADMIN_TOKEN = "tok-admin"


@pytest_asyncio.fixture
async def obs_harness(tmp_path: Path):
    db_file = tmp_path / "obs_ext.db"
    settings = Settings(APP_ENV="test", DATABASE_URL=f"sqlite+aiosqlite:///{db_file.as_posix()}")
    application = create_app(settings_override=settings)
    users = {
        OWNER_TOKEN: {"id": str(OWNER_ID), "full_name": "Alice", "email": "alice@example.com", "role": "OWNER", "is_active": True},
        ADMIN_TOKEN: {"id": str(ADMIN_ID), "full_name": "Admin", "email": "admin@example.com", "role": "ADMIN", "is_active": True},
    }
    application.state.identity_client = IdentityClient(
        base_url="http://id.local",
        timeout_seconds=5.0,
        transport=httpx.MockTransport(
            lambda req: httpx.Response(200, json=users[req.headers.get("authorization", "").removeprefix("Bearer ").strip()])
            if req.headers.get("authorization", "").removeprefix("Bearer ").strip() in users
            else httpx.Response(401)
        ),
    )

    # Simulate all 13 services healthy including explicit /ready checks
    def ok_handler(_req: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"status": "ready"})

    def not_ready_handler(req: httpx.Request) -> httpx.Response:
        if req.url.path == "/ready":
            return httpx.Response(404, json={"detail": "Not Found"})
        return httpx.Response(200, json={"status": "healthy"})

    peers = (
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
    transport_map = {
        svc: httpx.MockTransport(not_ready_handler if svc == "identity-service" else ok_handler)
        for svc in peers
    }
    application.state.probe_client = ServiceProbeClient(settings=settings, transport_map=transport_map)

    async with application.state.engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    async with AsyncClient(transport=ASGITransport(app=application), base_url="http://testserver") as client:
        yield client, application, settings.INTERNAL_SERVICE_KEY
    await application.state.engine.dispose()


@pytest.mark.asyncio
async def test_observability_ready_strictness_filtering_validation_and_503(obs_harness) -> None:
    client, application, internal_key = obs_harness

    # Unauthenticated rejected 401; non-admin rejected 403
    assert (await client.get("/api/v1/observability/services")).status_code == 401
    assert (await client.get("/api/v1/observability/errors", headers={"Authorization": f"Bearer {OWNER_TOKEN}"})).status_code == 403

    # Missing /ready (404) is NOT masked as healthy/ready -> must mark service unhealthy & overall degraded
    svc_status = (await client.get("/api/v1/observability/services", headers={"Authorization": f"Bearer {ADMIN_TOKEN}"})).json()
    assert svc_status["total_services"] == 13
    assert svc_status["ready_count"] == 12
    assert svc_status["overall_status"] == "degraded"
    id_entry = next(s for s in svc_status["services"] if s["service_name"] == "identity-service")
    assert id_entry["ready_status"] == "not_ready"
    assert id_entry["ready_http_status"] == 404

    # Invalid telemetry payload -> 422
    bad_tel = await client.post(
        "/api/v1/observability/telemetry",
        headers={"X-Service-Key": internal_key},
        json={"service_name": "gateway", "route_path": "/x", "method": "GET", "status_code": 999, "duration_ms": -5},
    )
    assert bad_tel.status_code == 422

    # Ingest multi-service metrics and filter by service_name
    for idx, (svc, code, dur) in enumerate([
        ("project-service", 200, 10.0),
        ("project-service", 200, 30.0),
        ("project-service", 500, 50.0),
        ("workspace-service", 502, 80.0),
    ]):
        res = await client.post(
            "/api/v1/observability/telemetry",
            headers={"X-Service-Key": internal_key},
            json={
                "service_name": svc,
                "route_path": f"/route/{idx}",
                "method": "GET",
                "status_code": code,
                "duration_ms": dur,
                "error_summary": "Downstream failure" if code >= 500 else None,
            },
        )
        assert res.status_code == 201

    proj_metrics = (await client.get("/api/v1/observability/metrics?service_name=project-service", headers={"Authorization": f"Bearer {ADMIN_TOKEN}"})).json()
    assert proj_metrics["total_requests"] == 3
    assert proj_metrics["total_errors"] == 1

    ws_errors = (await client.get("/api/v1/observability/errors?service_name=workspace-service", headers={"Authorization": f"Bearer {ADMIN_TOKEN}"})).json()
    assert ws_errors["total"] == 1
    assert ws_errors["items"][0]["service_name"] == "workspace-service"

    # /ready 503 when DB unreachable
    orig_factory = application.state.session_factory
    broken_engine = create_async_engine("postgresql+asyncpg://bad:bad@127.0.0.1:1/bad")
    application.state.session_factory = async_sessionmaker(broken_engine, expire_on_commit=False)
    try:
        assert (await client.get("/ready")).status_code == 503
    finally:
        application.state.session_factory = orig_factory
        await broken_engine.dispose()

