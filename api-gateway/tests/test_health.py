"""Tests for Gateway startup, /health endpoint, configuration, CORS, and HTTP client lifecycle."""

from __future__ import annotations

import httpx
import pytest
from pydantic import ValidationError

from app.clients.http_client import GatewayHTTPClient
from app.clients.service_registry import RouteRule, ServiceRegistry
from app.config import Settings
from app.core.exceptions import ServiceConfigurationError
from app.main import create_app


@pytest.mark.asyncio
async def test_gateway_startup_and_health_endpoint_with_services_offline() -> None:
    """Gateway starts and /health returns healthy even when all 12 microservices are offline."""
    app = create_app(Settings())

    assert not app.state.http_client.is_open

    async with app.router.lifespan_context(app):
        assert app.state.http_client.is_open
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
            response = await client.get("/health")

    assert response.status_code == 200
    assert response.json() == {
        "status": "healthy",
        "service": "api-gateway",
        "version": "1.0.0",
    }
    assert "X-Request-ID" in response.headers
    assert not app.state.http_client.is_open


@pytest.mark.asyncio
async def test_http_client_lifecycle_reuses_single_instance() -> None:
    """Gateway initializes one reusable AsyncClient on startup and closes it on shutdown."""
    seen_client_ids: list[int] = []

    async def mock_handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"ok": True})

    app = create_app(Settings(), http_transport=httpx.MockTransport(mock_handler))

    async with app.router.lifespan_context(app):
        http_client: GatewayHTTPClient = app.state.http_client
        assert http_client.is_open
        initial_client_obj_id = id(http_client._client)

        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
            res1 = await client.get("/api/v1/projects")
            seen_client_ids.append(id(http_client._client))
            res2 = await client.get("/api/v1/auth/me")
            seen_client_ids.append(id(http_client._client))

        assert res1.status_code == 200
        assert res2.status_code == 200
        assert seen_client_ids == [initial_client_obj_id, initial_client_obj_id]

    assert not app.state.http_client.is_open


@pytest.mark.asyncio
async def test_cors_behavior_allowed_and_disallowed_origins() -> None:
    """CORS allows configured origins and headers while withholding headers from unknown origins."""
    settings = Settings(
        CORS_ORIGINS="https://app.codebridge.local,http://localhost:3000",
        CORS_ALLOW_CREDENTIALS=False,
    )
    app = create_app(settings)

    async with app.router.lifespan_context(app):
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
            allowed_preflight = await client.options(
                "/api/v1/projects",
                headers={
                    "Origin": "https://app.codebridge.local",
                    "Access-Control-Request-Method": "POST",
                    "Access-Control-Request-Headers": "Authorization,X-Request-ID,Content-Type",
                },
            )
            disallowed_get = await client.get(
                "/health",
                headers={"Origin": "https://evil.example.com"},
            )

    assert allowed_preflight.status_code == 200
    assert allowed_preflight.headers.get("access-control-allow-origin") == "https://app.codebridge.local"
    allow_headers = allowed_preflight.headers.get("access-control-allow-headers", "").lower()
    assert "authorization" in allow_headers
    assert "x-request-id" in allow_headers
    assert "access-control-allow-origin" not in disallowed_get.headers


def test_cors_wildcard_with_credentials_rejected() -> None:
    """Settings reject wildcard CORS origin combined with allow_credentials=True."""
    with pytest.raises(ValidationError):
        Settings(CORS_ORIGINS="*", CORS_ALLOW_CREDENTIALS=True)


def test_missing_or_invalid_service_configuration_rejected() -> None:
    """Empty or malformed service URLs fail clearly during Settings startup validation."""
    with pytest.raises(ValidationError):
        Settings(IDENTITY_SERVICE_URL="")

    with pytest.raises(ValidationError):
        Settings(PROJECT_SERVICE_URL="ftp://invalid-scheme:8002")

    with pytest.raises(ValidationError):
        Settings(GITHUB_SERVICE_URL="http://user:secret@localhost:8003")


@pytest.mark.asyncio
async def test_missing_service_in_registry_returns_service_unavailable() -> None:
    """Requests targeting a service missing from the registry return 503 SERVICE_UNAVAILABLE."""
    app = create_app(Settings())

    async with app.router.lifespan_context(app):
        # Simulate missing service entry in registry while route exists.
        app.state.service_registry = ServiceRegistry(
            services={},
            routes=[
                RouteRule(
                    service_name="identity-service",
                    public_prefix="/auth",
                    internal_target_path="/api/v1/auth",
                )
            ],
        )
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
            response = await client.get("/api/v1/auth/me")

    assert response.status_code == 503
    body = response.json()
    assert body["success"] is False
    assert body["error"]["code"] == "SERVICE_UNAVAILABLE"
    assert "request_id" in body

    with pytest.raises(ServiceConfigurationError):
        app.state.service_registry.get_service("identity-service")


@pytest.mark.asyncio
async def test_gateway_ready_returns_200_when_open_and_503_when_closed() -> None:
    """Gateway /ready returns 200 when HTTP pool is open and 503 when closed."""
    app = create_app(Settings())

    async with app.router.lifespan_context(app):
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
            ok_resp = await client.get("/ready")
            assert ok_resp.status_code == 200
            assert ok_resp.json()["status"] == "ready"

            await app.state.http_client.shutdown()
            fail_resp = await client.get("/ready")
            assert fail_resp.status_code == 503
            assert fail_resp.json()["status"] == "not_ready"

