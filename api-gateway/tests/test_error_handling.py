"""Tests for centralized Gateway error handling, downstream errors, timeouts, and connect failures."""

from __future__ import annotations

import httpx
import pytest

from app.config import Settings
from app.main import create_app


@pytest.mark.asyncio
async def test_downstream_4xx_errors_preserve_status_and_standard_envelope() -> None:
    """Downstream 4xx responses preserve status codes and return the standard error format."""

    async def mock_handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/missing"):
            return httpx.Response(404, json={"detail": "Project not found"})
        return httpx.Response(
            422,
            json={
                "success": False,
                "error": {
                    "code": "INVALID_TASK_STATE",
                    "message": "Task cannot transition from closed.",
                },
            },
        )

    app = create_app(Settings(), http_transport=httpx.MockTransport(mock_handler))

    async with app.router.lifespan_context(app):
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
            not_found_resp = await client.get("/api/v1/projects/missing")
            invalid_resp = await client.post("/api/v1/projects/1/submit", json={})

    assert not_found_resp.status_code == 404
    nf_body = not_found_resp.json()
    assert nf_body["success"] is False
    assert nf_body["error"] == {"code": "NOT_FOUND", "message": "Project not found"}
    assert nf_body["request_id"] == not_found_resp.headers["X-Request-ID"]

    assert invalid_resp.status_code == 422
    inv_body = invalid_resp.json()
    assert inv_body["success"] is False
    assert inv_body["error"] == {
        "code": "INVALID_TASK_STATE",
        "message": "Task cannot transition from closed.",
    }
    assert inv_body["request_id"] == invalid_resp.headers["X-Request-ID"]


@pytest.mark.asyncio
async def test_downstream_5xx_errors_sanitized_without_exposing_traces_or_urls() -> None:
    """Downstream 500 errors never leak stack traces, DB secrets, or internal URLs."""

    async def mock_handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            500,
            json={
                "detail": "Traceback (most recent call last): psycopg2 failed at http://identity-service:8001/internal"
            },
        )

    app = create_app(Settings(), http_transport=httpx.MockTransport(mock_handler))

    async with app.router.lifespan_context(app):
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
            response = await client.get("/api/v1/auth/me")

    assert response.status_code == 502
    body = response.json()
    assert body["success"] is False
    assert body["error"]["code"] == "INTERNAL_SERVER_ERROR"
    assert "Traceback" not in body["error"]["message"]
    assert "psycopg2" not in body["error"]["message"]
    assert "8001" not in body["error"]["message"]
    assert body["request_id"] == response.headers["X-Request-ID"]


@pytest.mark.asyncio
async def test_downstream_timeout_returns_504_service_timeout() -> None:
    """httpx.TimeoutException maps to 504 SERVICE_TIMEOUT without exposing internal addresses."""

    async def mock_handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout("Timed out connecting to http://localhost:8004/api/v1/code-context", request=request)

    app = create_app(Settings(), http_transport=httpx.MockTransport(mock_handler))

    async with app.router.lifespan_context(app):
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
            response = await client.post("/api/v1/code-context/analyze", json={"repo": "org/repo"})

    assert response.status_code == 504
    body = response.json()
    assert body == {
        "success": False,
        "error": {
            "code": "SERVICE_TIMEOUT",
            "message": "The upstream service timed out while processing the request.",
        },
        "request_id": response.headers["X-Request-ID"],
    }
    assert "8004" not in body["error"]["message"]


@pytest.mark.asyncio
async def test_downstream_connection_failure_returns_503_service_unavailable() -> None:
    """httpx.ConnectError maps to 503 SERVICE_UNAVAILABLE without exposing internal hostnames."""

    async def mock_handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("Connection refused to http://localhost:8008", request=request)

    app = create_app(Settings(), http_transport=httpx.MockTransport(mock_handler))

    async with app.router.lifespan_context(app):
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
            response = await client.get("/api/v1/payment/balance")

    assert response.status_code == 503
    body = response.json()
    assert body == {
        "success": False,
        "error": {
            "code": "SERVICE_UNAVAILABLE",
            "message": "The requested service is currently unavailable.",
        },
        "request_id": response.headers["X-Request-ID"],
    }
    assert "8008" not in body["error"]["message"]


@pytest.mark.asyncio
async def test_unknown_gateway_route_returns_404_not_found_envelope() -> None:
    """Unknown public paths return standardized NOT_FOUND JSON with X-Request-ID."""
    app = create_app(Settings())

    async with app.router.lifespan_context(app):
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
            response = await client.get("/api/v1/unknown-domain/test")

    assert response.status_code == 404
    body = response.json()
    assert body == {
        "success": False,
        "error": {
            "code": "NOT_FOUND",
            "message": "The requested endpoint was not found.",
        },
        "request_id": response.headers["X-Request-ID"],
    }


@pytest.mark.asyncio
async def test_unexpected_internal_exception_sanitized_with_request_id() -> None:
    """Unexpected internal exceptions return 500 INTERNAL_SERVER_ERROR without leaking traces."""

    async def mock_handler(request: httpx.Request) -> httpx.Response:
        raise RuntimeError("sensitive internal traceback detail")

    app = create_app(Settings(_env_file=None), http_transport=httpx.MockTransport(mock_handler))

    async with app.router.lifespan_context(app):
        transport = httpx.ASGITransport(app=app, raise_app_exceptions=False)
        async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
            response = await client.get("/api/v1/audit/events")

    assert response.status_code == 500
    body = response.json()
    assert body["success"] is False
    assert body["error"]["code"] == "INTERNAL_SERVER_ERROR"
    assert "sensitive internal traceback detail" not in body["error"]["message"]
    assert body["request_id"] == response.headers["X-Request-ID"]

