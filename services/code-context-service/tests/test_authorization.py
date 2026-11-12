"""RBAC, project ownership, & active freelancer assignment enforcement tests."""

from __future__ import annotations

import httpx
import pytest

from tests.conftest import (
    ADMIN_TOKEN,
    ASSIGNED_FREELANCER_TOKEN,
    MISSING_PROJECT_ID,
    OTHER_OWNER_TOKEN,
    OWNER_TOKEN,
    PROJECT_ID,
    UNASSIGNED_FREELANCER_TOKEN,
    auth_headers,
)


@pytest.mark.asyncio
async def test_only_project_owner_can_index_reindex_or_generate_insights(
    async_client: httpx.AsyncClient,
) -> None:
    for token in (
        OTHER_OWNER_TOKEN,
        ASSIGNED_FREELANCER_TOKEN,
        UNASSIGNED_FREELANCER_TOKEN,
        ADMIN_TOKEN,
    ):
        r_index = await async_client.post(
            f"/api/v1/code-context/projects/{PROJECT_ID}/index",
            headers=auth_headers(token),
        )
        assert r_index.status_code == 403
        assert r_index.json()["error"]["code"] == "FORBIDDEN"

        r_reindex = await async_client.post(
            f"/api/v1/code-context/projects/{PROJECT_ID}/reindex",
            headers=auth_headers(token),
        )
        assert r_reindex.status_code == 403
        assert r_reindex.json()["error"]["code"] == "FORBIDDEN"

        r_insights = await async_client.post(
            f"/api/v1/code-context/projects/{PROJECT_ID}/insights/generate",
            headers=auth_headers(token),
        )
        assert r_insights.status_code == 403
        assert r_insights.json()["error"]["code"] == "FORBIDDEN"


@pytest.mark.asyncio
async def test_unassigned_freelancer_and_admin_cannot_query_or_read_context(
    async_client: httpx.AsyncClient,
) -> None:
    # Owner indexes and generates insights first so real index data exists.
    idx_resp = await async_client.post(
        f"/api/v1/code-context/projects/{PROJECT_ID}/index",
        headers=auth_headers(OWNER_TOKEN),
    )
    assert idx_resp.status_code == 201
    gen_resp = await async_client.post(
        f"/api/v1/code-context/projects/{PROJECT_ID}/insights/generate",
        headers=auth_headers(OWNER_TOKEN),
    )
    assert gen_resp.status_code == 201

    # Unassigned freelancer even on an OPEN project MUST be blocked from all endpoints.
    for method, suffix, body in (
        ("POST", "query", {"query": "How does Stripe webhook work?"}),
        ("GET", "index/status", None),
        ("GET", "insights", None),
    ):
        resp = await async_client.request(
            method,
            f"/api/v1/code-context/projects/{PROJECT_ID}/{suffix}",
            json=body,
            headers=auth_headers(UNASSIGNED_FREELANCER_TOKEN),
        )
        assert resp.status_code == 403
        assert resp.json()["error"]["code"] == "FORBIDDEN"

    # Non-owner owner MUST be blocked.
    resp_other = await async_client.post(
        f"/api/v1/code-context/projects/{PROJECT_ID}/query",
        json={"query": "How does webhook work?"},
        headers=auth_headers(OTHER_OWNER_TOKEN),
    )
    assert resp_other.status_code == 403

    # Admin cannot query source code snippets, though Admin can view diagnostics/insights.
    resp_admin_query = await async_client.post(
        f"/api/v1/code-context/projects/{PROJECT_ID}/query",
        json={"query": "Stripe webhook"},
        headers=auth_headers(ADMIN_TOKEN),
    )
    assert resp_admin_query.status_code == 403

    resp_admin_status = await async_client.get(
        f"/api/v1/code-context/projects/{PROJECT_ID}/index/status",
        headers=auth_headers(ADMIN_TOKEN),
    )
    assert resp_admin_status.status_code == 200


@pytest.mark.asyncio
async def test_assigned_freelancer_can_query_and_read_status_and_insights(
    async_client: httpx.AsyncClient,
) -> None:
    await async_client.post(
        f"/api/v1/code-context/projects/{PROJECT_ID}/index",
        headers=auth_headers(OWNER_TOKEN),
    )
    await async_client.post(
        f"/api/v1/code-context/projects/{PROJECT_ID}/insights/generate",
        headers=auth_headers(OWNER_TOKEN),
    )

    q_resp = await async_client.post(
        f"/api/v1/code-context/projects/{PROJECT_ID}/query",
        json={"query": "How is handle_payment_callback implemented?"},
        headers=auth_headers(ASSIGNED_FREELANCER_TOKEN),
    )
    assert q_resp.status_code == 200
    assert q_resp.json()["data"]["retrieval_count"] >= 1

    st_resp = await async_client.get(
        f"/api/v1/code-context/projects/{PROJECT_ID}/index/status",
        headers=auth_headers(ASSIGNED_FREELANCER_TOKEN),
    )
    assert st_resp.status_code == 200
    assert st_resp.json()["has_active_index"] is True

    ins_resp = await async_client.get(
        f"/api/v1/code-context/projects/{PROJECT_ID}/insights",
        headers=auth_headers(ASSIGNED_FREELANCER_TOKEN),
    )
    assert ins_resp.status_code == 200


@pytest.mark.asyncio
async def test_nonexistent_project_returns_404_project_not_found(
    async_client: httpx.AsyncClient,
) -> None:
    resp = await async_client.post(
        f"/api/v1/code-context/projects/{MISSING_PROJECT_ID}/index",
        headers=auth_headers(OWNER_TOKEN),
    )
    assert resp.status_code == 404
    assert resp.json()["error"]["code"] == "PROJECT_NOT_FOUND"

