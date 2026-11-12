"""Tests verifying Workspace Service fetches snapshots strictly via GitHub Service."""

from __future__ import annotations

import pytest
from httpx import AsyncClient

from tests.conftest import (
    ASSIGNED_PROJECT_ID,
    TEST_FREELANCER_TOKEN,
    MockGitHubServiceState,
    auth_headers,
)


@pytest.mark.asyncio
async def test_fetches_snapshot_from_github_service_and_handles_missing_repo_or_outage(
    client: AsyncClient,
    mock_github_state: MockGitHubServiceState,
) -> None:
    mock_github_state.connected_projects.discard(ASSIGNED_PROJECT_ID)
    not_conn = await client.post(
        f"/api/v1/workspace/projects/{ASSIGNED_PROJECT_ID}",
        headers=auth_headers(TEST_FREELANCER_TOKEN),
    )
    assert not_conn.status_code == 404
    assert not_conn.json()["error"]["code"] == "REPOSITORY_NOT_CONNECTED"

    mock_github_state.connected_projects.add(ASSIGNED_PROJECT_ID)
    mock_github_state.force_status = 502
    outage = await client.post(
        f"/api/v1/workspace/projects/{ASSIGNED_PROJECT_ID}",
        headers=auth_headers(TEST_FREELANCER_TOKEN),
    )
    assert outage.status_code == 503
    assert outage.json()["error"]["code"] == "GITHUB_SERVICE_UNAVAILABLE"

    # Recover and succeed
    mock_github_state.force_status = None
    ok = await client.post(
        f"/api/v1/workspace/projects/{ASSIGNED_PROJECT_ID}",
        headers=auth_headers(TEST_FREELANCER_TOKEN),
    )
    assert ok.status_code == 201
    assert len(mock_github_state.requests) >= 3
    for req in mock_github_state.requests:
        assert req.url.host == "github-test.local"

