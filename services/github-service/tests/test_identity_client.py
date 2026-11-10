"""Unit/transport tests for IdentityClient authentication, header forwarding, & error mapping."""

from __future__ import annotations

import uuid

import httpx
import pytest

from app.clients.identity_client import IdentityClient
from app.core.exceptions import (
    ForbiddenError,
    UnauthorizedError,
    UpstreamServiceUnavailableError,
)
from app.core.security import UserRole
from tests.conftest import OWNER_ID, build_test_settings


@pytest.mark.asyncio
async def test_identity_client_validates_user_and_propagates_request_id(tmp_path) -> None:
    captured_headers: list[httpx.Headers] = []

    def handler(request: httpx.Request) -> httpx.Response:
        captured_headers.append(request.headers)
        assert request.url.path == "/api/v1/auth/me"
        return httpx.Response(
            200,
            json={
                "id": str(OWNER_ID),
                "role": "OWNER",
                "is_active": True,
                "is_verified": True,
            },
        )

    settings = build_test_settings((tmp_path / "id.db").as_posix())
    client = IdentityClient(settings, transport=httpx.MockTransport(handler))
    await client.startup()
    try:
        req_id = str(uuid.uuid4())
        user = await client.authenticate_token("raw-jwt-token", request_id=req_id)
        assert user.id == OWNER_ID
        assert user.role == UserRole.OWNER
        assert user.is_active is True
        assert captured_headers[0]["x-request-id"] == req_id
        assert captured_headers[0]["authorization"] == "Bearer raw-jwt-token"
    finally:
        await client.close()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("response_factory", "expected_exc"),
    [
        (lambda req: httpx.Response(401, json={}), UnauthorizedError),
        (lambda req: httpx.Response(403, json={}), ForbiddenError),
        (
            lambda req: httpx.Response(
                200,
                json={"id": str(OWNER_ID), "role": "OWNER", "is_active": False},
            ),
            ForbiddenError,
        ),
        (lambda req: httpx.Response(500, json={}), UpstreamServiceUnavailableError),
        (lambda req: httpx.Response(200, content=b"not-json"), UpstreamServiceUnavailableError),
    ],
)
async def test_identity_client_error_mapping(tmp_path, response_factory, expected_exc) -> None:
    settings = build_test_settings((tmp_path / "id_err.db").as_posix())
    client = IdentityClient(
        settings,
        transport=httpx.MockTransport(lambda req: response_factory(req)),
    )
    await client.startup()
    try:
        with pytest.raises(expected_exc):
            await client.authenticate_token("tok", request_id=str(uuid.uuid4()))
    finally:
        await client.close()


@pytest.mark.asyncio
async def test_identity_client_timeout_and_connect_fail_closed(tmp_path) -> None:
    settings = build_test_settings((tmp_path / "id_net.db").as_posix())

    def timeout_handler(req: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout("timeout", request=req)

    c1 = IdentityClient(settings, transport=httpx.MockTransport(timeout_handler))
    await c1.startup()
    try:
        with pytest.raises(UpstreamServiceUnavailableError):
            await c1.authenticate_token("tok", request_id=str(uuid.uuid4()))
    finally:
        await c1.close()

    def connect_handler(req: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("down", request=req)

    c2 = IdentityClient(settings, transport=httpx.MockTransport(connect_handler))
    await c2.startup()
    try:
        with pytest.raises(UpstreamServiceUnavailableError):
            await c2.authenticate_token("tok", request_id=str(uuid.uuid4()))
    finally:
        await c2.close()

