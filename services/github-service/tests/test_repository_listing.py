"""Tests for GET /api/v1/github/repositories owner discovery and error mapping."""

from __future__ import annotations

import pytest
from httpx import AsyncClient

from tests.conftest import (
    TEST_ADMIN_TOKEN,
    TEST_FREELANCER_TOKEN,
    TEST_OWNER_TOKEN,
    MockGitHubState,
    auth_headers,
    connect_owner_github_account,
)


@pytest.mark.asyncio
async def test_list_repositories_returns_shaped_items_and_pagination(
    client: AsyncClient,
    mock_github_state: MockGitHubState,
) -> None:
    await connect_owner_github_account(client)
    mock_github_state.list_has_next_page = True

    resp = await client.get(
        "/api/v1/github/repositories",
        params={"page": 1, "per_page": 100, "visibility": "all"},
        headers=auth_headers(TEST_OWNER_TOKEN),
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["page"] == 1
    assert body["per_page"] == 50  # capped to GITHUB_API_PAGE_SIZE=50
    assert body["has_next_page"] is True
    assert len(body["items"]) == 2
    assert body["items"][0]["github_repository_id"] == 1001
    assert body["items"][0]["full_name"] == "alice-gh/sample-api"
    assert body["items"][0]["permissions"]["pull"] is True
    assert "gho_" not in resp.text


@pytest.mark.asyncio
async def test_list_repositories_validation_missing_connection_and_upstream_errors(
    client: AsyncClient,
    mock_github_state: MockGitHubState,
) -> None:
    # Missing OAuth connection -> 404
    missing = await client.get(
        "/api/v1/github/repositories",
        headers=auth_headers(TEST_OWNER_TOKEN),
    )
    assert missing.status_code == 404
    assert missing.json()["error"]["code"] == "GITHUB_CONNECTION_NOT_FOUND"

    # Connect first
    await connect_owner_github_account(client)

    # Exceeding max pages -> 422
    too_many_pages = await client.get(
        "/api/v1/github/repositories",
        params={"page": 10},
        headers=auth_headers(TEST_OWNER_TOKEN),
    )
    assert too_many_pages.status_code == 422
    assert too_many_pages.json()["error"]["code"] == "VALIDATION_ERROR"

    # Invalid visibility -> 422
    bad_vis = await client.get(
        "/api/v1/github/repositories",
        params={"visibility": "internal_only"},
        headers=auth_headers(TEST_OWNER_TOKEN),
    )
    assert bad_vis.status_code == 422

    # Non-owner roles denied
    for token in (TEST_FREELANCER_TOKEN, TEST_ADMIN_TOKEN):
        denied = await client.get(
            "/api/v1/github/repositories",
            headers=auth_headers(token),
        )
        assert denied.status_code == 403

    # Rate limit -> 429
    mock_github_state.forced_rate_limit_403 = True
    limited = await client.get(
        "/api/v1/github/repositories",
        headers=auth_headers(TEST_OWNER_TOKEN),
    )
    assert limited.status_code == 429
    assert limited.json()["error"]["code"] == "GITHUB_RATE_LIMITED"
    mock_github_state.forced_rate_limit_403 = False

    # GitHub revoked token -> 502 GITHUB_TOKEN_INVALID
    mock_github_state.forced_api_status = 401
    revoked = await client.get(
        "/api/v1/github/repositories",
        headers=auth_headers(TEST_OWNER_TOKEN),
    )
    assert revoked.status_code == 502
    assert revoked.json()["error"]["code"] == "GITHUB_TOKEN_INVALID"
    mock_github_state.forced_api_status = None

    # Timeout -> 504
    mock_github_state.force_timeout = True
    timed_out = await client.get(
        "/api/v1/github/repositories",
        headers=auth_headers(TEST_OWNER_TOKEN),
    )
    assert timed_out.status_code == 504
    assert timed_out.json()["error"]["code"] == "UPSTREAM_TIMEOUT"

