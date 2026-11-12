"""Extended tests for Verification Service RBAC, profile endpoints, downstream errors, and readiness."""

from __future__ import annotations

import uuid

import httpx
import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import create_async_engine

from tests.conftest import (
    ADMIN_TOKEN,
    APPROVED_SUBMISSION_ID,
    FREELANCER_ID,
    FREELANCER_TOKEN,
    OTHER_OWNER_TOKEN,
    OWNER_TOKEN,
    PROJECT_ID,
    auth_headers,
)


@pytest.mark.asyncio
async def test_degraded_database_readiness_returns_503(client: AsyncClient, app) -> None:
    original_engine = app.state.engine
    broken_engine = create_async_engine("sqlite+aiosqlite:///Z:/nonexistent_dir_xyz/missing.db")
    app.state.engine = broken_engine
    try:
        resp = await client.get("/ready")
        assert resp.status_code == 503
        assert resp.json()["status"] == "not_ready"
    finally:
        app.state.engine = original_engine
        await broken_engine.dispose()


@pytest.mark.asyncio
async def test_auth_missing_records_validation_and_double_revoke(client: AsyncClient) -> None:
    # Unauthenticated private endpoints return 401
    assert (
        await client.post(
            "/api/v1/verification/records",
            json={"project_id": str(PROJECT_ID), "submission_id": str(APPROVED_SUBMISSION_ID)},
        )
    ).status_code == 401
    assert (await client.get("/api/v1/verification/me")).status_code == 401

    # Missing public/private records -> 404
    assert (await client.get("/api/v1/verification/public/cbv_doesnotexist")).status_code == 404
    assert (
        await client.get(
            f"/api/v1/verification/records/{uuid.uuid4()}",
            headers=auth_headers(ADMIN_TOKEN),
        )
    ).status_code == 404
    assert (
        await client.post(f"/api/v1/verification/records/{uuid.uuid4()}/verify")
    ).status_code == 404

    # Invalid revocation reason -> 422
    issued = await client.post(
        "/api/v1/verification/records",
        json={"project_id": str(PROJECT_ID), "submission_id": str(APPROVED_SUBMISSION_ID)},
        headers=auth_headers(OWNER_TOKEN),
    )
    assert issued.status_code == 201
    rec_id = issued.json()["id"]

    # Unrelated owner cannot view or revoke private record
    assert (
        await client.get(
            f"/api/v1/verification/records/{rec_id}",
            headers=auth_headers(OTHER_OWNER_TOKEN),
        )
    ).status_code == 403
    assert (
        await client.post(
            f"/api/v1/verification/records/{rec_id}/revoke",
            json={"status": "REVOKED", "reason": "Unauthorized revoke attempt"},
            headers=auth_headers(OTHER_OWNER_TOKEN),
        )
    ).status_code == 403

    bad_reason = await client.post(
        f"/api/v1/verification/records/{rec_id}/revoke",
        json={"status": "REVOKED", "reason": "no"},
        headers=auth_headers(OWNER_TOKEN),
    )
    assert bad_reason.status_code == 422

    # Valid revocation followed by duplicate revocation -> 409
    assert (
        await client.post(
            f"/api/v1/verification/records/{rec_id}/revoke",
            json={"status": "REVOKED", "reason": "Corrected project metadata after release."},
            headers=auth_headers(OWNER_TOKEN),
        )
    ).status_code == 200
    dup_rev = await client.post(
        f"/api/v1/verification/records/{rec_id}/revoke",
        json={"status": "REVOKED", "reason": "Second revoke attempt should fail."},
        headers=auth_headers(OWNER_TOKEN),
    )
    assert dup_rev.status_code == 409


@pytest.mark.asyncio
async def test_freelancer_verified_work_profile_and_me_endpoints(client: AsyncClient) -> None:
    issued = await client.post(
        "/api/v1/verification/records",
        json={"project_id": str(PROJECT_ID), "submission_id": str(APPROVED_SUBMISSION_ID)},
        headers=auth_headers(OWNER_TOKEN),
    )
    assert issued.status_code == 201

    me_res = await client.get("/api/v1/verification/me", headers=auth_headers(FREELANCER_TOKEN))
    assert me_res.status_code == 200
    assert me_res.json()["total"] == 1
    assert me_res.json()["items"][0]["project_id"] == str(PROJECT_ID)

    pub_profile = await client.get(f"/api/v1/verification/freelancers/{FREELANCER_ID}")
    assert pub_profile.status_code == 200
    assert pub_profile.json()["total"] == 1
    assert pub_profile.json()["items"][0]["is_valid"] is True

    proj_list = await client.get(
        f"/api/v1/verification/projects/{PROJECT_ID}",
        headers=auth_headers(FREELANCER_TOKEN),
    )
    assert proj_list.status_code == 200
    assert proj_list.json()["total"] == 1


@pytest.mark.asyncio
async def test_downstream_review_service_outage_returns_503(client: AsyncClient, app) -> None:
    original_transport = app.state.review_client._transport
    app.state.review_client._transport = httpx.MockTransport(
        lambda req: (_ for _ in ()).throw(httpx.ConnectError("review unreachable", request=req))
    )
    try:
        resp = await client.post(
            "/api/v1/verification/records",
            json={"project_id": str(PROJECT_ID), "submission_id": str(APPROVED_SUBMISSION_ID)},
            headers=auth_headers(OWNER_TOKEN),
        )
        assert resp.status_code == 503
    finally:
        app.state.review_client._transport = original_transport

