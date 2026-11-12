"""Tests for Docker runtime adapter, failure rollback, and /health + /ready endpoints."""

from __future__ import annotations

from pathlib import Path

import pytest
from httpx import AsyncClient

from app.services.docker_adapter import MockDockerAdapter
from tests.conftest import (
    ASSIGNED_PROJECT_ID,
    TEST_FREELANCER_TOKEN,
    auth_headers,
)


@pytest.mark.asyncio
async def test_docker_failure_rolls_back_storage_and_updates_readiness(
    client: AsyncClient,
    mock_docker_adapter: MockDockerAdapter,
    tmp_path: Path,
) -> None:
    health = await client.get("/health")
    assert health.status_code == 200
    assert health.json()["status"] == "ok"

    ready = await client.get("/ready")
    assert ready.status_code == 200
    assert ready.json()["status"] == "ready"
    assert ready.json()["docker_runtime"] == "ok"

    # Inject container creation failure -> directory cleaned up, workspace marked FAILED, re-provision allowed
    mock_docker_adapter.fail_on_create = True
    failed = await client.post(
        f"/api/v1/workspace/projects/{ASSIGNED_PROJECT_ID}",
        headers=auth_headers(TEST_FREELANCER_TOKEN),
    )
    assert failed.status_code == 502
    assert failed.json()["error"]["code"] == "DOCKER_RUNTIME_ERROR"
    assert len(mock_docker_adapter.containers) == 0

    storage_root = tmp_path / "workspaces"
    if storage_root.exists():
        assert list(storage_root.iterdir()) == []

    # Inject start failure after create -> newly created container must be removed cleanly
    mock_docker_adapter.fail_on_create = False
    mock_docker_adapter.fail_on_start = True
    failed_start = await client.post(
        f"/api/v1/workspace/projects/{ASSIGNED_PROJECT_ID}",
        json={"auto_start": True},
        headers=auth_headers(TEST_FREELANCER_TOKEN),
    )
    assert failed_start.status_code == 502
    assert len(mock_docker_adapter.containers) == 0
    assert ("remove", mock_docker_adapter.operations[-1][1]) == mock_docker_adapter.operations[-1]

    # Readiness turns 503 when Docker daemon stops responding
    mock_docker_adapter.fail_on_ping = True
    degraded = await client.get("/ready")
    assert degraded.status_code == 503
    assert degraded.json()["docker_runtime"] == "error"

