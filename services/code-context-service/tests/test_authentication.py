"""Authentication, Identity Service integration, correlation IDs, & health checks."""

from __future__ import annotations

import uuid

import httpx
import pytest

from app.main import create_app
from tests.conftest import (
    INACTIVE_TOKEN,
    OWNER_TOKEN,
    PROJECT_ID,
    auth_headers,
    init_test_schema,
    make_github_service_transport,
    make_identity_transport,
    make_project_transport,
)


@pytest.mark.asyncio
async def test_health_and_readiness_endpoints(async_client: httpx.AsyncClient) -> None:
    health_resp = await async_client.get("/health")
    assert health_resp.status_code == 200
    assert health_resp.json()["status"] == "ok"
    assert health_resp.json()["service"] == "code-context-service"
    assert uuid.UUID(health_resp.headers["X-Request-ID"])

    ready_resp = await async_client.get("/ready")
    assert ready_resp.status_code == 200
    body = ready_resp.json()
    assert body["status"] == "ready"
    assert body["database"] == "ok"
    assert body["vector_store"] == "ok"
    assert body["identity_client"] == "ok"
    assert body["project_client"] == "ok"
    assert body["github_client"] == "ok"


@pytest.mark.asyncio
async def test_missing_or_invalid_token_returns_401(async_client: httpx.AsyncClient) -> None:
    resp_missing = await async_client.get(
        f"/api/v1/code-context/projects/{PROJECT_ID}/index/status"
    )
    assert resp_missing.status_code == 401
    assert resp_missing.json()["error"]["code"] == "UNAUTHORIZED"

    resp_invalid = await async_client.get(
        f"/api/v1/code-context/projects/{PROJECT_ID}/index/status",
        headers=auth_headers("bogus-jwt-token"),
    )
    assert resp_invalid.status_code == 401
    assert resp_invalid.json()["error"]["code"] == "UNAUTHORIZED"


@pytest.mark.asyncio
async def test_inactive_user_returns_403(async_client: httpx.AsyncClient) -> None:
    resp = await async_client.get(
        f"/api/v1/code-context/projects/{PROJECT_ID}/index/status",
        headers=auth_headers(INACTIVE_TOKEN),
    )
    assert resp.status_code == 403
    assert resp.json()["error"]["code"] == "FORBIDDEN"


@pytest.mark.asyncio
async def test_request_id_preserved_and_returned(async_client: httpx.AsyncClient) -> None:
    corr_id = "123e4567-e89b-12d3-a456-426614174000"
    resp = await async_client.get("/health", headers={"X-Request-ID": corr_id})
    assert resp.status_code == 200
    assert resp.headers["X-Request-ID"] == corr_id


@pytest.mark.asyncio
async def test_identity_service_unavailable_returns_503(base_test_settings) -> None:
    await init_test_schema(base_test_settings)
    app = create_app(
        settings=base_test_settings,
        identity_transport=make_identity_transport(force_status=502),
        project_transport=make_project_transport(),
        github_transport=make_github_service_transport(),
    )
    async with app.router.lifespan_context(app):
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app),
            base_url="http://code-context.test",
        ) as client:
            resp = await client.get(
                f"/api/v1/code-context/projects/{PROJECT_ID}/index/status",
                headers=auth_headers(OWNER_TOKEN),
            )
            assert resp.status_code == 503
            assert resp.json()["error"]["code"] == "IDENTITY_SERVICE_UNAVAILABLE"

