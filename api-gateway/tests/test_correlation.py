"""Tests for X-Request-ID correlation middleware, validation, downstream forwarding, and logs."""

from __future__ import annotations

import json
import logging
import uuid

import httpx
import pytest

from app.config import Settings
from app.main import create_app


@pytest.mark.asyncio
async def test_generated_request_id_when_missing() -> None:
    """Missing X-Request-ID generates a valid canonical UUIDv4 returned in response headers."""
    app = create_app(Settings())

    async with app.router.lifespan_context(app):
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
            response = await client.get("/health")

    assert response.status_code == 200
    request_id = response.headers.get("X-Request-ID")
    assert request_id is not None
    parsed = uuid.UUID(request_id)
    assert str(parsed) == request_id
    assert parsed.version == 4


@pytest.mark.asyncio
async def test_valid_incoming_request_id_is_preserved_and_forwarded() -> None:
    """A valid UUID X-Request-ID is preserved across request state, downstream call, and response."""
    incoming_uuid = str(uuid.uuid4())
    captured_downstream_headers: dict[str, str] = {}

    async def mock_handler(request: httpx.Request) -> httpx.Response:
        captured_downstream_headers.update({k.lower(): v for k, v in request.headers.items()})
        return httpx.Response(200, json={"request_id_seen": request.headers.get("X-Request-ID")})

    app = create_app(Settings(), http_transport=httpx.MockTransport(mock_handler))

    async with app.router.lifespan_context(app):
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
            response = await client.get(
                "/api/v1/projects",
                headers={"X-Request-ID": incoming_uuid},
            )

    assert response.status_code == 200
    assert response.headers.get("X-Request-ID") == incoming_uuid
    assert captured_downstream_headers.get("x-request-id") == incoming_uuid
    assert response.json()["request_id_seen"] == incoming_uuid


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "invalid_header",
    [
        "not-a-uuid",
        "12345",
        "550e8400e29b41d4a716446655440000",  # missing hyphens
        "../../malicious-id",
        " ",
    ],
)
async def test_invalid_incoming_request_id_is_replaced_with_fresh_uuid(invalid_header: str) -> None:
    """Invalid or unrestricted client-supplied X-Request-ID values are replaced with a fresh UUID."""
    captured_downstream_id: str | None = None

    async def mock_handler(request: httpx.Request) -> httpx.Response:
        nonlocal captured_downstream_id
        captured_downstream_id = request.headers.get("X-Request-ID")
        return httpx.Response(200, json={"ok": True})

    app = create_app(Settings(), http_transport=httpx.MockTransport(mock_handler))

    async with app.router.lifespan_context(app):
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
            response = await client.get(
                "/api/v1/auth/status",
                headers={"X-Request-ID": invalid_header},
            )

    assert response.status_code == 200
    response_id = response.headers.get("X-Request-ID")
    assert response_id is not None
    assert response_id != invalid_header
    parsed = uuid.UUID(response_id)
    assert str(parsed) == response_id
    assert captured_downstream_id == response_id


@pytest.mark.asyncio
async def test_request_id_included_in_structured_logs_without_body_or_secrets(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Structured logs include the correlation request ID and omit secrets and bodies."""
    incoming_uuid = str(uuid.uuid4())

    async def mock_handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(201, json={"created": True})

    app = create_app(Settings(), http_transport=httpx.MockTransport(mock_handler))
    gateway_logger = logging.getLogger("api_gateway")
    gateway_logger.propagate = True

    with caplog.at_level(logging.INFO, logger="api_gateway"):
        async with app.router.lifespan_context(app):
            transport = httpx.ASGITransport(app=app)
            async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
                await client.post(
                    "/api/v1/auth/login?code=secret_oauth_code",
                    headers={
                        "X-Request-ID": incoming_uuid,
                        "Authorization": "Bearer super-secret-jwt-token",
                    },
                    json={"password": "super-secret-password"},
                )

    formatter = gateway_logger.handlers[0].formatter
    assert formatter is not None
    rendered_logs = [formatter.format(rec) for rec in caplog.records if rec.name.startswith("api_gateway")]
    assert rendered_logs

    parsed_logs = [json.loads(line) for line in rendered_logs]
    assert any(entry.get("request_id") == incoming_uuid for entry in parsed_logs)
    joined_logs = "\n".join(rendered_logs)
    assert "super-secret-jwt-token" not in joined_logs
    assert "super-secret-password" not in joined_logs
    assert "secret_oauth_code" not in joined_logs

