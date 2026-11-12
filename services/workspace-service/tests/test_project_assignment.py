"""Tests for project assignment gating and Project Service lifecycle synchronization."""

from __future__ import annotations

import pytest
from httpx import AsyncClient

from tests.conftest import (
    ASSIGNED_PROJECT_ID,
    COMPLETED_PROJECT_ID,
    OPEN_PROJECT_ID,
    TEST_FREELANCER_TOKEN,
    TEST_OWNER_TOKEN,
    MockProjectState,
    auth_headers,
)


@pytest.mark.asyncio
async def test_rejects_unassigned_open_or_completed_projects_and_syncs_in_progress(
    client: AsyncClient,
    mock_project_state: MockProjectState,
) -> None:
    # Unassigned OPEN project cannot provision workspace even by owner
    open_resp = await client.post(
        f"/api/v1/workspace/projects/{OPEN_PROJECT_ID}",
        headers=auth_headers(TEST_OWNER_TOKEN),
    )
    assert open_resp.status_code == 409
    assert open_resp.json()["error"]["code"] == "INVALID_PROJECT_STATUS"

    # COMPLETED project cannot provision workspace
    done_resp = await client.post(
        f"/api/v1/workspace/projects/{COMPLETED_PROJECT_ID}",
        headers=auth_headers(TEST_OWNER_TOKEN),
    )
    assert done_resp.status_code == 409

    # ASSIGNED project provisions and advances Project Service status -> IN_PROGRESS
    ok = await client.post(
        f"/api/v1/workspace/projects/{ASSIGNED_PROJECT_ID}",
        headers=auth_headers(TEST_FREELANCER_TOKEN),
    )
    assert ok.status_code == 201
    assert mock_project_state.projects[ASSIGNED_PROJECT_ID]["status"] == "IN_PROGRESS"

