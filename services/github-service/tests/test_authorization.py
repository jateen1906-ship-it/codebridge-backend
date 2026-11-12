"""Authorization, correlation ID propagation, and fail-closed RBAC matrix tests."""

from __future__ import annotations

import uuid

import pytest
from httpx import AsyncClient

from tests.conftest import (
    OTHER_OWNER_PROJECT_ID,
    PROJECT_ID,
    TEST_ADMIN_TOKEN,
    TEST_FREELANCER_TOKEN,
    TEST_OWNER_TOKEN,
    auth_headers,
)


@pytest.mark.asyncio
async def test_authorization_matrix_and_correlation_id_propagation(
    client: AsyncClient,
) -> None:
    correlation_id = str(uuid.uuid4())

    protected_owner_requests = [
        ("GET", "/api/v1/github/oauth/authorize", {"project_id": str(PROJECT_ID)}),
        ("DELETE", "/api/v1/github/connection", None),
        ("GET", "/api/v1/github/repositories", None),
        ("POST", f"/api/v1/projects/{PROJECT_ID}/github/connect", None),
        ("DELETE", f"/api/v1/projects/{PROJECT_ID}/github/disconnect", None),
        ("GET", f"/api/v1/projects/{PROJECT_ID}/github/branches", None),
        ("GET", f"/api/v1/projects/{PROJECT_ID}/github/contents", None),
        ("GET", f"/api/v1/projects/{PROJECT_ID}/github/commits", None),
    ]

    for method, path, params in protected_owner_requests:
        # Unauthenticated -> 401 with echoed X-Request-ID
        json_body = (
            {"github_repository_id": 1001, "full_name": "alice-gh/sample-api"}
            if method == "POST"
            else None
        )
        unauth = await client.request(
            method,
            path,
            params=params,
            json=json_body,
            headers={"X-Request-ID": correlation_id},
            follow_redirects=False,
        )
        assert unauth.status_code == 401
        assert unauth.headers.get("x-request-id") == correlation_id
        assert unauth.json()["request_id"] == correlation_id

        # Non-owner roles -> 403
        for role_token in (TEST_FREELANCER_TOKEN, TEST_ADMIN_TOKEN):
            forbidden = await client.request(
                method,
                path,
                params=params,
                json=json_body,
                headers=auth_headers(role_token, request_id=correlation_id),
                follow_redirects=False,
            )
            assert forbidden.status_code == 403
            assert forbidden.headers.get("x-request-id") == correlation_id

    # Cross-owner isolation -> 403
    for method, path, params in (
        ("DELETE", f"/api/v1/projects/{OTHER_OWNER_PROJECT_ID}/github/disconnect", None),
        ("GET", f"/api/v1/projects/{OTHER_OWNER_PROJECT_ID}/github/branches", None),
        ("GET", f"/api/v1/projects/{OTHER_OWNER_PROJECT_ID}/github/contents", None),
        ("GET", f"/api/v1/projects/{OTHER_OWNER_PROJECT_ID}/github/commits", None),
    ):
        cross = await client.request(
            method,
            path,
            headers=auth_headers(TEST_OWNER_TOKEN, request_id=correlation_id),
        )
        assert cross.status_code == 403
        assert cross.headers.get("x-request-id") == correlation_id

