"""Tests for project repository and GitHub account disconnection lifecycle."""

from __future__ import annotations

import pytest
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.encryption import REVOKED_TOKEN_SENTINEL
from app.models.github_connection import GitHubConnection, GitHubConnectionStatus
from tests.conftest import (
    ASSIGNED_PROJECT_ID,
    OPEN_PROJECT_ID,
    OWNER_ID,
    PROJECT_ID,
    TEST_OWNER_TOKEN,
    MockProjectState,
    auth_headers,
    connect_owner_github_account,
)


@pytest.mark.asyncio
async def test_disconnect_project_repo_respects_active_assignments_and_keeps_connection(
    client: AsyncClient,
    mock_project_state: MockProjectState,
) -> None:
    await connect_owner_github_account(client)

    # Connect both OPEN_PROJECT_ID and ASSIGNED_PROJECT_ID
    for pid in (OPEN_PROJECT_ID, ASSIGNED_PROJECT_ID):
        resp = await client.post(
            f"/api/v1/projects/{pid}/github/connect",
            json={"github_repository_id": 1001, "full_name": "alice-gh/sample-api"},
            headers=auth_headers(TEST_OWNER_TOKEN),
        )
        assert resp.status_code == 201

    # Active assignment blocks project repo disconnect -> 409
    blocked = await client.delete(
        f"/api/v1/projects/{ASSIGNED_PROJECT_ID}/github/disconnect",
        headers=auth_headers(TEST_OWNER_TOKEN),
    )
    assert blocked.status_code == 409
    assert blocked.json()["error"]["code"] == "ACTIVE_ASSIGNMENT_CONFLICT"

    # Project without active assignment disconnects cleanly
    ok = await client.delete(
        f"/api/v1/projects/{OPEN_PROJECT_ID}/github/disconnect",
        headers=auth_headers(TEST_OWNER_TOKEN),
    )
    assert ok.status_code == 200
    assert ok.json()["connection_status"] == "DISCONNECTED"

    # Owner GitHub account & ASSIGNED_PROJECT_ID remain connected
    assigned_status = await client.get(
        f"/api/v1/projects/{ASSIGNED_PROJECT_ID}/github/status",
        headers=auth_headers(TEST_OWNER_TOKEN),
    )
    assert assigned_status.status_code == 200
    assert assigned_status.json()["connected"] is True

    # Project Service unavailable fails closed -> 503
    mock_project_state.force_timeout = True
    fail_closed = await client.delete(
        f"/api/v1/projects/{ASSIGNED_PROJECT_ID}/github/disconnect",
        headers=auth_headers(TEST_OWNER_TOKEN),
    )
    assert fail_closed.status_code == 503


@pytest.mark.asyncio
async def test_disconnect_github_account_scrubs_token_unlinks_projects_and_blocks_active_workflows(
    client: AsyncClient,
    db_session: AsyncSession,
) -> None:
    await connect_owner_github_account(client)

    # Connect inactive project -> full account disconnect succeeds & cascades project unlink
    await client.post(
        f"/api/v1/projects/{PROJECT_ID}/github/connect",
        json={"github_repository_id": 1001, "full_name": "alice-gh/sample-api"},
        headers=auth_headers(TEST_OWNER_TOKEN),
    )

    disc_acct = await client.delete(
        "/api/v1/github/connection",
        headers=auth_headers(TEST_OWNER_TOKEN),
    )
    assert disc_acct.status_code == 200
    assert disc_acct.json()["status"] == "DISCONNECTED"
    assert disc_acct.json()["disconnected_projects_count"] == 1

    # Token material in DB overwritten with sentinel
    conn_row = (
        await db_session.execute(
            select(GitHubConnection).where(GitHubConnection.owner_id == OWNER_ID)
        )
    ).scalar_one()
    assert conn_row.status == GitHubConnectionStatus.DISCONNECTED
    assert conn_row.encrypted_access_token == REVOKED_TOKEN_SENTINEL

    # Subsequent repo list returns 404
    after_list = await client.get(
        "/api/v1/github/repositories",
        headers=auth_headers(TEST_OWNER_TOKEN),
    )
    assert after_list.status_code == 404

    # Reconnecting OAuth works cleanly after disconnect and active assignment blocks disconnect
    await connect_owner_github_account(client)
    await client.post(
        f"/api/v1/projects/{ASSIGNED_PROJECT_ID}/github/connect",
        json={"github_repository_id": 1001, "full_name": "alice-gh/sample-api"},
        headers=auth_headers(TEST_OWNER_TOKEN),
    )
    blocked_acct = await client.delete(
        "/api/v1/github/connection",
        headers=auth_headers(TEST_OWNER_TOKEN),
    )
    assert blocked_acct.status_code == 409
    assert blocked_acct.json()["error"]["code"] == "ACTIVE_ASSIGNMENT_CONFLICT"

