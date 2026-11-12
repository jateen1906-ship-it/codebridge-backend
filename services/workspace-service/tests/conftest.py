"""Shared pytest fixtures, SQLite isolation, and mocked peer service transports for Workspace Service."""

from __future__ import annotations

import io
import uuid
import zipfile
from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import httpx
import pytest
import pytest_asyncio
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from app.core.config import Settings
from app.main import create_app
from app.models.base import Base
from app.services.docker_adapter import MockDockerAdapter

OWNER_ID = uuid.UUID("11111111-1111-4111-8111-111111111111")
OTHER_OWNER_ID = uuid.UUID("22222222-2222-4222-8222-222222222222")
FREELANCER_ID = uuid.UUID("33333333-3333-4333-8333-333333333333")
OTHER_FREELANCER_ID = uuid.UUID("44444444-4444-4444-8444-444444444444")
ADMIN_ID = uuid.UUID("55555555-5555-4555-8555-555555555555")

ASSIGNED_PROJECT_ID = uuid.UUID("aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa")
SECOND_ASSIGNED_PROJECT_ID = uuid.UUID("bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb")
OPEN_PROJECT_ID = uuid.UUID("cccccccc-cccc-4ccc-8ccc-cccccccccccc")
COMPLETED_PROJECT_ID = uuid.UUID("dddddddd-dddd-4ddd-8ddd-dddddddddddd")
OTHER_OWNER_PROJECT_ID = uuid.UUID("eeeeeeee-eeee-4eee-8eee-eeeeeeeeeeee")
MISSING_PROJECT_ID = uuid.UUID("ffffffff-ffff-4fff-8fff-ffffffffffff")

TEST_OWNER_TOKEN = "jwt-owner-token"
TEST_OTHER_OWNER_TOKEN = "jwt-other-owner-token"
TEST_FREELANCER_TOKEN = "jwt-freelancer-token"
TEST_OTHER_FREELANCER_TOKEN = "jwt-other-freelancer-token"
TEST_ADMIN_TOKEN = "jwt-admin-token"


def auth_headers(token: str = TEST_FREELANCER_TOKEN, request_id: str | None = None) -> dict[str, str]:
    headers = {"Authorization": f"Bearer {token}"}
    if request_id is not None:
        headers["X-Request-ID"] = request_id
    return headers


def build_sample_repo_zip(files: dict[str, bytes | str] | None = None) -> bytes:
    """Create a standard GitHub-style zipball archive wrapped in a root directory."""
    payload = files or {
        "alice-sample-api-abc1234/README.md": "# Sample API\n",
        "alice-sample-api-abc1234/app/main.py": "print('hello workspace')\n",
        "alice-sample-api-abc1234/requirements.txt": "fastapi>=0.115.0\n",
    }
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        for path, content in payload.items():
            zf.writestr(path, content)
    return buf.getvalue()


@dataclass
class MockIdentityState:
    users_by_token: dict[str, dict[str, Any]] = field(
        default_factory=lambda: {
            TEST_OWNER_TOKEN: {
                "id": str(OWNER_ID),
                "full_name": "Alice Owner",
                "email": "alice@example.com",
                "role": "OWNER",
                "is_active": True,
                "is_verified": True,
            },
            TEST_OTHER_OWNER_TOKEN: {
                "id": str(OTHER_OWNER_ID),
                "full_name": "Bob Owner",
                "email": "bob@example.com",
                "role": "OWNER",
                "is_active": True,
                "is_verified": True,
            },
            TEST_FREELANCER_TOKEN: {
                "id": str(FREELANCER_ID),
                "full_name": "Charlie Dev",
                "email": "charlie@example.com",
                "role": "FREELANCER",
                "is_active": True,
                "is_verified": True,
            },
            TEST_OTHER_FREELANCER_TOKEN: {
                "id": str(OTHER_FREELANCER_ID),
                "full_name": "Dana Dev",
                "email": "dana@example.com",
                "role": "FREELANCER",
                "is_active": True,
                "is_verified": True,
            },
            TEST_ADMIN_TOKEN: {
                "id": str(ADMIN_ID),
                "full_name": "Admin User",
                "email": "admin@example.com",
                "role": "ADMIN",
                "is_active": True,
                "is_verified": True,
            },
        }
    )
    force_status: int | None = None
    force_timeout: bool = False
    requests: list[httpx.Request] = field(default_factory=list)

    def build_transport(self) -> httpx.MockTransport:
        def handler(request: httpx.Request) -> httpx.Response:
            self.requests.append(request)
            if self.force_timeout:
                raise httpx.ReadTimeout("Simulated Identity timeout", request=request)
            if self.force_status is not None:
                return httpx.Response(self.force_status, json={"error": "forced"})

            auth = request.headers.get("authorization", "")
            token = auth.removeprefix("Bearer ").strip() if auth.startswith("Bearer ") else ""
            user = self.users_by_token.get(token)
            if user is None:
                return httpx.Response(401, json={"success": False, "error": {"code": "UNAUTHORIZED"}})
            return httpx.Response(200, json=user)

        return httpx.MockTransport(handler)


@dataclass
class MockProjectState:
    projects: dict[uuid.UUID, dict[str, Any]] = field(
        default_factory=lambda: {
            ASSIGNED_PROJECT_ID: {
                "id": str(ASSIGNED_PROJECT_ID),
                "owner_id": str(OWNER_ID),
                "title": "Assigned API Fix",
                "status": "ASSIGNED",
                "active_assignment": {
                    "id": "99999999-9999-4999-8999-999999999999",
                    "freelancer_id": str(FREELANCER_ID),
                    "status": "ACTIVE",
                },
            },
            SECOND_ASSIGNED_PROJECT_ID: {
                "id": str(SECOND_ASSIGNED_PROJECT_ID),
                "owner_id": str(OWNER_ID),
                "title": "Second Assigned Project",
                "status": "IN_PROGRESS",
                "active_assignment": {
                    "id": "88888888-8888-4888-8888-888888888888",
                    "freelancer_id": str(FREELANCER_ID),
                    "status": "ACTIVE",
                },
            },
            OPEN_PROJECT_ID: {
                "id": str(OPEN_PROJECT_ID),
                "owner_id": str(OWNER_ID),
                "title": "Unassigned Open Project",
                "status": "OPEN",
                "active_assignment": None,
            },
            COMPLETED_PROJECT_ID: {
                "id": str(COMPLETED_PROJECT_ID),
                "owner_id": str(OWNER_ID),
                "title": "Completed Project",
                "status": "COMPLETED",
                "active_assignment": None,
            },
            OTHER_OWNER_PROJECT_ID: {
                "id": str(OTHER_OWNER_PROJECT_ID),
                "owner_id": str(OTHER_OWNER_ID),
                "title": "Bob Private Project",
                "status": "ASSIGNED",
                "active_assignment": {
                    "id": "77777777-7777-4777-8777-777777777777",
                    "freelancer_id": str(OTHER_FREELANCER_ID),
                    "status": "ACTIVE",
                },
            },
        }
    )
    identity_state: MockIdentityState = field(default_factory=MockIdentityState)
    force_status: int | None = None
    requests: list[httpx.Request] = field(default_factory=list)

    def build_transport(self) -> httpx.MockTransport:
        def handler(request: httpx.Request) -> httpx.Response:
            self.requests.append(request)
            if self.force_status is not None:
                return httpx.Response(self.force_status, json={"detail": "forced"})

            auth = request.headers.get("authorization", "")
            token = auth.removeprefix("Bearer ").strip() if auth.startswith("Bearer ") else ""
            user = self.identity_state.users_by_token.get(token)
            if user is None:
                return httpx.Response(401, json={"error": {"code": "UNAUTHORIZED"}})

            parts = [p for p in request.url.path.split("/") if p]
            if request.method == "POST" and parts[-2:] == ["workflow", "transition"]:
                proj_uuid = uuid.UUID(parts[-3])
                proj = self.projects.get(proj_uuid)
                if proj is None:
                    return httpx.Response(404, json={"error": {"code": "PROJECT_NOT_FOUND"}})
                proj["status"] = "IN_PROGRESS"
                return httpx.Response(200, json=proj)

            try:
                proj_uuid = uuid.UUID(parts[-1])
            except ValueError:
                return httpx.Response(422, json={"error": {"code": "VALIDATION_ERROR"}})

            proj = self.projects.get(proj_uuid)
            if proj is None:
                return httpx.Response(404, json={"error": {"code": "PROJECT_NOT_FOUND"}})

            role = user["role"]
            user_id = user["id"]
            if role == "ADMIN":
                return httpx.Response(200, json=proj)
            if role == "OWNER":
                return (
                    httpx.Response(200, json=proj)
                    if proj["owner_id"] == user_id
                    else httpx.Response(403, json={"error": {"code": "FORBIDDEN"}})
                )
            if role == "FREELANCER":
                aa = proj.get("active_assignment")
                assigned_id = aa.get("freelancer_id") if isinstance(aa, dict) else None
                if proj["status"] == "OPEN" or assigned_id == user_id:
                    return httpx.Response(200, json=proj)
                return httpx.Response(403, json={"error": {"code": "FORBIDDEN"}})
            return httpx.Response(403, json={"error": {"code": "FORBIDDEN"}})

        return httpx.MockTransport(handler)


@dataclass
class MockGitHubServiceState:
    connected_projects: set[uuid.UUID] = field(
        default_factory=lambda: {
            ASSIGNED_PROJECT_ID,
            SECOND_ASSIGNED_PROJECT_ID,
            OPEN_PROJECT_ID,
            COMPLETED_PROJECT_ID,
            OTHER_OWNER_PROJECT_ID,
        }
    )
    archive_bytes: bytes = field(default_factory=build_sample_repo_zip)
    force_status: int | None = None
    requests: list[httpx.Request] = field(default_factory=list)

    def build_transport(self) -> httpx.MockTransport:
        def handler(request: httpx.Request) -> httpx.Response:
            self.requests.append(request)
            assert "github.com" not in (request.url.host or "").lower() or "test" in (request.url.host or "")
            if self.force_status is not None:
                return httpx.Response(self.force_status, json={"error": "forced"})

            parts = [p for p in request.url.path.split("/") if p]
            # /api/v1/projects/{project_id}/github/status or /snapshot
            proj_uuid = uuid.UUID(parts[3])
            action = parts[-1]
            if action == "status":
                if proj_uuid not in self.connected_projects:
                    return httpx.Response(
                        200,
                        json={
                            "project_id": str(proj_uuid),
                            "connected": False,
                            "connection_status": "NOT_CONNECTED",
                            "repository": None,
                        },
                    )
                return httpx.Response(
                    200,
                    json={
                        "project_id": str(proj_uuid),
                        "connected": True,
                        "connection_status": "ACTIVE",
                        "repository": {
                            "full_name": "alice-gh/sample-api",
                            "default_branch": "main",
                        },
                    },
                )
            if action == "snapshot":
                if proj_uuid not in self.connected_projects:
                    return httpx.Response(404, json={"error": {"code": "PROJECT_REPOSITORY_NOT_FOUND"}})
                ref = request.url.params.get("ref") or "main"
                return httpx.Response(
                    200,
                    content=self.archive_bytes,
                    headers={
                        "content-type": "application/zip",
                        "X-Repository-Full-Name": "alice-gh/sample-api",
                        "X-Repository-Ref": ref,
                    },
                )
            return httpx.Response(404, json={"error": {"code": "NOT_FOUND"}})

        return httpx.MockTransport(handler)


def build_test_settings(tmp_path: Path, **overrides: Any) -> Settings:
    db_file = (tmp_path / "workspace_test.db").as_posix()
    storage_dir = (tmp_path / "workspaces").as_posix()
    params: dict[str, Any] = {
        "APP_ENV": "test",
        "DEBUG": False,
        "DATABASE_URL": f"sqlite+aiosqlite:///{db_file}",
        "IDENTITY_SERVICE_URL": "http://identity-test.local:8001",
        "PROJECT_SERVICE_URL": "http://project-test.local:8002",
        "GITHUB_SERVICE_URL": "http://github-test.local:8003",
        "WORKSPACE_DOCKER_MODE": "mock",
        "WORKSPACE_STORAGE_ROOT": storage_dir,
        "WORKSPACE_DEFAULT_IMAGE": "python:3.13-slim",
        "WORKSPACE_ALLOWED_IMAGES": "python:3.13-slim,node:20-slim",
        "WORKSPACE_CPU_LIMIT": 1.0,
        "WORKSPACE_MEMORY_LIMIT_MB": 512,
        "WORKSPACE_PIDS_LIMIT": 128,
        "WORKSPACE_NETWORK_DISABLED": True,
        "WORKSPACE_TTL_HOURS": 24,
        "WORKSPACE_MAX_ACTIVE_PER_USER": 3,
    }
    params.update(overrides)
    return Settings(**params)


@pytest.fixture
def mock_identity_state() -> MockIdentityState:
    return MockIdentityState()


@pytest.fixture
def mock_project_state(mock_identity_state: MockIdentityState) -> MockProjectState:
    return MockProjectState(identity_state=mock_identity_state)


@pytest.fixture
def mock_github_state() -> MockGitHubServiceState:
    return MockGitHubServiceState()


@pytest.fixture
def mock_docker_adapter() -> MockDockerAdapter:
    return MockDockerAdapter()


@pytest_asyncio.fixture
async def workspace_app(
    tmp_path: Path,
    mock_identity_state: MockIdentityState,
    mock_project_state: MockProjectState,
    mock_github_state: MockGitHubServiceState,
    mock_docker_adapter: MockDockerAdapter,
) -> AsyncIterator[FastAPI]:
    settings = build_test_settings(tmp_path)
    application = create_app(
        settings=settings,
        identity_transport=mock_identity_state.build_transport(),
        project_transport=mock_project_state.build_transport(),
        github_transport=mock_github_state.build_transport(),
        docker_adapter=mock_docker_adapter,
    )
    async with application.state.engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    async with application.router.lifespan_context(application):
        yield application
    await application.state.engine.dispose()


@pytest_asyncio.fixture
async def client(workspace_app: FastAPI) -> AsyncIterator[AsyncClient]:
    async with AsyncClient(
        transport=ASGITransport(app=workspace_app),
        base_url="http://testserver",
    ) as async_client:
        yield async_client

