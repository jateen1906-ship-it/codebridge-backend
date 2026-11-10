"""Tests for auth failures, correlation IDs, structured logs, sanitized errors, & prod config."""

from __future__ import annotations

import logging
import uuid

import httpx
import pytest
from fastapi import FastAPI
from pydantic import ValidationError as PydanticValidationError
from sqlalchemy.exc import SQLAlchemyError

from app.core.config import Settings
from app.services.project_service import ProjectService
from tests.conftest import (
    INACTIVE_USER_TOKEN,
    INVALID_TOKEN,
    OWNER_TOKEN,
    auth_headers,
    build_project_payload,
)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("method", "path", "json_body"),
    [
        ("POST", "/api/v1/projects", build_project_payload()),
        ("GET", "/api/v1/projects", None),
        ("GET", f"/api/v1/projects/{uuid.uuid4()}", None),
        ("PATCH", f"/api/v1/projects/{uuid.uuid4()}", {"title": "New Title"}),
        ("POST", f"/api/v1/projects/{uuid.uuid4()}/publish", None),
        ("POST", f"/api/v1/projects/{uuid.uuid4()}/cancel", None),
        (
            "POST",
            f"/api/v1/projects/{uuid.uuid4()}/select-freelancer",
            {"freelancer_id": str(uuid.uuid4())},
        ),
    ],
)
async def test_unauthenticated_and_invalid_tokens_rejected_across_all_routes(
    client: httpx.AsyncClient,
    method: str,
    path: str,
    json_body: dict | None,
) -> None:
    missing_res = await client.request(method, path, json=json_body)
    assert missing_res.status_code == 401
    assert missing_res.json()["error"]["code"] == "UNAUTHORIZED"

    bad_scheme = await client.request(
        method,
        path,
        headers={"Authorization": "Basic dXNlcjpwYXNz"},
        json=json_body,
    )
    assert bad_scheme.status_code == 401
    assert bad_scheme.json()["error"]["code"] == "UNAUTHORIZED"

    invalid_res = await client.request(
        method,
        path,
        headers=auth_headers(INVALID_TOKEN),
        json=json_body,
    )
    assert invalid_res.status_code == 401
    assert invalid_res.json()["error"]["code"] == "UNAUTHORIZED"

    inactive_res = await client.request(
        method,
        path,
        headers=auth_headers(INACTIVE_USER_TOKEN),
        json=json_body,
    )
    assert inactive_res.status_code == 403
    assert inactive_res.json()["error"]["code"] == "FORBIDDEN"


@pytest.mark.asyncio
async def test_correlation_id_preservation_and_invalid_replacement(
    client: httpx.AsyncClient,
) -> None:
    explicit_id = str(uuid.uuid4())
    res = await client.get(
        "/api/v1/projects",
        headers=auth_headers(OWNER_TOKEN, request_id=explicit_id),
    )
    assert res.status_code == 200
    assert res.headers["x-request-id"] == explicit_id

    # Invalid correlation ID must be replaced with valid canonical UUIDv4
    bad_id_res = await client.get(
        "/api/v1/projects",
        headers=auth_headers(OWNER_TOKEN, request_id="not-a-valid-uuid"),
    )
    assert bad_id_res.status_code == 200
    generated_id = bad_id_res.headers["x-request-id"]
    assert generated_id != "not-a-valid-uuid"
    assert str(uuid.UUID(generated_id)) == generated_id


@pytest.mark.asyncio
async def test_logs_redact_secrets_and_never_contain_bearer_token(
    client: httpx.AsyncClient,
    project_app: FastAPI,
    caplog: pytest.LogCaptureFixture,
) -> None:
    logger = logging.getLogger("project_service")
    logger.propagate = True
    req_id = str(uuid.uuid4())

    with caplog.at_level(logging.INFO, logger="project_service"):
        res = await client.post(
            "/api/v1/projects",
            headers=auth_headers(OWNER_TOKEN, request_id=req_id),
            json=build_project_payload(),
        )
        assert res.status_code == 201

    assert len(caplog.records) >= 2
    formatted_lines = [logger.handlers[0].format(rec) for rec in caplog.records if logger.handlers]
    combined = "\n".join(formatted_lines)
    assert req_id in combined
    assert OWNER_TOKEN not in combined
    assert "internal-identity-mock" not in combined
    logger.propagate = False


@pytest.mark.asyncio
async def test_database_error_returns_sanitized_500_without_internals(
    client: httpx.AsyncClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def boom(*args, **kwargs):
        raise SQLAlchemyError("SELECT * FROM projects SECRET_SQL_STATE")

    monkeypatch.setattr(ProjectService, "list_projects", boom)
    req_id = str(uuid.uuid4())
    res = await client.get(
        "/api/v1/projects",
        headers=auth_headers(OWNER_TOKEN, request_id=req_id),
    )
    assert res.status_code == 500
    body = res.json()
    assert body["success"] is False
    assert body["error"]["code"] == "INTERNAL_SERVER_ERROR"
    assert body["request_id"] == req_id
    assert "SECRET_SQL_STATE" not in res.text


def test_production_and_staging_settings_guards() -> None:
    # Default placeholder DB URL blocked in production
    with pytest.raises(PydanticValidationError):
        Settings(APP_ENV="production", DEBUG=False)

    # SQLite blocked in staging/production
    with pytest.raises(PydanticValidationError):
        Settings(
            APP_ENV="staging",
            DEBUG=False,
            DATABASE_URL="sqlite+aiosqlite:///./bad.db",
        )

    # DEBUG=True blocked in production
    with pytest.raises(PydanticValidationError):
        Settings(
            APP_ENV="production",
            DEBUG=True,
            DATABASE_URL="postgresql+asyncpg://svc:strongpass@db.internal:5432/codebridge_project",
        )

    # DEBUG=release coerced to False cleanly
    prod_settings = Settings(
        APP_ENV="production",
        DEBUG="release",  # type: ignore[arg-type]
        DATABASE_URL="postgresql+asyncpg://svc:strongpass@db.internal:5432/codebridge_project",
    )
    assert prod_settings.DEBUG is False


@pytest.mark.asyncio
async def test_health_and_openapi_endpoints(client: httpx.AsyncClient) -> None:
    health = await client.get("/health")
    assert health.status_code == 200
    assert health.json() == {
        "status": "healthy",
        "service": "project-service",
        "version": "1.0.0",
    }

    openapi = await client.get("/openapi.json")
    assert openapi.status_code == 200
    paths = set(openapi.json()["paths"].keys())
    assert "/api/v1/projects" in paths
    assert "/api/v1/projects/{project_id}" in paths
    assert "/api/v1/projects/{project_id}/publish" in paths
    assert "/api/v1/projects/{project_id}/cancel" in paths
    assert "/api/v1/projects/{project_id}/select-freelancer" in paths

