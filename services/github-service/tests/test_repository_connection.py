"""Tests for POST /api/v1/projects/{project_id}/github/connect."""

from __future__ import annotations

import pytest
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.project_repository import ProjectRepository, ProjectRepositoryStatus
from tests.conftest import (
    MISSING_PROJECT_ID,
    OTHER_OWNER_PROJECT_ID,
    PROJECT_ID,
    TEST_FREELANCER_TOKEN,
    TEST_OTHER_OWNER_TOKEN,
    TEST_OWNER_TOKEN,
    auth_headers,
    connect_owner_github_account,
)


@pytest.mark.asyncio
async def test_connect_repository_succeeds_and_enforces_single_active_per_project(
    client: AsyncClient,
    db_session: AsyncSession,
) -> None:
    await connect_owner_github_account(client)

    payload = {"github_repository_id": 1001, "full_name": "alice-gh/sample-api"}
    resp = await client.post(
        f"/api/v1/projects/{PROJECT_ID}/github/connect",
        json=payload,
        headers=auth_headers(TEST_OWNER_TOKEN),
    )
    assert resp.status_code == 201
    body = resp.json()
    assert body["project_id"] == str(PROJECT_ID)
    assert body["github_repository_id"] == 1001
    assert body["full_name"] == "alice-gh/sample-api"
    assert body["is_private"] is True
    assert body["default_branch"] == "main"
    assert body["connection_status"] == "ACTIVE"

    # Second connect to same project -> 409 PROJECT_ALREADY_CONNECTED
    conflict = await client.post(
        f"/api/v1/projects/{PROJECT_ID}/github/connect",
        json={"github_repository_id": 1002, "full_name": "alice-gh/second-repo"},
        headers=auth_headers(TEST_OWNER_TOKEN),
    )
    assert conflict.status_code == 409
    assert conflict.json()["error"]["code"] == "PROJECT_ALREADY_CONNECTED"

    # Disconnect, then connecting replacement repo succeeds while preserving audit history
    disc = await client.delete(
        f"/api/v1/projects/{PROJECT_ID}/github/disconnect",
        headers=auth_headers(TEST_OWNER_TOKEN),
    )
    assert disc.status_code == 200

    reconnect = await client.post(
        f"/api/v1/projects/{PROJECT_ID}/github/connect",
        json={"github_repository_id": 1002, "full_name": "alice-gh/second-repo"},
        headers=auth_headers(TEST_OWNER_TOKEN),
    )
    assert reconnect.status_code == 201
    assert reconnect.json()["full_name"] == "alice-gh/second-repo"

    rows = (
        await db_session.execute(
            select(ProjectRepository).where(ProjectRepository.project_id == PROJECT_ID)
        )
    ).scalars().all()
    assert len(rows) == 2
    statuses = sorted(r.connection_status for r in rows)
    assert statuses == [ProjectRepositoryStatus.ACTIVE, ProjectRepositoryStatus.DISCONNECTED]


@pytest.mark.asyncio
async def test_connect_repository_rejects_mismatch_unauthorized_and_missing_resources(
    client: AsyncClient,
) -> None:
    # Missing GitHub connection -> 404
    no_conn = await client.post(
        f"/api/v1/projects/{PROJECT_ID}/github/connect",
        json={"github_repository_id": 1001, "full_name": "alice-gh/sample-api"},
        headers=auth_headers(TEST_OWNER_TOKEN),
    )
    assert no_conn.status_code == 404
    assert no_conn.json()["error"]["code"] == "GITHUB_CONNECTION_NOT_FOUND"

    await connect_owner_github_account(client)

    # Repository ID mismatch -> 400
    mismatch = await client.post(
        f"/api/v1/projects/{PROJECT_ID}/github/connect",
        json={"github_repository_id": 999999, "full_name": "alice-gh/sample-api"},
        headers=auth_headers(TEST_OWNER_TOKEN),
    )
    assert mismatch.status_code == 400
    assert mismatch.json()["error"]["code"] == "REPOSITORY_MISMATCH"

    # Repository without pull access -> 403
    no_pull = await client.post(
        f"/api/v1/projects/{PROJECT_ID}/github/connect",
        json={"github_repository_id": 1003, "full_name": "alice-gh/no-pull-repo"},
        headers=auth_headers(TEST_OWNER_TOKEN),
    )
    assert no_pull.status_code == 403

    # Non-existent repo on GitHub -> 404
    missing_repo = await client.post(
        f"/api/v1/projects/{PROJECT_ID}/github/connect",
        json={"github_repository_id": 1001, "full_name": "alice-gh/does-not-exist"},
        headers=auth_headers(TEST_OWNER_TOKEN),
    )
    assert missing_repo.status_code == 404
    assert missing_repo.json()["error"]["code"] == "GITHUB_RESOURCE_NOT_FOUND"

    # Cross-owner & freelancer & missing project
    other_owner = await client.post(
        f"/api/v1/projects/{OTHER_OWNER_PROJECT_ID}/github/connect",
        json={"github_repository_id": 1001, "full_name": "alice-gh/sample-api"},
        headers=auth_headers(TEST_OWNER_TOKEN),
    )
    assert other_owner.status_code == 403

    another_owner_target = await client.post(
        f"/api/v1/projects/{PROJECT_ID}/github/connect",
        json={"github_repository_id": 1001, "full_name": "alice-gh/sample-api"},
        headers=auth_headers(TEST_OTHER_OWNER_TOKEN),
    )
    assert another_owner_target.status_code == 403

    freelancer = await client.post(
        f"/api/v1/projects/{PROJECT_ID}/github/connect",
        json={"github_repository_id": 1001, "full_name": "alice-gh/sample-api"},
        headers=auth_headers(TEST_FREELANCER_TOKEN),
    )
    assert freelancer.status_code == 403

    missing_proj = await client.post(
        f"/api/v1/projects/{MISSING_PROJECT_ID}/github/connect",
        json={"github_repository_id": 1001, "full_name": "alice-gh/sample-api"},
        headers=auth_headers(TEST_OWNER_TOKEN),
    )
    assert missing_proj.status_code == 404
    assert missing_proj.json()["error"]["code"] == "PROJECT_NOT_FOUND"

