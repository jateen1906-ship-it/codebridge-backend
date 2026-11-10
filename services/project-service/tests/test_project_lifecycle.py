"""Tests for project publish, cancel, and central state transition machine."""

from __future__ import annotations

import uuid
from datetime import date, timedelta

import httpx
import pytest
from fastapi import FastAPI
from sqlalchemy import update

from app.core.exceptions import InvalidProjectStatusError, ValidationError
from app.models.project import Project, ProjectStatus
from app.models.project_requirement import ProjectRequirement
from app.services.project_state_service import ProjectStateService
from tests.conftest import (
    ADMIN_TOKEN,
    FREELANCER_ID,
    FREELANCER_TOKEN,
    OWNER_2_TOKEN,
    OWNER_ID,
    OWNER_TOKEN,
    auth_headers,
    create_sample_project,
)


@pytest.mark.asyncio
async def test_publish_project_sets_published_at_and_blocks_duplicate_publish(
    client: httpx.AsyncClient,
) -> None:
    proj = await create_sample_project(client, token=OWNER_TOKEN)
    assert proj["status"] == "OPEN"
    assert proj["published_at"] is None

    pub_res = await client.post(
        f"/api/v1/projects/{proj['id']}/publish",
        headers=auth_headers(OWNER_TOKEN),
    )
    assert pub_res.status_code == 200, pub_res.text
    published = pub_res.json()
    assert published["status"] == "OPEN"
    assert published["published_at"] is not None

    dup_res = await client.post(
        f"/api/v1/projects/{proj['id']}/publish",
        headers=auth_headers(OWNER_TOKEN),
    )
    assert dup_res.status_code == 409
    assert dup_res.json()["error"]["code"] == "INVALID_PROJECT_STATUS"


@pytest.mark.asyncio
async def test_publish_project_rbac_status_and_completeness_checks(
    client: httpx.AsyncClient,
    project_app: FastAPI,
) -> None:
    proj = await create_sample_project(client, token=OWNER_TOKEN)

    for token in (OWNER_2_TOKEN, FREELANCER_TOKEN, ADMIN_TOKEN):
        res = await client.post(
            f"/api/v1/projects/{proj['id']}/publish",
            headers=auth_headers(token),
        )
        assert res.status_code == 403

    missing = await client.post(
        f"/api/v1/projects/{uuid.uuid4()}/publish",
        headers=auth_headers(OWNER_TOKEN),
    )
    assert missing.status_code == 404
    assert missing.json()["error"]["code"] == "PROJECT_NOT_FOUND"

    # Assigned project cannot be published
    assigned = await create_sample_project(client, token=OWNER_TOKEN, title="Assigned Proj")
    await client.post(
        f"/api/v1/projects/{assigned['id']}/select-freelancer",
        headers=auth_headers(OWNER_TOKEN),
        json={"freelancer_id": str(FREELANCER_ID)},
    )
    pub_assigned = await client.post(
        f"/api/v1/projects/{assigned['id']}/publish",
        headers=auth_headers(OWNER_TOKEN),
    )
    assert pub_assigned.status_code == 409
    assert pub_assigned.json()["error"]["code"] == "INVALID_PROJECT_STATUS"

    # Cancelled project cannot be published
    cancelled = await create_sample_project(client, token=OWNER_TOKEN, title="Cancelled Proj")
    await client.post(
        f"/api/v1/projects/{cancelled['id']}/cancel",
        headers=auth_headers(OWNER_TOKEN),
    )
    pub_cancelled = await client.post(
        f"/api/v1/projects/{cancelled['id']}/publish",
        headers=auth_headers(OWNER_TOKEN),
    )
    assert pub_cancelled.status_code == 409
    assert pub_cancelled.json()["error"]["code"] == "INVALID_PROJECT_STATUS"

    # Incomplete project requirement fails completeness guard
    async with project_app.state.session_factory() as session:
        await session.execute(
            update(ProjectRequirement)
            .where(ProjectRequirement.project_id == uuid.UUID(proj["id"]))
            .values(expected_result="   ")
        )
        await session.commit()

    pub_incomplete = await client.post(
        f"/api/v1/projects/{proj['id']}/publish",
        headers=auth_headers(OWNER_TOKEN),
    )
    assert pub_incomplete.status_code == 422
    assert pub_incomplete.json()["error"]["code"] == "VALIDATION_ERROR"


@pytest.mark.asyncio
async def test_cancel_project_unassigned_open_and_rejects_assigned_or_re_cancel(
    client: httpx.AsyncClient,
) -> None:
    proj = await create_sample_project(client, token=OWNER_TOKEN)

    for token in (OWNER_2_TOKEN, FREELANCER_TOKEN, ADMIN_TOKEN):
        res = await client.post(
            f"/api/v1/projects/{proj['id']}/cancel",
            headers=auth_headers(token),
        )
        assert res.status_code == 403

    cancel_res = await client.post(
        f"/api/v1/projects/{proj['id']}/cancel",
        headers=auth_headers(OWNER_TOKEN),
    )
    assert cancel_res.status_code == 200
    body = cancel_res.json()
    assert body["status"] == "CANCELLED"
    assert body["closed_at"] is not None

    # Repeat cancel rejected
    repeat_res = await client.post(
        f"/api/v1/projects/{proj['id']}/cancel",
        headers=auth_headers(OWNER_TOKEN),
    )
    assert repeat_res.status_code == 409
    assert repeat_res.json()["error"]["code"] == "INVALID_PROJECT_STATUS"

    # Assigned project cannot be cancelled
    assigned = await create_sample_project(client, token=OWNER_TOKEN, title="Assigned Lock")
    await client.post(
        f"/api/v1/projects/{assigned['id']}/select-freelancer",
        headers=auth_headers(OWNER_TOKEN),
        json={"freelancer_id": str(FREELANCER_ID)},
    )
    cancel_assigned = await client.post(
        f"/api/v1/projects/{assigned['id']}/cancel",
        headers=auth_headers(OWNER_TOKEN),
    )
    assert cancel_assigned.status_code == 409
    assert cancel_assigned.json()["error"]["code"] == "INVALID_PROJECT_STATUS"


@pytest.mark.asyncio
async def test_central_state_machine_enforces_valid_transitions_and_review_guard(
    client: httpx.AsyncClient,
) -> None:
    # Valid lifecycle transitions
    ProjectStateService.validate_transition(ProjectStatus.OPEN, ProjectStatus.ASSIGNED)
    ProjectStateService.validate_transition(ProjectStatus.OPEN, ProjectStatus.CANCELLED)
    ProjectStateService.validate_transition(ProjectStatus.ASSIGNED, ProjectStatus.IN_PROGRESS)
    ProjectStateService.validate_transition(ProjectStatus.IN_PROGRESS, ProjectStatus.CHANGES_REQUESTED)
    ProjectStateService.validate_transition(ProjectStatus.CHANGES_REQUESTED, ProjectStatus.IN_PROGRESS)
    ProjectStateService.validate_transition(ProjectStatus.IN_PROGRESS, ProjectStatus.COMPLETED)

    # Disallowed jumps
    invalid_pairs = [
        (ProjectStatus.OPEN, ProjectStatus.COMPLETED),
        (ProjectStatus.OPEN, ProjectStatus.IN_PROGRESS),
        (ProjectStatus.ASSIGNED, ProjectStatus.COMPLETED),
        (ProjectStatus.ASSIGNED, ProjectStatus.CANCELLED),
        (ProjectStatus.CANCELLED, ProjectStatus.OPEN),
        (ProjectStatus.COMPLETED, ProjectStatus.OPEN),
    ]
    for source, target in invalid_pairs:
        with pytest.raises(InvalidProjectStatusError):
            ProjectStateService.validate_transition(source, target)

    # Service-level hook guards COMPLETED behind verified review approval
    project = Project(
        owner_id=OWNER_ID,
        title="Hook verification",
        description="Verify state hooks",
        status=ProjectStatus.ASSIGNED,
        budget=100,
        currency="INR",
        deadline=date.today() + timedelta(days=5),
    )
    ProjectStateService.advance_to_in_progress(project)
    assert project.status == ProjectStatus.IN_PROGRESS

    with pytest.raises(InvalidProjectStatusError):
        ProjectStateService.complete_project(project, approval_verified=False)
    assert project.status == ProjectStatus.IN_PROGRESS

    ProjectStateService.request_changes(project)
    assert project.status == ProjectStatus.CHANGES_REQUESTED
    ProjectStateService.advance_to_in_progress(project)
    ProjectStateService.complete_project(project, approval_verified=True)
    assert project.status == ProjectStatus.COMPLETED
    assert project.closed_at is not None

    # Validate publish past-deadline guard
    stale_project = Project(
        owner_id=OWNER_ID,
        title="Past Deadline",
        description="Valid description text",
        status=ProjectStatus.OPEN,
        budget=100,
        currency="INR",
        deadline=date.today() - timedelta(days=1),
    )
    with pytest.raises(ValidationError):
        ProjectStateService.validate_publish(stale_project)

    # Verify OpenAPI exposes no endpoint mutating IN_PROGRESS or COMPLETED directly
    openapi = (await client.get("/openapi.json")).json()
    paths = set(openapi["paths"].keys())
    assert "/api/v1/projects/{project_id}/complete" not in paths
    assert "/api/v1/projects/{project_id}/start" not in paths

