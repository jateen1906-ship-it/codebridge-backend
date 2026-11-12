"""Tests for submission lifecycle, drafts, withdrawal, comments, and validation."""

from __future__ import annotations

import pytest
from httpx import AsyncClient

from tests.conftest import (
    ASSIGNED_PROJECT_ID,
    COMPLETED_PROJECT_ID,
    FREELANCER_TOKEN,
    OPEN_PROJECT_ID,
    OTHER_FREELANCER_TOKEN,
    OWNER_TOKEN,
    auth_headers,
)


@pytest.mark.asyncio
async def test_health_and_readiness(client: AsyncClient) -> None:
    health = await client.get("/health")
    assert health.status_code == 200
    assert health.json()["service"] == "review-service"
    ready = await client.get("/ready")
    assert ready.status_code == 200
    assert ready.json()["database"] == "ok"


@pytest.mark.asyncio
async def test_submission_draft_submit_withdraw_and_comments(client: AsyncClient) -> None:
    # Unassigned or wrong role cannot submit
    owner_attempt = await client.post(
        f"/api/v1/submissions/projects/{ASSIGNED_PROJECT_ID}",
        json={
            "title": "Illegal owner submit",
            "summary": "Owner cannot pretend to be freelancer.",
            "changes_summary": "None",
        },
        headers=auth_headers(OWNER_TOKEN),
    )
    assert owner_attempt.status_code == 403

    other_dev = await client.post(
        f"/api/v1/submissions/projects/{ASSIGNED_PROJECT_ID}",
        json={
            "title": "Other freelancer",
            "summary": "Not assigned to this project.",
            "changes_summary": "Unauthorized change payload.",
        },
        headers=auth_headers(OTHER_FREELANCER_TOKEN),
    )
    assert other_dev.status_code == 403

    # Reject path traversal in file manifest
    bad_path = await client.post(
        f"/api/v1/submissions/projects/{ASSIGNED_PROJECT_ID}",
        json={
            "title": "Traversal attempt",
            "summary": "Valid summary length here.",
            "changes_summary": "Changed one file illegally.",
            "files": [{"file_path": "../../etc/passwd", "change_type": "MODIFIED"}],
        },
        headers=auth_headers(FREELANCER_TOKEN),
    )
    assert bad_path.status_code == 422

    # Open or completed projects are ineligible
    assert (
        await client.post(
            f"/api/v1/submissions/projects/{OPEN_PROJECT_ID}",
            json={"title": "Open", "summary": "Should fail.", "changes_summary": "Should fail."},
            headers=auth_headers(FREELANCER_TOKEN),
        )
    ).status_code == 403
    assert (
        await client.post(
            f"/api/v1/submissions/projects/{COMPLETED_PROJECT_ID}",
            json={"title": "Done", "summary": "Should fail.", "changes_summary": "Should fail."},
            headers=auth_headers(FREELANCER_TOKEN),
        )
    ).status_code == 403

    # Create draft submission v1
    draft = await client.post(
        f"/api/v1/submissions/projects/{ASSIGNED_PROJECT_ID}",
        json={
            "title": "Fix Stripe Webhook HMAC Check",
            "summary": "Implemented constant-time signature validation and idempotency.",
            "changes_summary": "Updated webhook handler and added unit tests.",
            "test_results_summary": "18/18 pytest passed",
            "git_ref": "main",
            "commit_sha": "abcdef123456",
            "submit_immediately": False,
            "files": [{"file_path": "app/webhooks.py", "change_type": "MODIFIED", "summary": "HMAC check"}],
        },
        headers=auth_headers(FREELANCER_TOKEN),
    )
    assert draft.status_code == 201
    draft_data = draft.json()
    assert draft_data["status"] == "DRAFT"
    assert draft_data["version_number"] == 1
    sub_id = draft_data["id"]

    # Second simultaneous open submission rejected
    dup = await client.post(
        f"/api/v1/submissions/projects/{ASSIGNED_PROJECT_ID}",
        json={
            "title": "Duplicate open",
            "summary": "Should conflict with draft.",
            "changes_summary": "Conflict.",
        },
        headers=auth_headers(FREELANCER_TOKEN),
    )
    assert dup.status_code == 409
    assert dup.json()["error"]["code"] == "SUBMISSION_ALREADY_OPEN"

    # Submit draft -> SUBMITTED
    submitted = await client.post(
        f"/api/v1/submissions/{sub_id}/submit",
        headers=auth_headers(FREELANCER_TOKEN),
    )
    assert submitted.status_code == 200
    assert submitted.json()["status"] == "SUBMITTED"

    # Comment thread between freelancer and owner
    c1 = await client.post(
        f"/api/v1/submissions/{sub_id}/comments",
        json={"body": "Added edge-case test for malformed timestamp.", "file_path": "app/webhooks.py", "line_number": 42},
        headers=auth_headers(FREELANCER_TOKEN),
    )
    assert c1.status_code == 201
    c2 = await client.post(
        f"/api/v1/submissions/{sub_id}/comments",
        json={"body": "Looks clean, starting review."},
        headers=auth_headers(OWNER_TOKEN),
    )
    assert c2.status_code == 201

    # Withdraw v1 -> WITHDRAWN, allowing v2
    withdrawn = await client.post(
        f"/api/v1/submissions/{sub_id}/withdraw",
        headers=auth_headers(FREELANCER_TOKEN),
    )
    assert withdrawn.status_code == 200
    assert withdrawn.json()["status"] == "WITHDRAWN"

    v2 = await client.post(
        f"/api/v1/submissions/projects/{ASSIGNED_PROJECT_ID}",
        json={
            "title": "Webhook Fix v2",
            "summary": "Re-submitted with clean replay protection.",
            "changes_summary": "Updated store lookup and pytest coverage.",
            "submit_immediately": True,
        },
        headers=auth_headers(FREELANCER_TOKEN),
    )
    assert v2.status_code == 201
    assert v2.json()["version_number"] == 2
    assert v2.json()["status"] == "SUBMITTED"
