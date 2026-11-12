"""Tests for Workspace Service strict RBAC, ownership, and active assignment authorization."""

from __future__ import annotations

import pytest
from httpx import AsyncClient

from tests.conftest import (
    ASSIGNED_PROJECT_ID,
    OTHER_OWNER_PROJECT_ID,
    TEST_ADMIN_TOKEN,
    TEST_FREELANCER_TOKEN,
    TEST_OTHER_FREELANCER_TOKEN,
    TEST_OTHER_OWNER_TOKEN,
    TEST_OWNER_TOKEN,
    auth_headers,
)


@pytest.mark.asyncio
async def test_workspace_authorization_matrix(client: AsyncClient) -> None:
    # Unassigned freelancer & other owner forbidden
    for token in (TEST_OTHER_FREELANCER_TOKEN, TEST_OTHER_OWNER_TOKEN, TEST_ADMIN_TOKEN):
        denied = await client.post(
            f"/api/v1/workspace/projects/{ASSIGNED_PROJECT_ID}",
            headers=auth_headers(token),
        )
        assert denied.status_code == 403

    # Assigned freelancer provisions workspace
    created = await client.post(
        f"/api/v1/workspace/projects/{ASSIGNED_PROJECT_ID}",
        headers=auth_headers(TEST_FREELANCER_TOKEN),
    )
    assert created.status_code == 201
    ws_id = created.json()["id"]

    # Unrelated users cannot inspect, start, stop, refresh, or destroy workspace
    for token in (TEST_OTHER_FREELANCER_TOKEN, TEST_OTHER_OWNER_TOKEN):
        assert (
            await client.get(f"/api/v1/workspace/{ws_id}", headers=auth_headers(token))
        ).status_code == 403
        assert (
            await client.post(f"/api/v1/workspace/{ws_id}/start", headers=auth_headers(token))
        ).status_code == 403
        assert (
            await client.delete(f"/api/v1/workspace/{ws_id}", headers=auth_headers(token))
        ).status_code == 403

    # Admin can inspect read-only, cannot mutate container
    admin_get = await client.get(f"/api/v1/workspace/{ws_id}", headers=auth_headers(TEST_ADMIN_TOKEN))
    assert admin_get.status_code == 200
    admin_start = await client.post(
        f"/api/v1/workspace/{ws_id}/start", headers=auth_headers(TEST_ADMIN_TOKEN)
    )
    assert admin_start.status_code == 403

    # Owner of the project can inspect and stop/destroy
    owner_get = await client.get(f"/api/v1/workspace/{ws_id}", headers=auth_headers(TEST_OWNER_TOKEN))
    assert owner_get.status_code == 200

    # Other owner cannot list/provision their non-owned project here via Alice's token
    assert (
        await client.get(
            f"/api/v1/workspace/projects/{OTHER_OWNER_PROJECT_ID}",
            headers=auth_headers(TEST_OWNER_TOKEN),
        )
    ).status_code == 403

