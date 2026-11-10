"""Tests for synchronous HTTPX IdentityClient behavior, resilience, and header hygiene."""

from __future__ import annotations

import uuid

import httpx
import pytest
from fastapi import FastAPI

from app.clients.identity_client import IdentityClient
from app.core.config import Settings
from app.core.exceptions import (
    ForbiddenError,
    FreelancerNotFoundError,
    IdentityServiceUnavailableError,
    InvalidFreelancerError,
    UnauthorizedError,
)
from app.core.security import UserRole
from tests.conftest import (
    FREELANCER_ID,
    INACTIVE_USER_TOKEN,
    INVALID_TOKEN,
    MISSING_FREELANCER_ID,
    OWNER_ID,
    OWNER_TOKEN,
    WRONG_ROLE_FREELANCER_ID,
    MockIdentityRouter,
    auth_headers,
    create_sample_project,
)


@pytest.mark.asyncio
async def test_authenticate_token_valid_and_propagates_request_id(
    test_settings: Settings,
    identity_mock: MockIdentityRouter,
) -> None:
    req_id = str(uuid.uuid4())
    client = IdentityClient(test_settings, transport=httpx.MockTransport(identity_mock.handle))
    await client.startup()
    try:
        user = await client.authenticate_token(OWNER_TOKEN, request_id=req_id)
        assert user.id == OWNER_ID
        assert user.role == UserRole.OWNER
        assert user.is_active is True

        captured = identity_mock.recorded_requests[-1]
        assert captured.path == "/api/v1/auth/me"
        assert captured.headers["authorization"] == f"Bearer {OWNER_TOKEN}"
        assert captured.headers["x-request-id"] == req_id
    finally:
        await client.close()


@pytest.mark.asyncio
async def test_authenticate_token_rejects_inactive_401_403_and_upstream_failures(
    test_settings: Settings,
    identity_mock: MockIdentityRouter,
) -> None:
    req_id = str(uuid.uuid4())
    client = IdentityClient(test_settings, transport=httpx.MockTransport(identity_mock.handle))
    await client.startup()
    try:
        with pytest.raises(ForbiddenError):
            await client.authenticate_token(INACTIVE_USER_TOKEN, request_id=req_id)

        with pytest.raises(UnauthorizedError):
            await client.authenticate_token(INVALID_TOKEN, request_id=req_id)

        identity_mock.auth_mode = "403"
        with pytest.raises(ForbiddenError):
            await client.authenticate_token(OWNER_TOKEN, request_id=req_id)

        for failure_mode in ("timeout", "connect_error", "500", "malformed_json", "missing_field"):
            identity_mock.auth_mode = failure_mode
            with pytest.raises(IdentityServiceUnavailableError) as exc_info:
                await client.authenticate_token(OWNER_TOKEN, request_id=req_id)
            assert "internal-identity-mock" not in exc_info.value.message
    finally:
        await client.close()


@pytest.mark.asyncio
async def test_verify_freelancer_resilience_and_no_auth_header_leak(
    test_settings: Settings,
    identity_mock: MockIdentityRouter,
) -> None:
    req_id = str(uuid.uuid4())
    client = IdentityClient(test_settings, transport=httpx.MockTransport(identity_mock.handle))
    await client.startup()
    try:
        verified = await client.verify_freelancer(FREELANCER_ID, request_id=req_id)
        assert verified.user_id == FREELANCER_ID

        captured = identity_mock.recorded_requests[-1]
        assert captured.path == f"/api/v1/freelancers/{FREELANCER_ID}"
        assert captured.headers["x-request-id"] == req_id
        assert "authorization" not in captured.headers

        with pytest.raises(FreelancerNotFoundError):
            await client.verify_freelancer(MISSING_FREELANCER_ID, request_id=req_id)

        with pytest.raises(InvalidFreelancerError):
            await client.verify_freelancer(WRONG_ROLE_FREELANCER_ID, request_id=req_id)

        for failure_mode in ("timeout", "connect_error", "500", "malformed_json", "missing_fields"):
            identity_mock.freelancer_mode = failure_mode
            with pytest.raises(IdentityServiceUnavailableError) as exc_info:
                await client.verify_freelancer(FREELANCER_ID, request_id=req_id)
            assert "internal-identity-mock" not in exc_info.value.message
    finally:
        await client.close()


@pytest.mark.asyncio
async def test_http_endpoint_never_leaks_identity_url_or_caller_token_to_freelancers_endpoint(
    client: httpx.AsyncClient,
    project_app: FastAPI,
    identity_mock: MockIdentityRouter,
) -> None:
    proj = await create_sample_project(client, token=OWNER_TOKEN)
    req_id = str(uuid.uuid4())

    assign_res = await client.post(
        f"/api/v1/projects/{proj['id']}/select-freelancer",
        headers=auth_headers(OWNER_TOKEN, request_id=req_id),
        json={"freelancer_id": str(FREELANCER_ID)},
    )
    assert assign_res.status_code == 201

    freelancer_calls = [
        rec for rec in identity_mock.recorded_requests if rec.path.startswith("/api/v1/freelancers/")
    ]
    assert len(freelancer_calls) == 1
    assert freelancer_calls[0].headers["x-request-id"] == req_id
    assert "authorization" not in freelancer_calls[0].headers

    # Now simulate Identity outage during auth -> sanitized 503 with matching X-Request-ID
    identity_mock.auth_mode = "connect_error"
    err_res = await client.get(
        "/api/v1/projects",
        headers=auth_headers(OWNER_TOKEN, request_id=req_id),
    )
    assert err_res.status_code == 503
    assert err_res.headers["x-request-id"] == req_id
    err_body = err_res.json()
    assert err_body["error"]["code"] == "IDENTITY_SERVICE_UNAVAILABLE"
    assert "internal-identity-mock" not in err_res.text

