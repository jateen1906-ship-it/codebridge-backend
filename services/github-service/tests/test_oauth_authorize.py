"""Tests for GET /api/v1/github/oauth/authorize initiation and CSRF state creation."""

from __future__ import annotations

from urllib.parse import parse_qs, urlparse

import pytest
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.security import hash_oauth_state
from app.models.oauth_state import OAuthState
from tests.conftest import (
    MISSING_PROJECT_ID,
    OTHER_OWNER_PROJECT_ID,
    OWNER_ID,
    PROJECT_ID,
    TEST_ADMIN_TOKEN,
    TEST_FREELANCER_TOKEN,
    TEST_OWNER_TOKEN,
    MockIdentityState,
    MockProjectState,
    auth_headers,
)


@pytest.mark.asyncio
async def test_authorize_redirects_owner_and_persists_hashed_state(
    client: AsyncClient,
    db_session: AsyncSession,
) -> None:
    resp = await client.get(
        "/api/v1/github/oauth/authorize",
        params={"project_id": str(PROJECT_ID)},
        headers=auth_headers(TEST_OWNER_TOKEN),
        follow_redirects=False,
    )

    assert resp.status_code == 302
    assert resp.headers.get("cache-control") == "no-store"
    location = resp.headers["location"]
    parsed = urlparse(location)
    assert f"{parsed.scheme}://{parsed.netloc}{parsed.path}" == (
        "https://github.com/login/oauth/authorize"
    )

    params = parse_qs(parsed.query)
    assert params["client_id"] == ["test-client-id"]
    assert params["redirect_uri"] == ["http://localhost:8000/api/v1/github/oauth/callback"]
    assert params["scope"] == ["repo read:user"]
    assert "client_secret" not in location
    assert "test-client-secret" not in location

    raw_state = params["state"][0]
    assert len(raw_state) >= 32

    rows = (await db_session.execute(select(OAuthState))).scalars().all()
    assert len(rows) == 1
    state_row = rows[0]
    assert state_row.state_hash != raw_state
    assert state_row.state_hash == hash_oauth_state(raw_state)
    assert state_row.owner_id == OWNER_ID
    assert state_row.project_id == PROJECT_ID
    assert state_row.consumed_at is None


@pytest.mark.asyncio
async def test_authorize_rejects_unauthenticated_and_non_owner_roles(
    client: AsyncClient,
) -> None:
    unauth = await client.get(
        "/api/v1/github/oauth/authorize",
        params={"project_id": str(PROJECT_ID)},
        follow_redirects=False,
    )
    assert unauth.status_code == 401
    assert unauth.json()["error"]["code"] == "UNAUTHORIZED"

    for token in (TEST_FREELANCER_TOKEN, TEST_ADMIN_TOKEN):
        forbidden = await client.get(
            "/api/v1/github/oauth/authorize",
            params={"project_id": str(PROJECT_ID)},
            headers=auth_headers(token),
            follow_redirects=False,
        )
        assert forbidden.status_code == 403
        assert forbidden.json()["error"]["code"] == "FORBIDDEN"


@pytest.mark.asyncio
async def test_authorize_rejects_other_owner_project_and_missing_project(
    client: AsyncClient,
) -> None:
    other_owner_proj = await client.get(
        "/api/v1/github/oauth/authorize",
        params={"project_id": str(OTHER_OWNER_PROJECT_ID)},
        headers=auth_headers(TEST_OWNER_TOKEN),
        follow_redirects=False,
    )
    assert other_owner_proj.status_code == 403

    missing_proj = await client.get(
        "/api/v1/github/oauth/authorize",
        params={"project_id": str(MISSING_PROJECT_ID)},
        headers=auth_headers(TEST_OWNER_TOKEN),
        follow_redirects=False,
    )
    assert missing_proj.status_code == 404
    assert missing_proj.json()["error"]["code"] == "PROJECT_NOT_FOUND"


@pytest.mark.asyncio
async def test_authorize_fails_closed_when_project_or_identity_unavailable(
    client: AsyncClient,
    mock_project_state: MockProjectState,
    mock_identity_state: MockIdentityState,
) -> None:
    mock_project_state.force_connect_error = True
    resp = await client.get(
        "/api/v1/github/oauth/authorize",
        params={"project_id": str(PROJECT_ID)},
        headers=auth_headers(TEST_OWNER_TOKEN),
        follow_redirects=False,
    )
    assert resp.status_code == 503
    assert resp.json()["error"]["code"] == "PROJECT_SERVICE_UNAVAILABLE"
    mock_project_state.force_connect_error = False

    mock_identity_state.force_timeout = True
    resp2 = await client.get(
        "/api/v1/github/oauth/authorize",
        params={"project_id": str(PROJECT_ID)},
        headers=auth_headers(TEST_OWNER_TOKEN),
        follow_redirects=False,
    )
    assert resp2.status_code == 503
    assert resp2.json()["error"]["code"] == "IDENTITY_SERVICE_UNAVAILABLE"

