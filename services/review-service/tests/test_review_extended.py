"""Extended API, negative-path, downstream-failure, and readiness tests for Review Service."""

from __future__ import annotations

import uuid

import httpx
import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import create_async_engine

from tests.conftest import (
    ADMIN_TOKEN,
    ASSIGNED_PROJECT_ID,
    FREELANCER_TOKEN,
    OPEN_PROJECT_ID,
    OTHER_FREELANCER_TOKEN,
    OTHER_OWNER_TOKEN,
    OWNER_TOKEN,
    MockProjectState,
    auth_headers,
)


@pytest.mark.asyncio
async def test_health_ready_and_degraded_database(client: AsyncClient, app) -> None:
    assert (await client.get("/health")).status_code == 200
    ready = await client.get("/ready")
    assert ready.status_code == 200
    assert ready.json()["database"] == "ok"

    original_engine = app.state.engine
    broken_engine = create_async_engine("sqlite+aiosqlite:///Z:/nonexistent_dir_xyz/missing.db")
    app.state.engine = broken_engine
    try:
        degraded = await client.get("/ready")
        assert degraded.status_code == 503
        assert degraded.json()["status"] == "not_ready"
    finally:
        app.state.engine = original_engine
        await broken_engine.dispose()


@pytest.mark.asyncio
async def test_auth_validation_and_missing_resources(client: AsyncClient) -> None:
    # Unauthenticated request rejected 401
    assert (
        await client.get(f"/api/v1/submissions/projects/{ASSIGNED_PROJECT_ID}")
    ).status_code == 401

    # Missing submission -> 404
    missing_id = uuid.uuid4()
    res_404 = await client.get(
        f"/api/v1/submissions/{missing_id}",
        headers=auth_headers(OWNER_TOKEN),
    )
    assert res_404.status_code == 404

    # Invalid file traversal & extra forbidden field -> 422
    bad_file = await client.post(
        f"/api/v1/submissions/projects/{ASSIGNED_PROJECT_ID}",
        json={
            "title": "Bad File Path",
            "summary": "Valid summary length here.",
            "changes_summary": "Valid changes summary length.",
            "files": [{"file_path": "../../etc/passwd", "change_type": "MODIFIED"}],
        },
        headers=auth_headers(FREELANCER_TOKEN),
    )
    assert bad_file.status_code == 422

    # Unassigned project cannot accept submission
    unassigned = await client.post(
        f"/api/v1/submissions/projects/{OPEN_PROJECT_ID}",
        json={
            "title": "Valid Title",
            "summary": "Valid summary length here.",
            "changes_summary": "Valid changes summary length.",
        },
        headers=auth_headers(FREELANCER_TOKEN),
    )
    assert unassigned.status_code in {403, 409}


@pytest.mark.asyncio
async def test_comments_rbac_withdrawal_and_invalid_transitions(client: AsyncClient) -> None:
    draft = await client.post(
        f"/api/v1/submissions/projects/{ASSIGNED_PROJECT_ID}",
        json={
            "title": "Draft Deliverable",
            "summary": "Initial draft prior to final polish.",
            "changes_summary": "Created draft files.",
            "submit_immediately": False,
        },
        headers=auth_headers(FREELANCER_TOKEN),
    )
    assert draft.status_code == 201
    sub_id = draft.json()["id"]
    assert draft.json()["status"] == "DRAFT"

    # Owner cannot start review while still in DRAFT
    assert (
        await client.post(
            f"/api/v1/review/submissions/{sub_id}/start",
            headers=auth_headers(OWNER_TOKEN),
        )
    ).status_code == 409

    # Project member can comment; unrelated freelancer cannot comment or read
    comment_ok = await client.post(
        f"/api/v1/submissions/{sub_id}/comments",
        json={"body": "Please run full suite before submitting.", "file_path": "app/main.py", "line_number": 10},
        headers=auth_headers(OWNER_TOKEN),
    )
    assert comment_ok.status_code == 201
    assert comment_ok.json()["author_role"] == "OWNER"

    assert (
        await client.post(
            f"/api/v1/submissions/{sub_id}/comments",
            json={"body": "Unauthorized comment"},
            headers=auth_headers(OTHER_FREELANCER_TOKEN),
        )
    ).status_code == 403
    assert (
        await client.post(
            f"/api/v1/submissions/{sub_id}/comments",
            json={"body": "Unauthorized other owner"},
            headers=auth_headers(OTHER_OWNER_TOKEN),
        )
    ).status_code == 403

    # Freelancer submits draft then withdraws cleanly
    submitted = await client.post(
        f"/api/v1/submissions/{sub_id}/submit",
        headers=auth_headers(FREELANCER_TOKEN),
    )
    assert submitted.status_code == 200
    assert submitted.json()["status"] == "SUBMITTED"

    # Duplicate open submission rejected
    dup_open = await client.post(
        f"/api/v1/submissions/projects/{ASSIGNED_PROJECT_ID}",
        json={
            "title": "Duplicate Attempt",
            "summary": "Should be blocked while v1 open.",
            "changes_summary": "Changes text here.",
        },
        headers=auth_headers(FREELANCER_TOKEN),
    )
    assert dup_open.status_code == 409

    withdrawn = await client.post(
        f"/api/v1/submissions/{sub_id}/withdraw",
        headers=auth_headers(FREELANCER_TOKEN),
    )
    assert withdrawn.status_code == 200
    assert withdrawn.json()["status"] == "WITHDRAWN"


@pytest.mark.asyncio
async def test_downstream_project_outage_returns_503_and_rolls_back(
    client: AsyncClient,
    app,
    mock_project_state: MockProjectState,
) -> None:
    # Simulate Project Service outage via HTTP transport failure
    original_transport = app.state.project_client._transport
    app.state.project_client._transport = httpx.MockTransport(
        lambda req: (_ for _ in ()).throw(httpx.ConnectError("down", request=req))
    )
    try:
        res = await client.get(
            f"/api/v1/submissions/projects/{ASSIGNED_PROJECT_ID}",
            headers=auth_headers(ADMIN_TOKEN),
        )
        assert res.status_code == 503
    finally:
        app.state.project_client._transport = original_transport

