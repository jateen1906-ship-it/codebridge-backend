"""Tests for PATCH /api/v1/projects/{project_id} partial updates and restrictions."""

from __future__ import annotations

import uuid

import httpx
import pytest
from fastapi import FastAPI
from sqlalchemy import select
from sqlalchemy.orm import selectinload

from app.models.project import Project
from tests.conftest import (
    ADMIN_TOKEN,
    FREELANCER_ID,
    FREELANCER_TOKEN,
    OWNER_2_TOKEN,
    OWNER_ID,
    OWNER_TOKEN,
    auth_headers,
    create_sample_project,
    future_deadline,
)


@pytest.mark.asyncio
async def test_update_project_success_updates_project_and_requirement(
    client: httpx.AsyncClient,
    project_app: FastAPI,
) -> None:
    proj = await create_sample_project(client, token=OWNER_TOKEN)
    new_deadline = future_deadline(45)

    patch_res = await client.patch(
        f"/api/v1/projects/{proj['id']}",
        headers=auth_headers(OWNER_TOKEN),
        json={
            "title": "Updated Stripe Callback Fix",
            "expected_result": "Updated webhook verification with replay guard.",
            "budget": "7250.50",
            "currency": "usd",
            "deadline": new_deadline,
            "required_skills": ["Python", "Stripe", "python"],
        },
    )
    assert patch_res.status_code == 200, patch_res.text
    body = patch_res.json()
    assert body["title"] == "Updated Stripe Callback Fix"
    assert body["description"] == proj["description"]
    assert body["expected_result"] == "Updated webhook verification with replay guard."
    assert body["budget"] == "7250.50"
    assert body["currency"] == "USD"
    assert body["deadline"] == new_deadline
    assert body["required_skills"] == ["Python", "Stripe"]
    assert body["updated_at"] >= proj["updated_at"]

    async with project_app.state.session_factory() as session:
        stmt = select(Project).options(selectinload(Project.requirement)).where(
            Project.id == uuid.UUID(proj["id"])
        )
        row = (await session.execute(stmt)).scalar_one()
        assert row.owner_id == OWNER_ID
        assert row.requirement is not None
        assert row.requirement.expected_result == "Updated webhook verification with replay guard."
        assert row.requirement.required_skills == ["Python", "Stripe"]


@pytest.mark.asyncio
async def test_update_project_rbac_and_missing_project(client: httpx.AsyncClient) -> None:
    proj = await create_sample_project(client, token=OWNER_TOKEN)

    for token in (OWNER_2_TOKEN, FREELANCER_TOKEN, ADMIN_TOKEN):
        res = await client.patch(
            f"/api/v1/projects/{proj['id']}",
            headers=auth_headers(token),
            json={"title": "Unauthorized Edit"},
        )
        assert res.status_code == 403
        assert res.json()["error"]["code"] == "FORBIDDEN"

    missing = await client.patch(
        f"/api/v1/projects/{uuid.uuid4()}",
        headers=auth_headers(OWNER_TOKEN),
        json={"title": "Missing Project"},
    )
    assert missing.status_code == 404
    assert missing.json()["error"]["code"] == "PROJECT_NOT_FOUND"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "bad_patch",
    [
        {},
        {"title": None},
        {"budget": None},
        {"status": "COMPLETED"},
        {"status": "CANCELLED"},
        {"owner_id": str(uuid.uuid4())},
        {"id": str(uuid.uuid4())},
        {"published_at": "2026-12-01T00:00:00Z"},
        {"closed_at": "2026-12-01T00:00:00Z"},
    ],
)
async def test_update_project_rejects_forbidden_fields_and_empty_or_null_patch(
    client: httpx.AsyncClient,
    bad_patch: dict[str, object],
) -> None:
    proj = await create_sample_project(client, token=OWNER_TOKEN)
    res = await client.patch(
        f"/api/v1/projects/{proj['id']}",
        headers=auth_headers(OWNER_TOKEN),
        json=bad_patch,
    )
    assert res.status_code == 422, res.text
    assert res.json()["error"]["code"] == "VALIDATION_ERROR"

    # Confirm DB entity remained completely untouched
    check = await client.get(f"/api/v1/projects/{proj['id']}", headers=auth_headers(OWNER_TOKEN))
    assert check.json()["title"] == proj["title"]
    assert check.json()["owner_id"] == str(OWNER_ID)
    assert check.json()["status"] == "OPEN"


@pytest.mark.asyncio
async def test_update_project_rejected_after_assignment_or_cancellation(
    client: httpx.AsyncClient,
) -> None:
    assigned = await create_sample_project(client, token=OWNER_TOKEN, title="Will Be Assigned")
    await client.post(
        f"/api/v1/projects/{assigned['id']}/select-freelancer",
        headers=auth_headers(OWNER_TOKEN),
        json={"freelancer_id": str(FREELANCER_ID)},
    )
    res_assigned = await client.patch(
        f"/api/v1/projects/{assigned['id']}",
        headers=auth_headers(OWNER_TOKEN),
        json={"title": "Cannot Modify Assigned"},
    )
    assert res_assigned.status_code == 409
    assert res_assigned.json()["error"]["code"] == "INVALID_PROJECT_STATUS"

    cancelled = await create_sample_project(client, token=OWNER_TOKEN, title="Will Be Cancelled")
    await client.post(
        f"/api/v1/projects/{cancelled['id']}/cancel",
        headers=auth_headers(OWNER_TOKEN),
    )
    res_cancelled = await client.patch(
        f"/api/v1/projects/{cancelled['id']}",
        headers=auth_headers(OWNER_TOKEN),
        json={"title": "Cannot Modify Cancelled"},
    )
    assert res_cancelled.status_code == 409
    assert res_cancelled.json()["error"]["code"] == "INVALID_PROJECT_STATUS"

