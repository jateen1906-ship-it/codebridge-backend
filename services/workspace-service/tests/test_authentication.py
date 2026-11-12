"""Tests for Workspace Service authentication and correlation ID propagation."""

from __future__ import annotations

import uuid

import pytest
from httpx import AsyncClient

from tests.conftest import (
    ASSIGNED_PROJECT_ID,
    TEST_FREELANCER_TOKEN,
    MockIdentityState,
    auth_headers,
)


@pytest.mark.asyncio
async def test_requires_valid_bearer_token_and_propagates_request_id(
    client: AsyncClient,
    mock_identity_state: MockIdentityState,
) -> None:
    no_auth = await client.post(f"/api/v1/workspace/projects/{ASSIGNED_PROJECT_ID}")
    assert no_auth.status_code == 401
    assert no_auth.json()["error"]["code"] == "UNAUTHORIZED"

    bad_auth = await client.post(
        f"/api/v1/workspace/projects/{ASSIGNED_PROJECT_ID}",
        headers=auth_headers("bad-token"),
    )
    assert bad_auth.status_code == 401

    req_id = str(uuid.uuid4())
    ok = await client.post(
        f"/api/v1/workspace/projects/{ASSIGNED_PROJECT_ID}",
        headers=auth_headers(TEST_FREELANCER_TOKEN, request_id=req_id),
    )
    assert ok.status_code == 201
    assert ok.headers["x-request-id"] == req_id
    assert mock_identity_state.requests[-1].headers["x-request-id"] == req_id


@pytest.mark.asyncio
async def test_identity_service_unavailable_fails_closed(
    client: AsyncClient,
    mock_identity_state: MockIdentityState,
) -> None:
    mock_identity_state.force_timeout = True
    resp = await client.post(
        f"/api/v1/workspace/projects/{ASSIGNED_PROJECT_ID}",
        headers=auth_headers(TEST_FREELANCER_TOKEN),
    )
    assert resp.status_code == 503
    assert resp.json()["error"]["code"] == "IDENTITY_SERVICE_UNAVAILABLE"

