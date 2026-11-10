"""Tests for POST /api/v1/projects/{project_id}/select-freelancer & DB concurrency safety."""

from __future__ import annotations

import asyncio
import uuid

import httpx
import pytest
from fastapi import FastAPI
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from app.models.assignment import Assignment, AssignmentStatus
from app.models.project import Project, ProjectStatus
from tests.conftest import (
    ADMIN_TOKEN,
    FREELANCER_2_ID,
    FREELANCER_ID,
    FREELANCER_PROFILE_ID,
    FREELANCER_TOKEN,
    MISSING_FREELANCER_ID,
    OWNER_2_TOKEN,
    OWNER_ID,
    OWNER_TOKEN,
    WRONG_ROLE_FREELANCER_ID,
    MockIdentityRouter,
    auth_headers,
    create_sample_project,
)


@pytest.mark.asyncio
async def test_select_freelancer_success_transitions_project_and_creates_assignment(
    client: httpx.AsyncClient,
    project_app: FastAPI,
) -> None:
    proj = await create_sample_project(client, token=OWNER_TOKEN)
    res = await client.post(
        f"/api/v1/projects/{proj['id']}/select-freelancer",
        headers=auth_headers(OWNER_TOKEN),
        json={"freelancer_id": str(FREELANCER_ID)},
    )
    assert res.status_code == 201, res.text
    body = res.json()
    assert body["project_id"] == proj["id"]
    assert body["freelancer_id"] == str(FREELANCER_ID)
    assert body["assigned_by"] == str(OWNER_ID)
    assert body["status"] == AssignmentStatus.ACTIVE.value
    assert body["project_status"] == ProjectStatus.ASSIGNED.value
    assert body["cancelled_at"] is None

    async with project_app.state.session_factory() as session:
        assignments = list((await session.execute(select(Assignment))).scalars().all())
        assert len(assignments) == 1
        assert assignments[0].freelancer_id == FREELANCER_ID
        project = (
            await session.execute(select(Project).where(Project.id == uuid.UUID(proj["id"])))
        ).scalar_one()
        assert project.status == ProjectStatus.ASSIGNED


@pytest.mark.asyncio
async def test_select_freelancer_blocks_owner_self_assignment_and_invalid_targets(
    client: httpx.AsyncClient,
    identity_mock: MockIdentityRouter,
) -> None:
    proj = await create_sample_project(client, token=OWNER_TOKEN)

    # Owner cannot assign themselves
    self_res = await client.post(
        f"/api/v1/projects/{proj['id']}/select-freelancer",
        headers=auth_headers(OWNER_TOKEN),
        json={"freelancer_id": str(OWNER_ID)},
    )
    assert self_res.status_code == 400
    assert self_res.json()["error"]["code"] == "INVALID_FREELANCER"

    # Profile ID instead of Identity User ID rejected cleanly
    profile_id_res = await client.post(
        f"/api/v1/projects/{proj['id']}/select-freelancer",
        headers=auth_headers(OWNER_TOKEN),
        json={"freelancer_id": str(FREELANCER_PROFILE_ID)},
    )
    assert profile_id_res.status_code == 400
    assert profile_id_res.json()["error"]["code"] == "INVALID_FREELANCER"

    # Target user returned by Identity with non-FREELANCER role rejected
    wrong_role_res = await client.post(
        f"/api/v1/projects/{proj['id']}/select-freelancer",
        headers=auth_headers(OWNER_TOKEN),
        json={"freelancer_id": str(WRONG_ROLE_FREELANCER_ID)},
    )
    assert wrong_role_res.status_code == 400
    assert wrong_role_res.json()["error"]["code"] == "INVALID_FREELANCER"

    # Inactive freelancer flag rejected
    identity_mock.freelancer_mode = "inactive_flag"
    inactive_res = await client.post(
        f"/api/v1/projects/{proj['id']}/select-freelancer",
        headers=auth_headers(OWNER_TOKEN),
        json={"freelancer_id": str(FREELANCER_ID)},
    )
    assert inactive_res.status_code == 400
    assert inactive_res.json()["error"]["code"] == "INVALID_FREELANCER"
    identity_mock.freelancer_mode = "normal"

    # Missing freelancer rejected 404
    missing_res = await client.post(
        f"/api/v1/projects/{proj['id']}/select-freelancer",
        headers=auth_headers(OWNER_TOKEN),
        json={"freelancer_id": str(MISSING_FREELANCER_ID)},
    )
    assert missing_res.status_code == 404
    assert missing_res.json()["error"]["code"] == "FREELANCER_NOT_FOUND"

    # Missing project rejected 404
    missing_proj = await client.post(
        f"/api/v1/projects/{uuid.uuid4()}/select-freelancer",
        headers=auth_headers(OWNER_TOKEN),
        json={"freelancer_id": str(FREELANCER_ID)},
    )
    assert missing_proj.status_code == 404
    assert missing_proj.json()["error"]["code"] == "PROJECT_NOT_FOUND"


@pytest.mark.asyncio
async def test_select_freelancer_rejects_duplicate_cancelled_and_unauthorized(
    client: httpx.AsyncClient,
) -> None:
    proj = await create_sample_project(client, token=OWNER_TOKEN)

    for token in (OWNER_2_TOKEN, FREELANCER_TOKEN, ADMIN_TOKEN):
        unauth_res = await client.post(
            f"/api/v1/projects/{proj['id']}/select-freelancer",
            headers=auth_headers(token),
            json={"freelancer_id": str(FREELANCER_ID)},
        )
        assert unauth_res.status_code == 403

    first = await client.post(
        f"/api/v1/projects/{proj['id']}/select-freelancer",
        headers=auth_headers(OWNER_TOKEN),
        json={"freelancer_id": str(FREELANCER_ID)},
    )
    assert first.status_code == 201

    second = await client.post(
        f"/api/v1/projects/{proj['id']}/select-freelancer",
        headers=auth_headers(OWNER_TOKEN),
        json={"freelancer_id": str(FREELANCER_2_ID)},
    )
    assert second.status_code == 409
    assert second.json()["error"]["code"] == "PROJECT_ALREADY_ASSIGNED"

    cancelled = await create_sample_project(client, token=OWNER_TOKEN, title="Cancelled")
    await client.post(
        f"/api/v1/projects/{cancelled['id']}/cancel",
        headers=auth_headers(OWNER_TOKEN),
    )
    assign_cancelled = await client.post(
        f"/api/v1/projects/{cancelled['id']}/select-freelancer",
        headers=auth_headers(OWNER_TOKEN),
        json={"freelancer_id": str(FREELANCER_ID)},
    )
    assert assign_cancelled.status_code == 409
    assert assign_cancelled.json()["error"]["code"] == "INVALID_PROJECT_STATUS"


@pytest.mark.asyncio
async def test_select_freelancer_concurrent_race_allows_only_one_assignment(
    client: httpx.AsyncClient,
    project_app: FastAPI,
    identity_mock: MockIdentityRouter,
) -> None:
    proj = await create_sample_project(client, token=OWNER_TOKEN)

    # Force both concurrent HTTP requests through preflight checks simultaneously before writing.
    arrivals = 0
    both_ready = asyncio.Event()

    async def barrier(_freelancer_id: uuid.UUID) -> None:
        nonlocal arrivals
        arrivals += 1
        if arrivals == 2:
            both_ready.set()
        await asyncio.wait_for(both_ready.wait(), timeout=2.0)

    identity_mock.before_freelancer_lookup = barrier

    res1, res2 = await asyncio.gather(
        client.post(
            f"/api/v1/projects/{proj['id']}/select-freelancer",
            headers=auth_headers(OWNER_TOKEN),
            json={"freelancer_id": str(FREELANCER_ID)},
        ),
        client.post(
            f"/api/v1/projects/{proj['id']}/select-freelancer",
            headers=auth_headers(OWNER_TOKEN),
            json={"freelancer_id": str(FREELANCER_2_ID)},
        ),
    )

    statuses = sorted([res1.status_code, res2.status_code])
    assert statuses == [201, 409], (res1.text, res2.text)

    failed_res = res1 if res1.status_code == 409 else res2
    assert failed_res.json()["error"]["code"] in {
        "CONCURRENT_ASSIGNMENT_CONFLICT",
        "PROJECT_ALREADY_ASSIGNED",
    }

    async with project_app.state.session_factory() as session:
        rows = list(
            (
                await session.execute(
                    select(Assignment).where(Assignment.project_id == uuid.UUID(proj["id"]))
                )
            )
            .scalars()
            .all()
        )
        assert len(rows) == 1


@pytest.mark.asyncio
async def test_partial_unique_index_enforces_single_active_assignment_per_project(
    client: httpx.AsyncClient,
    project_app: FastAPI,
) -> None:
    proj = await create_sample_project(client, token=OWNER_TOKEN)
    project_uuid = uuid.UUID(proj["id"])

    async with project_app.state.session_factory() as session:
        first_active = Assignment(
            project_id=project_uuid,
            freelancer_id=FREELANCER_ID,
            assigned_by=OWNER_ID,
            status=AssignmentStatus.ACTIVE,
        )
        session.add(first_active)
        await session.commit()

        # Attempting to insert a 2nd ACTIVE assignment violates partial unique index
        second_active = Assignment(
            project_id=project_uuid,
            freelancer_id=FREELANCER_2_ID,
            assigned_by=OWNER_ID,
            status=AssignmentStatus.ACTIVE,
        )
        session.add(second_active)
        with pytest.raises(IntegrityError):
            await session.commit()
        await session.rollback()

        # Cancel the first assignment -> now a new ACTIVE assignment MUST be allowed!
        first_active.status = AssignmentStatus.CANCELLED
        session.add(first_active)
        await session.flush()
        replacement_active = Assignment(
            project_id=project_uuid,
            freelancer_id=FREELANCER_2_ID,
            assigned_by=OWNER_ID,
            status=AssignmentStatus.ACTIVE,
        )
        session.add(replacement_active)
        await session.commit()

        all_rows = list(
            (
                await session.execute(
                    select(Assignment).where(Assignment.project_id == project_uuid)
                )
            )
            .scalars()
            .all()
        )
        assert len(all_rows) == 2


@pytest.mark.asyncio
async def test_select_freelancer_identity_unavailable_leaves_project_unmodified(
    client: httpx.AsyncClient,
    project_app: FastAPI,
    identity_mock: MockIdentityRouter,
) -> None:
    proj = await create_sample_project(client, token=OWNER_TOKEN)
    identity_mock.freelancer_mode = "timeout"

    res = await client.post(
        f"/api/v1/projects/{proj['id']}/select-freelancer",
        headers=auth_headers(OWNER_TOKEN),
        json={"freelancer_id": str(FREELANCER_ID)},
    )
    assert res.status_code == 503
    assert res.json()["error"]["code"] == "IDENTITY_SERVICE_UNAVAILABLE"

    identity_mock.freelancer_mode = "normal"
    async with project_app.state.session_factory() as session:
        row = (
            await session.execute(select(Project).where(Project.id == uuid.UUID(proj["id"])))
        ).scalar_one()
        assert row.status == ProjectStatus.OPEN
        assignments = list((await session.execute(select(Assignment))).scalars().all())
        assert len(assignments) == 0


@pytest.mark.asyncio
async def test_select_freelancer_maps_commit_integrity_error_to_409_conflict(
    client: httpx.AsyncClient,
    project_app: FastAPI,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from sqlalchemy.ext.asyncio import AsyncSession

    proj = await create_sample_project(client, token=OWNER_TOKEN)
    original_commit = AsyncSession.commit
    call_count = 0

    async def fail_once_on_assignment_commit(self: AsyncSession) -> None:
        nonlocal call_count
        call_count += 1
        raise IntegrityError("INSERT INTO assignments ...", {}, Exception("duplicate key uq_assignments_one_active_per_project"))

    monkeypatch.setattr(AsyncSession, "commit", fail_once_on_assignment_commit)
    res = await client.post(
        f"/api/v1/projects/{proj['id']}/select-freelancer",
        headers=auth_headers(OWNER_TOKEN),
        json={"freelancer_id": str(FREELANCER_ID)},
    )
    monkeypatch.setattr(AsyncSession, "commit", original_commit)

    assert call_count == 1
    assert res.status_code == 409
    assert res.json()["error"]["code"] == "CONCURRENT_ASSIGNMENT_CONFLICT"

    async with project_app.state.session_factory() as session:
        row = (
            await session.execute(select(Project).where(Project.id == uuid.UUID(proj["id"])))
        ).scalar_one()
        assert row.status == ProjectStatus.OPEN
        assert len(list((await session.execute(select(Assignment))).scalars().all())) == 0

