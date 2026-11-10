"""Security tests for single-use, expiring, cross-owner, and tamper-proof OAuth states."""

from __future__ import annotations

import asyncio
from datetime import timedelta
from urllib.parse import parse_qs, urlparse

import pytest
from httpx import AsyncClient
from sqlalchemy import update
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.security import hash_oauth_state
from app.models.base import utc_now
from app.models.oauth_state import OAuthState
from tests.conftest import (
    PROJECT_ID,
    TEST_OTHER_OWNER_TOKEN,
    TEST_OWNER_TOKEN,
    MockGitHubState,
    auth_headers,
)


def _extract_state(location: str) -> str:
    return parse_qs(urlparse(location).query)["state"][0]


@pytest.mark.asyncio
async def test_rejects_missing_unknown_and_tampered_callback_params(
    client: AsyncClient,
    mock_github_state: MockGitHubState,
) -> None:
    missing_params = await client.get("/api/v1/github/oauth/callback")
    assert missing_params.status_code == 400
    assert missing_params.json()["error"]["code"] == "INVALID_OAUTH_CALLBACK"

    unknown_state = await client.get(
        "/api/v1/github/oauth/callback",
        params={"code": "valid-code", "state": "forged-unknown-state"},
    )
    assert unknown_state.status_code == 400
    assert unknown_state.json()["error"]["code"] == "INVALID_OAUTH_STATE"

    tampered_owner = await client.get(
        "/api/v1/github/oauth/callback",
        params={"code": "valid-code", "state": "anything", "owner_id": "123"},
    )
    assert tampered_owner.status_code == 400
    assert tampered_owner.json()["error"]["code"] == "INVALID_OAUTH_CALLBACK"

    tampered_proj = await client.get(
        "/api/v1/github/oauth/callback",
        params={"code": "valid-code", "state": "anything", "project_id": str(PROJECT_ID)},
    )
    assert tampered_proj.status_code == 400
    assert tampered_proj.json()["error"]["code"] == "INVALID_OAUTH_CALLBACK"

    assert mock_github_state.token_exchange_calls == 0


@pytest.mark.asyncio
async def test_rejects_state_replay_and_concurrent_callbacks(
    client: AsyncClient,
    mock_github_state: MockGitHubState,
) -> None:
    auth_resp = await client.get(
        "/api/v1/github/oauth/authorize",
        params={"project_id": str(PROJECT_ID)},
        headers=auth_headers(TEST_OWNER_TOKEN),
        follow_redirects=False,
    )
    raw_state = _extract_state(auth_resp.headers["location"])

    # Concurrent replay race against single-use state
    res1, res2 = await asyncio.gather(
        client.get("/api/v1/github/oauth/callback", params={"code": "valid-code", "state": raw_state}),
        client.get("/api/v1/github/oauth/callback", params={"code": "valid-code", "state": raw_state}),
    )
    statuses = sorted([res1.status_code, res2.status_code])
    assert statuses == [200, 409]
    assert mock_github_state.token_exchange_calls == 1

    # Explicit sequential replay also returns 409 without touching GitHub
    replay = await client.get(
        "/api/v1/github/oauth/callback",
        params={"code": "valid-code", "state": raw_state},
    )
    assert replay.status_code == 409
    assert replay.json()["error"]["code"] == "OAUTH_STATE_ALREADY_USED"
    assert mock_github_state.token_exchange_calls == 1


@pytest.mark.asyncio
async def test_rejects_expired_state_and_cross_owner_state_use(
    client: AsyncClient,
    db_session: AsyncSession,
    mock_github_state: MockGitHubState,
) -> None:
    # Different authenticated owner trying to consume owner 1's state
    auth_resp = await client.get(
        "/api/v1/github/oauth/authorize",
        params={"project_id": str(PROJECT_ID)},
        headers=auth_headers(TEST_OWNER_TOKEN),
        follow_redirects=False,
    )
    raw_state = _extract_state(auth_resp.headers["location"])

    cross_owner = await client.get(
        "/api/v1/github/oauth/callback",
        params={"code": "valid-code", "state": raw_state},
        headers=auth_headers(TEST_OTHER_OWNER_TOKEN),
    )
    assert cross_owner.status_code == 403
    assert cross_owner.json()["error"]["code"] == "FORBIDDEN"

    # Expired state check
    expired_time = utc_now() - timedelta(minutes=1)
    await db_session.execute(
        update(OAuthState)
        .where(OAuthState.state_hash == hash_oauth_state(raw_state))
        .values(expires_at=expired_time)
    )
    await db_session.commit()

    expired = await client.get(
        "/api/v1/github/oauth/callback",
        params={"code": "valid-code", "state": raw_state},
    )
    assert expired.status_code == 400
    assert expired.json()["error"]["code"] == "OAUTH_STATE_EXPIRED"
    assert mock_github_state.token_exchange_calls == 0

