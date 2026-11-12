"""Tests for workspace provisioning, duplicate prevention, quotas, and image allowlisting."""

from __future__ import annotations

import uuid
from pathlib import Path

import pytest
from httpx import AsyncClient

from tests.conftest import (
    ASSIGNED_PROJECT_ID,
    OWNER_ID,
    SECOND_ASSIGNED_PROJECT_ID,
    TEST_FREELANCER_TOKEN,
    MockGitHubServiceState,
    MockProjectState,
    auth_headers,
)


@pytest.mark.asyncio
async def test_workspace_provisioning_extracts_files_and_enforces_uniqueness_and_images(
    client: AsyncClient,
    mock_project_state: MockProjectState,
    mock_github_state: MockGitHubServiceState,
) -> None:
    bad_image = await client.post(
        f"/api/v1/workspace/projects/{ASSIGNED_PROJECT_ID}",
        json={"runtime_image": "evil-registry/backdoor:latest"},
        headers=auth_headers(TEST_FREELANCER_TOKEN),
    )
    assert bad_image.status_code == 422

    # Valid provisioning with auto_start=True
    created = await client.post(
        f"/api/v1/workspace/projects/{ASSIGNED_PROJECT_ID}",
        json={"runtime_image": "python:3.13-slim", "auto_start": True},
        headers=auth_headers(TEST_FREELANCER_TOKEN),
    )
    assert created.status_code == 201
    body = created.json()
    assert body["status"] == "RUNNING"
    assert body["file_count"] == 3
    assert body["storage_bytes"] > 0
    assert body["snapshot_sha256"]
    assert body["container_id"].startswith("mock-")

    # Duplicate active workspace on same project rejected with 409
    dup = await client.post(
        f"/api/v1/workspace/projects/{ASSIGNED_PROJECT_ID}",
        headers=auth_headers(TEST_FREELANCER_TOKEN),
    )
    assert dup.status_code == 409
    assert dup.json()["error"]["code"] == "WORKSPACE_ALREADY_EXISTS"

    # Quota limit enforced (max 3 active per user in test settings)
    p3 = uuid.UUID("12121212-1212-4121-8121-121212121212")
    p4 = uuid.UUID("34343434-3434-4343-8343-343434343434")
    for extra_id in (p3, p4):
        mock_project_state.projects[extra_id] = {
            "id": str(extra_id),
            "owner_id": str(OWNER_ID),
            "title": "Extra Project",
            "status": "ASSIGNED",
            "active_assignment": {
                "id": str(uuid.uuid4()),
                "freelancer_id": str(mock_project_state.projects[ASSIGNED_PROJECT_ID]["active_assignment"]["freelancer_id"]),
                "status": "ACTIVE",
            },
        }
        mock_github_state.connected_projects.add(extra_id)
    assert (
        await client.post(
            f"/api/v1/workspace/projects/{SECOND_ASSIGNED_PROJECT_ID}",
            headers=auth_headers(TEST_FREELANCER_TOKEN),
        )
    ).status_code == 201
    assert (
        await client.post(
            f"/api/v1/workspace/projects/{p3}",
            headers=auth_headers(TEST_FREELANCER_TOKEN),
        )
    ).status_code == 201
    quota_exceeded = await client.post(
        f"/api/v1/workspace/projects/{p4}",
        headers=auth_headers(TEST_FREELANCER_TOKEN),
    )
    assert quota_exceeded.status_code == 409
    assert quota_exceeded.json()["error"]["code"] == "WORKSPACE_QUOTA_EXCEEDED"
