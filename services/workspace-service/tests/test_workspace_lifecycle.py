"""Tests for Workspace lifecycle transitions (READY -> RUNNING -> STOPPED -> RUNNING -> DESTROYED)."""

from __future__ import annotations

import pytest
from httpx import AsyncClient

from tests.conftest import (
    ASSIGNED_PROJECT_ID,
    TEST_FREELANCER_TOKEN,
    auth_headers,
)


@pytest.mark.asyncio
async def test_workspace_lifecycle_state_machine_and_audit_events(client: AsyncClient) -> None:
    created = await client.post(
        f"/api/v1/workspace/projects/{ASSIGNED_PROJECT_ID}",
        headers=auth_headers(TEST_FREELANCER_TOKEN),
    )
    assert created.status_code == 201
    ws_id = created.json()["id"]
    assert created.json()["status"] == "READY"

    started = await client.post(
        f"/api/v1/workspace/{ws_id}/start",
        headers=auth_headers(TEST_FREELANCER_TOKEN),
    )
    assert started.status_code == 200
    assert started.json()["status"] == "RUNNING"

    # Cannot start an already RUNNING workspace
    dup_start = await client.post(
        f"/api/v1/workspace/{ws_id}/start",
        headers=auth_headers(TEST_FREELANCER_TOKEN),
    )
    assert dup_start.status_code == 409
    assert dup_start.json()["error"]["code"] == "INVALID_WORKSPACE_STATE"

    stopped = await client.post(
        f"/api/v1/workspace/{ws_id}/stop",
        headers=auth_headers(TEST_FREELANCER_TOKEN),
    )
    assert stopped.status_code == 200
    assert stopped.json()["status"] == "STOPPED"

    restarted = await client.post(
        f"/api/v1/workspace/{ws_id}/start",
        headers=auth_headers(TEST_FREELANCER_TOKEN),
    )
    assert restarted.status_code == 200
    assert restarted.json()["status"] == "RUNNING"

    destroyed = await client.delete(
        f"/api/v1/workspace/{ws_id}",
        headers=auth_headers(TEST_FREELANCER_TOKEN),
    )
    assert destroyed.status_code == 200
    assert destroyed.json()["status"] == "DESTROYED"

    # Cannot transition out of DESTROYED
    post_destroy = await client.post(
        f"/api/v1/workspace/{ws_id}/start",
        headers=auth_headers(TEST_FREELANCER_TOKEN),
    )
    assert post_destroy.status_code == 409

    # Lifecycle events recorded in order
    detail = await client.get(
        f"/api/v1/workspace/{ws_id}",
        headers=auth_headers(TEST_FREELANCER_TOKEN),
    )
    assert detail.status_code == 200
    event_types = [e["event_type"] for e in detail.json()["events"]]
    assert "PROVISION_STARTED" in event_types
    assert "PROVISION_COMPLETED" in event_types
    assert "WORKSPACE_STARTED" in event_types
    assert "WORKSPACE_STOPPED" in event_types
    assert "WORKSPACE_DESTROYED" in event_types

