"""Tests for manual workspace destruction and TTL expiration cleanup."""

from __future__ import annotations

import uuid
from datetime import timedelta
from pathlib import Path

import pytest
from fastapi import FastAPI
from httpx import AsyncClient
from sqlalchemy import update

from app.models.base import utc_now
from app.models.workspace import Workspace
from app.services.docker_adapter import MockDockerAdapter
from tests.conftest import (
    ASSIGNED_PROJECT_ID,
    TEST_ADMIN_TOKEN,
    TEST_FREELANCER_TOKEN,
    auth_headers,
)


@pytest.mark.asyncio
async def test_destroy_and_expired_cleanup_remove_containers_and_directories(
    client: AsyncClient,
    workspace_app: FastAPI,
    mock_docker_adapter: MockDockerAdapter,
) -> None:
    created = await client.post(
        f"/api/v1/workspace/projects/{ASSIGNED_PROJECT_ID}",
        headers=auth_headers(TEST_FREELANCER_TOKEN),
    )
    assert created.status_code == 201
    ws_id = created.json()["id"]
    cid = created.json()["container_id"]

    # Retrieve disk path via DB and confirm extracted files exist
    async with workspace_app.state.session_factory() as session:
        ws = await session.get(Workspace, uuid.UUID(ws_id))
        assert ws is not None
        ws_dir = Path(ws.workspace_path)
    assert ws_dir.exists()
    assert cid in mock_docker_adapter.containers

    # Backdate expires_at so cleanup-expired expires & removes container and disk folder
    async with workspace_app.state.session_factory() as session:
        await session.execute(
            update(Workspace)
            .where(Workspace.id == uuid.UUID(ws_id))
            .values(expires_at=utc_now() - timedelta(hours=2))
        )
        await session.commit()

    # Non-owner freelancer cannot run global cleanup
    assert (
        await client.post(
            "/api/v1/workspace/cleanup-expired",
            headers=auth_headers(TEST_FREELANCER_TOKEN),
        )
    ).status_code == 403

    cleanup = await client.post(
        "/api/v1/workspace/cleanup-expired",
        headers=auth_headers(TEST_ADMIN_TOKEN),
    )
    assert cleanup.status_code == 200
    assert cleanup.json()["expired_count"] == 1
    assert ws_id in cleanup.json()["destroyed_workspace_ids"]
    assert cid not in mock_docker_adapter.containers
    assert not ws_dir.exists()

