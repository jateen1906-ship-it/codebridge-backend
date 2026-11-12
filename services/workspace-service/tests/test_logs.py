"""Tests for bounded workspace log retrieval and secret scrubbing."""

from __future__ import annotations

import pytest
from httpx import AsyncClient

from app.services.docker_adapter import MockDockerAdapter
from tests.conftest import (
    ASSIGNED_PROJECT_ID,
    TEST_FREELANCER_TOKEN,
    auth_headers,
)


@pytest.mark.asyncio
async def test_workspace_logs_are_bounded_and_scrubbed_of_secrets(
    client: AsyncClient,
    mock_docker_adapter: MockDockerAdapter,
) -> None:
    created = await client.post(
        f"/api/v1/workspace/projects/{ASSIGNED_PROJECT_ID}",
        headers=auth_headers(TEST_FREELANCER_TOKEN),
    )
    assert created.status_code == 201
    ws_id = created.json()["id"]
    cid = created.json()["container_id"]

    mock_docker_adapter.containers[cid]["logs"].extend(
        [
            "Token leaked: Bearer super.secret.jwt.token",
            "GitHub token ghp_secret1234567890abcdef in output",
            "Tests passed cleanly: 12/12",
        ]
    )

    logs_resp = await client.get(
        f"/api/v1/workspace/{ws_id}/logs",
        params={"tail": 3},
        headers=auth_headers(TEST_FREELANCER_TOKEN),
    )
    assert logs_resp.status_code == 200
    body = logs_resp.json()
    assert len(body["lines"]) == 3
    joined = "\n".join(body["lines"])
    assert "super.secret.jwt.token" not in joined
    assert "ghp_secret1234567890abcdef" not in joined
    assert "[REDACTED]" in joined
    assert "Tests passed cleanly" in joined

