"""Tests for GET /api/v1/projects/{project_id} access control and not-found handling."""

from __future__ import annotations

import uuid

import httpx
import pytest

from tests.conftest import (
    ADMIN_TOKEN,
    FREELANCER_2_TOKEN,
    FREELANCER_ID,
    FREELANCER_TOKEN,
    OWNER_2_TOKEN,
    OWNER_TOKEN,
    auth_headers,
    create_sample_project,
)


@pytest.mark.asyncio
async def test_get_project_owner_vs_other_owner_and_admin(client: httpx.AsyncClient) -> None:
    proj = await create_sample_project(client, token=OWNER_TOKEN)

    own_res = await client.get(f"/api/v1/projects/{proj['id']}", headers=auth_headers(OWNER_TOKEN))
    assert own_res.status_code == 200
    assert own_res.json()["id"] == proj["id"]

    other_owner_res = await client.get(
        f"/api/v1/projects/{proj['id']}",
        headers=auth_headers(OWNER_2_TOKEN),
    )
    assert other_owner_res.status_code == 403
    assert other_owner_res.json()["error"]["code"] == "FORBIDDEN"

    admin_res = await client.get(
        f"/api/v1/projects/{proj['id']}",
        headers=auth_headers(ADMIN_TOKEN),
    )
    assert admin_res.status_code == 200
    assert admin_res.json()["id"] == proj["id"]


@pytest.mark.asyncio
async def test_get_project_freelancer_open_assigned_and_cancelled_visibility(
    client: httpx.AsyncClient,
) -> None:
    open_proj = await create_sample_project(client, token=OWNER_TOKEN, title="Open For Discovery")
    assigned_proj = await create_sample_project(client, token=OWNER_TOKEN, title="Assigned Task")
    cancelled_proj = await create_sample_project(client, token=OWNER_TOKEN, title="Cancelled Task")

    # Any freelancer can view OPEN project details
    res_open = await client.get(
        f"/api/v1/projects/{open_proj['id']}",
        headers=auth_headers(FREELANCER_2_TOKEN),
    )
    assert res_open.status_code == 200
    assert res_open.json()["active_assignment"] is None

    # Assign assigned_proj to FREELANCER_ID
    assign_res = await client.post(
        f"/api/v1/projects/{assigned_proj['id']}/select-freelancer",
        headers=auth_headers(OWNER_TOKEN),
        json={"freelancer_id": str(FREELANCER_ID)},
    )
    assert assign_res.status_code == 201

    # Assigned freelancer can view ASSIGNED project with active_assignment summary
    f1_assigned = await client.get(
        f"/api/v1/projects/{assigned_proj['id']}",
        headers=auth_headers(FREELANCER_TOKEN),
    )
    assert f1_assigned.status_code == 200
    assert f1_assigned.json()["active_assignment"]["freelancer_id"] == str(FREELANCER_ID)

    # Unassigned freelancer cannot view ASSIGNED project
    f2_assigned = await client.get(
        f"/api/v1/projects/{assigned_proj['id']}",
        headers=auth_headers(FREELANCER_2_TOKEN),
    )
    assert f2_assigned.status_code == 403
    assert f2_assigned.json()["error"]["code"] == "FORBIDDEN"

    # Cancel cancelled_proj -> freelancers cannot view CANCELLED project
    await client.post(
        f"/api/v1/projects/{cancelled_proj['id']}/cancel",
        headers=auth_headers(OWNER_TOKEN),
    )
    f1_cancelled = await client.get(
        f"/api/v1/projects/{cancelled_proj['id']}",
        headers=auth_headers(FREELANCER_TOKEN),
    )
    assert f1_cancelled.status_code == 403
    assert f1_cancelled.json()["error"]["code"] == "FORBIDDEN"


@pytest.mark.asyncio
async def test_get_project_not_found_and_invalid_uuid(client: httpx.AsyncClient) -> None:
    missing = await client.get(
        f"/api/v1/projects/{uuid.uuid4()}",
        headers=auth_headers(OWNER_TOKEN),
    )
    assert missing.status_code == 404
    assert missing.json()["error"]["code"] == "PROJECT_NOT_FOUND"

    malformed = await client.get(
        "/api/v1/projects/not-a-uuid",
        headers=auth_headers(OWNER_TOKEN),
    )
    assert malformed.status_code == 422
    assert malformed.json()["error"]["code"] == "VALIDATION_ERROR"

