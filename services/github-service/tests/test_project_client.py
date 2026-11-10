"""Unit/transport tests for ProjectClient ownership, workflow status, and error mapping."""

from __future__ import annotations

import uuid

import httpx
import pytest

from app.clients.project_client import ProjectClient
from app.core.exceptions import (
    ForbiddenError,
    NotFoundError,
    UnauthorizedError,
    UpstreamServiceUnavailableError,
)
from tests.conftest import OWNER_ID, PROJECT_ID, build_test_settings


@pytest.mark.asyncio
async def test_project_client_parses_active_workflow_and_forwards_headers(tmp_path) -> None:
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(
            200,
            json={
                "id": str(PROJECT_ID),
                "owner_id": str(OWNER_ID),
                "status": "IN_PROGRESS",
                "active_assignment": {
                    "id": str(uuid.uuid4()),
                    "freelancer_id": str(uuid.uuid4()),
                    "status": "ACTIVE",
                },
            },
        )

    settings = build_test_settings((tmp_path / "proj.db").as_posix())
    client = ProjectClient(settings, transport=httpx.MockTransport(handler))
    await client.startup()
    try:
        req_id = str(uuid.uuid4())
        summary = await client.get_project(
            PROJECT_ID,
            bearer_token="caller-jwt",
            request_id=req_id,
        )
        assert summary.id == PROJECT_ID
        assert summary.owner_id == OWNER_ID
        assert summary.has_active_assignment is True
        assert summary.has_active_workflow is True
        assert seen[0].headers["authorization"] == "Bearer caller-jwt"
        assert seen[0].headers["x-request-id"] == req_id
    finally:
        await client.close()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("response_factory", "expected_exc"),
    [
        (lambda req: httpx.Response(401, json={}), UnauthorizedError),
        (lambda req: httpx.Response(403, json={}), ForbiddenError),
        (lambda req: httpx.Response(404, json={}), NotFoundError),
        (lambda req: httpx.Response(500, json={}), UpstreamServiceUnavailableError),
        (lambda req: httpx.Response(200, content=b"not-json"), UpstreamServiceUnavailableError),
        (
            lambda req: httpx.Response(
                200,
                json={"id": str(uuid.uuid4()), "owner_id": str(OWNER_ID), "status": "OPEN"},
            ),
            UpstreamServiceUnavailableError,
        ),
    ],
)
async def test_project_client_error_mapping(tmp_path, response_factory, expected_exc) -> None:
    settings = build_test_settings((tmp_path / "proj_err.db").as_posix())
    client = ProjectClient(
        settings,
        transport=httpx.MockTransport(lambda req: response_factory(req)),
    )
    await client.startup()
    try:
        with pytest.raises(expected_exc):
            await client.get_project(
                PROJECT_ID,
                bearer_token="caller-jwt",
                request_id=str(uuid.uuid4()),
            )
    finally:
        await client.close()

