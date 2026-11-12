"""Tests enforcing container CPU, memory, PIDs, disabled networking, and production config rules."""

from __future__ import annotations

from pathlib import Path

import pytest
from httpx import AsyncClient
from pydantic import ValidationError

from app.core.config import Settings
from app.services.docker_adapter import MockDockerAdapter
from tests.conftest import (
    ASSIGNED_PROJECT_ID,
    TEST_FREELANCER_TOKEN,
    auth_headers,
    build_test_settings,
)


@pytest.mark.asyncio
async def test_container_spec_enforces_hardened_resource_and_network_limits(
    client: AsyncClient,
    mock_docker_adapter: MockDockerAdapter,
) -> None:
    resp = await client.post(
        f"/api/v1/workspace/projects/{ASSIGNED_PROJECT_ID}",
        headers=auth_headers(TEST_FREELANCER_TOKEN),
    )
    assert resp.status_code == 201
    cid = resp.json()["container_id"]
    spec = mock_docker_adapter.containers[cid]["spec"]
    assert spec.cpu_limit == 1.0
    assert spec.memory_limit_mb == 512
    assert spec.pids_limit == 128
    assert spec.network_disabled is True


def test_production_config_rejects_mock_docker_or_enabled_networking(tmp_path: Path) -> None:
    base_kwargs = build_test_settings(tmp_path).model_dump()
    base_kwargs["APP_ENV"] = "production"
    base_kwargs["DATABASE_URL"] = "postgresql+asyncpg://cb_user:strongpw@db:5432/codebridge_workspace"

    # Mock mode disallowed in production
    with pytest.raises(ValidationError):
        Settings(**base_kwargs)

    # Disabling network isolation disallowed in production
    prod_kwargs = dict(base_kwargs)
    prod_kwargs["WORKSPACE_DOCKER_MODE"] = "docker"
    prod_kwargs["WORKSPACE_NETWORK_DISABLED"] = False
    with pytest.raises(ValidationError):
        Settings(**prod_kwargs)

    prod_kwargs["WORKSPACE_NETWORK_DISABLED"] = True
    valid = Settings(**prod_kwargs)
    assert valid.WORKSPACE_DOCKER_MODE == "docker"

