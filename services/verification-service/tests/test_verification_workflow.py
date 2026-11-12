"""Tests for Verification Service cryptographic signing, approval enforcement, public lookup, and revocation."""

from __future__ import annotations

import pytest
from httpx import AsyncClient
from sqlalchemy import update

from app.models import VerifiedWorkRecord
from tests.conftest import (
    ADMIN_TOKEN,
    APPROVED_SUBMISSION_ID,
    FREELANCER_TOKEN,
    OTHER_OWNER_TOKEN,
    OWNER_TOKEN,
    PROJECT_ID,
    UNAPPROVED_SUBMISSION_ID,
    auth_headers,
)


@pytest.mark.asyncio
async def test_health_and_readiness(client: AsyncClient) -> None:
    assert (await client.get("/health")).status_code == 200
    ready = await client.get("/ready")
    assert ready.status_code == 200
    assert ready.json()["database"] == "ok"


@pytest.mark.asyncio
async def test_issuance_requires_approval_rbac_public_lookup_tamper_and_revocation(
    client: AsyncClient,
    app,
) -> None:
    # Freelancer and non-owner cannot issue records
    assert (
        await client.post(
            "/api/v1/verification/records",
            json={"project_id": str(PROJECT_ID), "submission_id": str(APPROVED_SUBMISSION_ID)},
            headers=auth_headers(FREELANCER_TOKEN),
        )
    ).status_code == 403
    assert (
        await client.post(
            "/api/v1/verification/records",
            json={"project_id": str(PROJECT_ID), "submission_id": str(APPROVED_SUBMISSION_ID)},
            headers=auth_headers(OTHER_OWNER_TOKEN),
        )
    ).status_code == 403

    # Unapproved submission cannot be verified
    unapproved = await client.post(
        "/api/v1/verification/records",
        json={"project_id": str(PROJECT_ID), "submission_id": str(UNAPPROVED_SUBMISSION_ID)},
        headers=auth_headers(OWNER_TOKEN),
    )
    assert unapproved.status_code == 409
    assert unapproved.json()["error"]["code"] == "SUBMISSION_NOT_APPROVED"

    # Owner issues signed verified work record for approved submission
    issued = await client.post(
        "/api/v1/verification/records",
        json={"project_id": str(PROJECT_ID), "submission_id": str(APPROVED_SUBMISSION_ID)},
        headers=auth_headers(OWNER_TOKEN),
    )
    assert issued.status_code == 201
    body = issued.json()
    record_id = body["id"]
    public_id = body["public_verification_id"]
    assert public_id.startswith("cbv_")
    assert body["signature_valid"] is True
    assert body["is_valid"] is True
    assert body["repository_full_name"] == "alice-gh/sample-api"

    # Duplicate active record blocked
    dup = await client.post(
        "/api/v1/verification/records",
        json={"project_id": str(PROJECT_ID), "submission_id": str(APPROVED_SUBMISSION_ID)},
        headers=auth_headers(OWNER_TOKEN),
    )
    assert dup.status_code == 409

    # Unauthenticated public lookup works and never leaks owner email or internal secrets
    pub = await client.get(f"/api/v1/verification/public/{public_id}")
    assert pub.status_code == 200
    pub_json = pub.json()
    assert pub_json["is_valid"] is True
    assert pub_json["signature_valid"] is True
    assert "alice@example.com" not in pub.text

    # Signature verification endpoint succeeds
    chk = await client.post(f"/api/v1/verification/records/{record_id}/verify")
    assert chk.status_code == 200
    assert chk.json()["signature_valid"] is True
    assert chk.json()["is_valid"] is True

    # Formal revocation marks record invalid while preserving immutable audit trail
    revoked = await client.post(
        f"/api/v1/verification/records/{record_id}/revoke",
        json={"status": "REVOKED", "reason": "Manual audit discovered inaccurate commit metadata."},
        headers=auth_headers(ADMIN_TOKEN),
    )
    assert revoked.status_code == 200
    assert revoked.json()["status"] == "REVOKED"
    assert revoked.json()["is_valid"] is False
    assert revoked.json()["signature_valid"] is True

    pub_after_revoke = await client.get(f"/api/v1/verification/public/{public_id}")
    assert pub_after_revoke.status_code == 200
    assert pub_after_revoke.json()["status"] == "REVOKED"
    assert pub_after_revoke.json()["is_valid"] is False

    # Simulate DB tampering -> cryptographic check immediately flags invalid signature
    async with app.state.session_factory() as session:
        await session.execute(
            update(VerifiedWorkRecord)
            .where(VerifiedWorkRecord.public_verification_id == public_id)
            .values(canonical_payload_json='{"tampered":true}')
        )
        await session.commit()
    tampered_pub = await client.get(f"/api/v1/verification/public/{public_id}")
    assert tampered_pub.status_code == 200
    assert tampered_pub.json()["signature_valid"] is False
    assert tampered_pub.json()["is_valid"] is False
