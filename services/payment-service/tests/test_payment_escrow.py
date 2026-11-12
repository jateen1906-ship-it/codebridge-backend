"""Tests for Payment Service escrow lifecycle, idempotency, RBAC, and Review approval lock."""

from __future__ import annotations

import pytest
from httpx import AsyncClient

from tests.conftest import (
    FREELANCER_TOKEN,
    OTHER_OWNER_TOKEN,
    OWNER_TOKEN,
    PROJECT_ID,
    SECOND_PROJECT_ID,
    MockReviewState,
    auth_headers,
)


@pytest.mark.asyncio
async def test_health_and_readiness(client: AsyncClient) -> None:
    assert (await client.get("/health")).status_code == 200
    ready = await client.get("/ready")
    assert ready.status_code == 200
    assert ready.json()["database"] == "ok"


@pytest.mark.asyncio
async def test_escrow_lifecycle_review_gate_idempotency_and_refund_guards(
    client: AsyncClient,
    mock_review_state: MockReviewState,
) -> None:
    # Freelancer and non-owner cannot create escrow
    assert (
        await client.post(
            f"/api/v1/payment/projects/{PROJECT_ID}/escrow",
            json={},
            headers=auth_headers(FREELANCER_TOKEN),
        )
    ).status_code == 403
    assert (
        await client.post(
            f"/api/v1/payment/projects/{PROJECT_ID}/escrow",
            json={},
            headers=auth_headers(OTHER_OWNER_TOKEN),
        )
    ).status_code == 403

    # Mismatched client amount vs server budget rejected
    mismatch = await client.post(
        f"/api/v1/payment/projects/{PROJECT_ID}/escrow",
        json={"amount": "1.00"},
        headers=auth_headers(OWNER_TOKEN),
    )
    assert mismatch.status_code == 422
    assert mismatch.json()["error"]["code"] == "ESCROW_AMOUNT_MISMATCH"

    # Owner creates escrow with server budget using Idempotency-Key
    created = await client.post(
        f"/api/v1/payment/projects/{PROJECT_ID}/escrow",
        json={"notes": "Initial milestone escrow"},
        headers=auth_headers(OWNER_TOKEN, idem="esc-create-1"),
    )
    assert created.status_code == 201
    esc_body = created.json()
    escrow_id = esc_body["id"]
    assert esc_body["status"] == "CREATED"
    assert esc_body["amount"] == "1500.00"

    # Idempotent replay returns exact same escrow without duplicate error
    replay = await client.post(
        f"/api/v1/payment/projects/{PROJECT_ID}/escrow",
        json={"notes": "Initial milestone escrow"},
        headers=auth_headers(OWNER_TOKEN, idem="esc-create-1"),
    )
    assert replay.status_code == 201
    assert replay.json()["id"] == escrow_id

    # Reusing same Idempotency-Key with different payload -> 409 conflict
    conflict = await client.post(
        f"/api/v1/payment/projects/{PROJECT_ID}/escrow",
        json={"notes": "Tampered retry"},
        headers=auth_headers(OWNER_TOKEN, idem="esc-create-1"),
    )
    assert conflict.status_code == 409
    assert conflict.json()["error"]["code"] == "IDEMPOTENCY_KEY_REUSE_CONFLICT"

    # Fund escrow (auto holds because freelancer is assigned)
    funded = await client.post(
        f"/api/v1/payment/escrows/{escrow_id}/fund",
        json={"hold_if_assigned": True},
        headers=auth_headers(OWNER_TOKEN, idem="esc-fund-1"),
    )
    assert funded.status_code == 200
    assert funded.json()["status"] == "HELD"

    # HARD BLOCK: release rejected while Review Service has not approved
    blocked_release = await client.post(
        f"/api/v1/payment/escrows/{escrow_id}/release",
        json={"reason": "Attempt premature release"},
        headers=auth_headers(OWNER_TOKEN),
    )
    assert blocked_release.status_code == 409
    assert blocked_release.json()["error"]["code"] == "REVIEW_APPROVAL_REQUIRED"

    # Review Service approves project -> Release succeeds with idempotency
    mock_review_state.approved_projects.add(PROJECT_ID)
    released = await client.post(
        f"/api/v1/payment/escrows/{escrow_id}/release",
        json={"reason": "Approved work payout"},
        headers=auth_headers(OWNER_TOKEN, idem="esc-rel-1"),
    )
    assert released.status_code == 200
    assert released.json()["status"] == "RELEASED"

    # Second release without idempotency key blocked; idempotent replay returns RELEASED cleanly
    assert (
        await client.post(
            f"/api/v1/payment/escrows/{escrow_id}/release",
            json={"reason": "Double release attempt"},
            headers=auth_headers(OWNER_TOKEN),
        )
    ).status_code == 409
    assert (
        await client.post(
            f"/api/v1/payment/escrows/{escrow_id}/release",
            json={"reason": "Approved work payout"},
            headers=auth_headers(OWNER_TOKEN, idem="esc-rel-1"),
        )
    ).json()["status"] == "RELEASED"

    # Cannot refund after RELEASED
    refund_after_release = await client.post(
        f"/api/v1/payment/escrows/{escrow_id}/refund",
        json={"reason": "Try refunding released money"},
        headers=auth_headers(OWNER_TOKEN),
    )
    assert refund_after_release.status_code == 409
    assert refund_after_release.json()["error"]["code"] == "ESCROW_ALREADY_RELEASED"

    # Freelancer can read ledger transactions for assigned escrow
    txns = await client.get(
        f"/api/v1/payment/escrows/{escrow_id}/transactions",
        headers=auth_headers(FREELANCER_TOKEN),
    )
    assert txns.status_code == 200
    assert [t["transaction_type"] for t in txns.json()] == ["CREATE", "FUND", "RELEASE"]


@pytest.mark.asyncio
async def test_refund_flow_and_unfunded_cancel(client: AsyncClient) -> None:
    esc = await client.post(
        f"/api/v1/payment/projects/{SECOND_PROJECT_ID}/escrow",
        json={"fund_immediately": True},
        headers=auth_headers(OWNER_TOKEN),
    )
    assert esc.status_code == 201
    assert esc.json()["status"] == "FUNDED"
    esc_id = esc.json()["id"]

    refunded = await client.post(
        f"/api/v1/payment/escrows/{esc_id}/refund",
        json={"reason": "Owner cancelled project prior to assignment."},
        headers=auth_headers(OWNER_TOKEN),
    )
    assert refunded.status_code == 200
    assert refunded.json()["status"] == "REFUNDED"
