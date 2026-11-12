"""Tests for refreshing repository snapshot inside an existing workspace."""

from __future__ import annotations

import uuid
from pathlib import Path

import pytest
from fastapi import FastAPI
from httpx import AsyncClient

from app.models.workspace import Workspace
from tests.conftest import (
    ASSIGNED_PROJECT_ID,
    TEST_FREELANCER_TOKEN,
    MockGitHubServiceState,
    auth_headers,
    build_sample_repo_zip,
)


@pytest.mark.asyncio
async def test_refresh_workspace_replaces_files_and_updates_sha(
    client: AsyncClient,
    workspace_app: FastAPI,
    mock_github_state: MockGitHubServiceState,
) -> None:
    created = await client.post(
        f"/api/v1/workspace/projects/{ASSIGNED_PROJECT_ID}",
        headers=auth_headers(TEST_FREELANCER_TOKEN),
    )
    assert created.status_code == 201
    ws_id = created.json()["id"]
    old_sha = created.json()["snapshot_sha256"]

    async with workspace_app.state.session_factory() as session:
        ws = await session.get(Workspace, uuid.UUID(ws_id))
        assert ws is not None
        ws_dir = Path(ws.workspace_path)
    assert (ws_dir / "app" / "main.py").exists()

    # Update mock repository archive and refresh onto another ref
    mock_github_state.archive_bytes = build_sample_repo_zip(
        {
            "alice-sample-api-newsha/README.md": "# Updated\n",
            "alice-sample-api-newsha/src/feature.py": "def new_feature():\n    return True\n",
        }
    )
    refreshed = await client.post(
        f"/api/v1/workspace/{ws_id}/refresh",
        json={"ref": "feat/fix-callback"},
        headers=auth_headers(TEST_FREELANCER_TOKEN),
    )
    assert refreshed.status_code == 200
    body = refreshed.json()
    assert body["repository_ref"] == "feat/fix-callback"
    assert body["snapshot_sha256"] != old_sha
    assert body["file_count"] == 2
    assert body["last_refreshed_at"] is not None

    # Stale old files removed, new files extracted cleanly
    assert not (ws_dir / "app" / "main.py").exists()
    assert (ws_dir / "src" / "feature.py").exists()

