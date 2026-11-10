"""Tests for GET /api/v1/projects/{project_id}/github/status across all roles."""

from __future__ import annotations

import pytest
from httpx import AsyncClient

from tests.conftest import (
    ASSIGNED_PROJECT_ID,
    MISSING_PROJECT_ID,
    OPEN_PROJECT_ID,
    OTHER_OWNER_PROJECT_ID,
    PROJECT_ID,
    TEST_ADMIN_TOKEN,
    TEST_FREELANCER_TOKEN,
    TEST_OTHER_FREELANCER_TOKEN,
    TEST_OTHER_OWNER_TOKEN,
    TEST_OWNER_TOKEN,
    auth_headers,
    connect_owner_github_account,
)


@pytest.mark.asyncio
async def test_project_github_status_connected_vs_not_connected_and_role_scoping(
    client: AsyncClient,
) -> None:
    await connect_owner_github_account(client)

    # Not connected state
    not_conn = await client.get(
        f"/api/v1/projects/{PROJECT_ID}/github/status",
        headers=auth_headers(TEST_OWNER_TOKEN),
    )
    assert not_conn.status_code == 200
    assert not_conn.json() == {
        "project_id": str(PROJECT_ID),
        "connected": False,
        "connection_status": "NOT_CONNECTED",
        "repository": None,
    }

    # Connect OPEN project and ASSIGNED project
    for proj_id in (OPEN_PROJECT_ID, ASSIGNED_PROJECT_ID):
        res = await client.post(
            f"/api/v1/projects/{proj_id}/github/connect",
            json={"github_repository_id": 1001, "full_name": "alice-gh/sample-api"},
            headers=auth_headers(TEST_OWNER_TOKEN),
        )
        assert res.status_code == 201

    # Owner & Admin see ASSIGNED project status
    for token in (TEST_OWNER_TOKEN, TEST_ADMIN_TOKEN):
        ok = await client.get(
            f"/api/v1/projects/{ASSIGNED_PROJECT_ID}/github/status",
            headers=auth_headers(token),
        )
        assert ok.status_code == 200
        data = ok.json()
        assert data["connected"] is True
        assert data["connection_status"] == "ACTIVE"
        assert data["repository"]["full_name"] == "alice-gh/sample-api"
        assert "github_connection_id" not in data["repository"]
        assert "gho_" not in ok.text

    # Assigned freelancer sees assigned project status
    assigned_fl = await client.get(
        f"/api/v1/projects/{ASSIGNED_PROJECT_ID}/github/status",
        headers=auth_headers(TEST_FREELANCER_TOKEN),
    )
    assert assigned_fl.status_code == 200
    assert assigned_fl.json()["connected"] is True

    # Any freelancer sees OPEN project status
    open_fl = await client.get(
        f"/api/v1/projects/{OPEN_PROJECT_ID}/github/status",
        headers=auth_headers(TEST_OTHER_FREELANCER_TOKEN),
    )
    assert open_fl.status_code == 200

    # Unassigned freelancer cannot read non-OPEN project status
    unassigned_fl = await client.get(
        f"/api/v1/projects/{ASSIGNED_PROJECT_ID}/github/status",
        headers=auth_headers(TEST_OTHER_FREELANCER_TOKEN),
    )
    assert unassigned_fl.status_code == 403

    # Other owner cannot read another owner's project status
    other_owner = await client.get(
        f"/api/v1/projects/{OPEN_PROJECT_ID}/github/status",
        headers=auth_headers(TEST_OTHER_OWNER_TOKEN),
    )
    assert other_owner.status_code == 403

    # Owner cannot read other owner's project
    other_proj = await client.get(
        f"/api/v1/projects/{OTHER_OWNER_PROJECT_ID}/github/status",
        headers=auth_headers(TEST_OWNER_TOKEN),
    )
    assert other_proj.status_code == 403

    # Missing project
    missing = await client.get(
        f"/api/v1/projects/{MISSING_PROJECT_ID}/github/status",
        headers=auth_headers(TEST_OWNER_TOKEN),
    )
    assert missing.status_code == 404

