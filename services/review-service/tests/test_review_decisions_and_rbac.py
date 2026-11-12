"""Tests for owner review decisions, Project Service sync, and atomicity."""

from __future__ import annotations

import pytest
from httpx import AsyncClient

from tests.conftest import (
    ADMIN_TOKEN,
    ASSIGNED_PROJECT_ID,
    FREELANCER_TOKEN,
    OTHER_OWNER_TOKEN,
    OWNER_TOKEN,
    MockProjectState,
    MockTelemetryState,
    auth_headers,
)


@pytest.mark.asyncio
async def test_changes_requested_then_approved_syncs_project_and_summary(
    client: AsyncClient,
    mock_project_state: MockProjectState,
    mock_telemetry_state: MockTelemetryState,
) -> None:
    sub1 = await client.post(
        f"/api/v1/submissions/projects/{ASSIGNED_PROJECT_ID}",
        json={
            "title": "Initial Deliverable",
            "summary": "Implemented main API routes and migrations.",
            "changes_summary": "Added endpoints and schema.",
            "submit_immediately": True,
        },
        headers=auth_headers(FREELANCER_TOKEN),
    )
    assert sub1.status_code == 201
    sub1_id = sub1.json()["id"]

    # Non-owner or freelancer cannot start review or decide
    assert (
        await client.post(
            f"/api/v1/review/submissions/{sub1_id}/start",
            headers=auth_headers(FREELANCER_TOKEN),
        )
    ).status_code == 403
    assert (
        await client.post(
            f"/api/v1/review/submissions/{sub1_id}/start",
            headers=auth_headers(OTHER_OWNER_TOKEN),
        )
    ).status_code == 403

    started = await client.post(
        f"/api/v1/review/submissions/{sub1_id}/start",
        headers=auth_headers(OWNER_TOKEN),
    )
    assert started.status_code == 200
    assert started.json()["status"] == "UNDER_REVIEW"

    # Once UNDER_REVIEW, freelancer cannot withdraw
    assert (
        await client.post(
            f"/api/v1/submissions/{sub1_id}/withdraw",
            headers=auth_headers(FREELANCER_TOKEN),
        )
    ).status_code == 409

    # Owner requests changes -> syncs Project Service to CHANGES_REQUESTED
    req_changes = await client.post(
        f"/api/v1/review/submissions/{sub1_id}/decision",
        json={
            "decision": "CHANGES_REQUESTED",
            "feedback": "Please add rate-limit tests before final sign-off.",
            "checklist_passed": False,
        },
        headers=auth_headers(OWNER_TOKEN),
    )
    assert req_changes.status_code == 200
    assert req_changes.json()["status"] == "CHANGES_REQUESTED"
    assert mock_project_state.projects[ASSIGNED_PROJECT_ID]["status"] == "CHANGES_REQUESTED"

    # Approval requires checklist_passed=True
    sub2 = await client.post(
        f"/api/v1/submissions/projects/{ASSIGNED_PROJECT_ID}",
        json={
            "title": "Deliverable v2 with Tests",
            "summary": "Added full rate-limit test suite and updated docs.",
            "changes_summary": "Added test_rate_limit.py.",
            "submit_immediately": True,
        },
        headers=auth_headers(FREELANCER_TOKEN),
    )
    assert sub2.status_code == 201
    sub2_id = sub2.json()["id"]

    bad_approve = await client.post(
        f"/api/v1/review/submissions/{sub2_id}/decision",
        json={
            "decision": "APPROVED",
            "feedback": "Trying without checklist.",
            "checklist_passed": False,
        },
        headers=auth_headers(OWNER_TOKEN),
    )
    assert bad_approve.status_code == 422
    assert bad_approve.json()["error"]["code"] == "APPROVAL_CHECKLIST_REQUIRED"

    # If Project Service rejects transition, review DB change rolls back cleanly
    mock_project_state.force_transition_status = 503
    outage_approve = await client.post(
        f"/api/v1/review/submissions/{sub2_id}/decision",
        json={
            "decision": "APPROVED",
            "feedback": "All checks verified.",
            "checklist_passed": True,
        },
        headers=auth_headers(OWNER_TOKEN),
    )
    assert outage_approve.status_code == 503
    mock_project_state.force_transition_status = None

    still_submitted = await client.get(
        f"/api/v1/review/submissions/{sub2_id}",
        headers=auth_headers(OWNER_TOKEN),
    )
    assert still_submitted.json()["status"] == "SUBMITTED"

    # Real approval succeeds and completes Project
    approved = await client.post(
        f"/api/v1/review/submissions/{sub2_id}/decision",
        json={
            "decision": "APPROVED",
            "feedback": "All requirements satisfied and verified.",
            "checklist_passed": True,
        },
        headers=auth_headers(OWNER_TOKEN),
    )
    assert approved.status_code == 200
    assert approved.json()["status"] == "APPROVED"
    assert mock_project_state.projects[ASSIGNED_PROJECT_ID]["status"] == "COMPLETED"
    assert mock_project_state.transitions[-1] == {
        "target_status": "COMPLETED",
        "approval_verified": True,
    }

    # Cannot decide a second time once APPROVED
    double_decide = await client.post(
        f"/api/v1/review/submissions/{sub2_id}/decision",
        json={
            "decision": "REJECTED",
            "feedback": "Too late to reject after approval.",
            "checklist_passed": False,
        },
        headers=auth_headers(OWNER_TOKEN),
    )
    assert double_decide.status_code == 409

    # Project review summary reports verified approval accurately (accessible to owner, freelancer, admin)
    summary = await client.get(
        f"/api/v1/review/projects/{ASSIGNED_PROJECT_ID}",
        headers=auth_headers(ADMIN_TOKEN),
    )
    assert summary.status_code == 200
    summary_body = summary.json()
    assert summary_body["approved"] is True
    assert summary_body["approved_submission_id"] == sub2_id
    assert summary_body["submissions_count"] == 2
    assert len(mock_telemetry_state.notifications) >= 2
    assert len(mock_telemetry_state.audits) >= 2

