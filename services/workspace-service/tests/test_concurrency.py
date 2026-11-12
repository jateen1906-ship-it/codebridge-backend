"""Tests ensuring concurrent provisioning on one project permits only a single active workspace."""

from __future__ import annotations

import asyncio

import pytest
from httpx import AsyncClient

from tests.conftest import (
    ASSIGNED_PROJECT_ID,
    TEST_FREELANCER_TOKEN,
    auth_headers,
)


@pytest.mark.asyncio
async def test_concurrent_workspace_provisioning_yields_single_winner_and_conflict(
    client: AsyncClient,
) -> None:
    r1, r2 = await asyncio.gather(
        client.post(
            f"/api/v1/workspace/projects/{ASSIGNED_PROJECT_ID}",
            headers=auth_headers(TEST_FREELANCER_TOKEN),
        ),
        client.post(
            f"/api/v1/workspace/projects/{ASSIGNED_PROJECT_ID}",
            headers=auth_headers(TEST_FREELANCER_TOKEN),
        ),
    )
    statuses = sorted([r1.status_code, r2.status_code])
    assert statuses == [201, 409]

