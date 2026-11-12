"""Extended tests for Payment Service RBAC, state guards, hold/cancel, downstream failures, and readiness."""

from __future__ import annotations

import uuid

import httpx
import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import create_async_engine

from tests.conftest import (
    FREELANCER_TOKEN,
    OTHER_OWNER_TOKEN,
    OWNER_TOKEN,
    PROJECT_ID,
    SECOND_PROJECT_ID,
    auth_headers,
)


@pytest.mark.asyncio
async def test_degraded_database_readiness_returns_503(client: AsyncClient, app) -> None:
    original_engine = app.state.engine
    broken_engine = create_async_engine("sqlite+aiosqlite:///Z:/nonexistent_dir_xyz/missing.db")
    app.state.engine = broken_engine
    try:
        res = await client.get("/ready")
        assert res.status_code == 503
        assert res.json()["status"] == "not_ready"
    finally:
        app.state.engine = original_engine
        await broken_engine.dispose()


@pytest.mark.asyncio
async def test_auth_missing_validation_and_duplicate_escrow(client: AsyncClient) -> None:
    # Unauthenticated rejected
    assert (
        await client.post(f"/api/v1/payment/projects/{PROJECT_ID}/escrow", json={})
    ).status_code == 401

    # Missing escrow -> 404
    assert (
        await client.get(
            f"/api/v1/payment/escrows/{uuid.uuid4()}",
            headers=auth_headers(OWNER_TOKEN),
        )
    ).status_code == 404

    # Mismatched currency -> 422
    bad_cur = await client.post(
        f"/api/v1/payment/projects/{PROJECT_ID}/escrow",
        json={"currency": "EUR"},
        headers=auth_headers(OWNER_TOKEN),
    )
    assert bad_cur.status_code == 422
    assert bad_cur.json()["error"]["code"] == "ESCROW_CURRENCY_MISMATCH"

    # Extra forbidden body field -> 422
    extra = await client.post(
        f"/api/v1/payment/projects/{PROJECT_ID}/escrow",
        json={"unexpected": 123},
        headers=auth_headers(OWNER_TOKEN),
    )
    assert extra.status_code == 422

    # Second active escrow on same project without idempotency -> 409
    first = await client.post(
        f"/api/v1/payment/projects/{PROJECT_ID}/escrow",
        json={},
        headers=auth_headers(OWNER_TOKEN),
    )
    assert first.status_code == 201
    dup = await client.post(
        f"/api/v1/payment/projects/{PROJECT_ID}/escrow",
        json={},
        headers=auth_headers(OWNER_TOKEN),
    )
    assert dup.status_code == 409
    assert dup.json()["error"]["code"] == "ESCROW_ALREADY_EXISTS"


@pytest.mark.asyncio
async def test_hold_cancel_and_cross_user_isolation(client: AsyncClient) -> None:
    # Create unfunded escrow on unassigned project -> cancel succeeds, refund fails 409
    esc = await client.post(
        f"/api/v1/payment/projects/{SECOND_PROJECT_ID}/escrow",
        json={},
        headers=auth_headers(OWNER_TOKEN),
    )
    assert esc.status_code == 201
    esc_id = esc.json()["id"]

    # Unrelated owner and freelancer cannot inspect or mutate escrow
    assert (
        await client.get(
            f"/api/v1/payment/escrows/{esc_id}",
            headers=auth_headers(OTHER_OWNER_TOKEN),
        )
    ).status_code == 403
    assert (
        await client.get(
            f"/api/v1/payment/escrows/{esc_id}",
            headers=auth_headers(FREELANCER_TOKEN),
        )
    ).status_code == 403
    assert (
        await client.post(
            f"/api/v1/payment/escrows/{esc_id}/cancel",
            json={"reason": "Freelancer try"},
            headers=auth_headers(FREELANCER_TOKEN),
        )
    ).status_code == 403

    # Cannot refund or hold an unfunded CREATED escrow
    assert (
        await client.post(
            f"/api/v1/payment/escrows/{esc_id}/refund",
            json={"reason": "Cannot refund unfunded escrow"},
            headers=auth_headers(OWNER_TOKEN),
        )
    ).status_code == 409
    assert (
        await client.post(
            f"/api/v1/payment/escrows/{esc_id}/hold",
            json={"reason": "Cannot hold unfunded escrow"},
            headers=auth_headers(OWNER_TOKEN),
        )
    ).status_code == 409

    # Cancel succeeds cleanly
    cancelled = await client.post(
        f"/api/v1/payment/escrows/{esc_id}/cancel",
        json={"reason": "Owner withdrew requirement"},
        headers=auth_headers(OWNER_TOKEN),
    )
    assert cancelled.status_code == 200
    assert cancelled.json()["status"] == "CANCELLED"

    # Explicit hold on assigned funded project
    proj_esc = await client.post(
        f"/api/v1/payment/projects/{PROJECT_ID}/escrow",
        json={},
        headers=auth_headers(OWNER_TOKEN),
    )
    assert proj_esc.status_code == 201
    pid = proj_esc.json()["id"]
    funded = await client.post(
        f"/api/v1/payment/escrows/{pid}/fund",
        json={"hold_if_assigned": False},
        headers=auth_headers(OWNER_TOKEN),
    )
    assert funded.json()["status"] == "FUNDED"
    held = await client.post(
        f"/api/v1/payment/escrows/{pid}/hold",
        json={"reason": "Explicit hold"},
        headers=auth_headers(OWNER_TOKEN),
    )
    assert held.json()["status"] == "HELD"


@pytest.mark.asyncio
async def test_downstream_project_outage_returns_503(client: AsyncClient, app) -> None:
    original_transport = app.state.project_client._transport
    app.state.project_client._transport = httpx.MockTransport(
        lambda req: (_ for _ in ()).throw(httpx.ConnectError("proj down", request=req))
    )
    try:
        res = await client.post(
            f"/api/v1/payment/projects/{PROJECT_ID}/escrow",
            json={},
            headers=auth_headers(OWNER_TOKEN),
        )
        assert res.status_code == 503
    finally:
        app.state.project_client._transport = original_transport

