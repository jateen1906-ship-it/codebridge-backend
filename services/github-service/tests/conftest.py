"""Shared pytest fixtures, isolated SQLite DB, and mocked HTTPX transports."""

from __future__ import annotations

import base64
import json
import uuid
from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from typing import Any

import httpx
import pytest
import pytest_asyncio
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings
from app.models.base import Base

OWNER_ID = uuid.UUID("11111111-1111-4111-8111-111111111111")
OTHER_OWNER_ID = uuid.UUID("22222222-2222-4222-8222-222222222222")
FREELANCER_ID = uuid.UUID("33333333-3333-4333-8333-333333333333")
OTHER_FREELANCER_ID = uuid.UUID("44444444-4444-4444-8444-444444444444")
ADMIN_ID = uuid.UUID("55555555-5555-4555-8555-555555555555")

PROJECT_ID = uuid.UUID("aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa")
SECOND_PROJECT_ID = uuid.UUID("bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb")
OPEN_PROJECT_ID = uuid.UUID("cccccccc-cccc-4ccc-8ccc-cccccccccccc")
ASSIGNED_PROJECT_ID = uuid.UUID("dddddddd-dddd-4ddd-8ddd-dddddddddddd")
OTHER_OWNER_PROJECT_ID = uuid.UUID("eeeeeeee-eeee-4eee-8eee-eeeeeeeeeeee")
MISSING_PROJECT_ID = uuid.UUID("ffffffff-ffff-4fff-8fff-ffffffffffff")

TEST_ENCRYPTION_KEY = base64.urlsafe_b64encode(
    b"0123456789abcdef0123456789abcdef"
).decode("ascii")
ALTERNATE_ENCRYPTION_KEY = base64.urlsafe_b64encode(
    b"fedcba9876543210fedcba9876543210"
).decode("ascii")

TEST_OWNER_TOKEN = "jwt-owner-token"
TEST_OTHER_OWNER_TOKEN = "jwt-other-owner-token"
TEST_FREELANCER_TOKEN = "jwt-freelancer-token"
TEST_OTHER_FREELANCER_TOKEN = "jwt-other-freelancer-token"
TEST_ADMIN_TOKEN = "jwt-admin-token"


def auth_headers(token: str = TEST_OWNER_TOKEN, request_id: str | None = None) -> dict[str, str]:
    headers = {"Authorization": f"Bearer {token}"}
    if request_id is not None:
        headers["X-Request-ID"] = request_id
    return headers


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
    force_connect_error: bool = False
    requests: list[httpx.Request] = field(default_factory=list)

    def build_transport(self) -> httpx.MockTransport:
        def handler(request: httpx.Request) -> httpx.Response:
            self.requests.append(request)
            if self.force_timeout:
                raise httpx.ReadTimeout("Simulated Identity Service timeout", request=request)
            if self.force_connect_error:
                raise httpx.ConnectError("Simulated Identity connection failure", request=request)
            if self.force_status is not None:
                return httpx.Response(self.force_status, json={"error": "forced"})

            auth = request.headers.get("authorization", "")
            token = auth.removeprefix("Bearer ").strip() if auth.startswith("Bearer ") else ""
            user = self.users_by_token.get(token)
            if user is None:
                return httpx.Response(
                    401,
                    json={"success": False, "error": {"code": "UNAUTHORIZED", "message": "Invalid"}},
                )
            return httpx.Response(200, json=user)

        return httpx.MockTransport(handler)


@dataclass
class MockProjectState:
    projects: dict[uuid.UUID, dict[str, Any]] = field(
        default_factory=lambda: {
            PROJECT_ID: {
                "id": str(PROJECT_ID),
                "owner_id": str(OWNER_ID),
                "title": "CodeBridge Sample Project",
                "status": "DRAFT",
                "active_assignment": None,
            },
            SECOND_PROJECT_ID: {
                "id": str(SECOND_PROJECT_ID),
                "owner_id": str(OWNER_ID),
                "title": "Second Owner Project",
                "status": "OPEN",
                "active_assignment": None,
            },
            OPEN_PROJECT_ID: {
                "id": str(OPEN_PROJECT_ID),
                "owner_id": str(OWNER_ID),
                "title": "Open Project",
                "status": "OPEN",
                "active_assignment": None,
            },
            ASSIGNED_PROJECT_ID: {
                "id": str(ASSIGNED_PROJECT_ID),
                "owner_id": str(OWNER_ID),
                "title": "Assigned Project",
                "status": "ASSIGNED",
                "active_assignment": {
                    "id": "99999999-9999-4999-8999-999999999999",
                    "freelancer_id": str(FREELANCER_ID),
                    "status": "ACTIVE",
                },
            },
            OTHER_OWNER_PROJECT_ID: {
                "id": str(OTHER_OWNER_PROJECT_ID),
                "owner_id": str(OTHER_OWNER_ID),
                "title": "Other Owner Private Project",
                "status": "DRAFT",
                "active_assignment": None,
            },
        }
    )
    identity_state: MockIdentityState = field(default_factory=MockIdentityState)
    force_status: int | None = None
    force_timeout: bool = False
    force_connect_error: bool = False
    requests: list[httpx.Request] = field(default_factory=list)

    def build_transport(self) -> httpx.MockTransport:
        def handler(request: httpx.Request) -> httpx.Response:
            self.requests.append(request)
            if self.force_timeout:
                raise httpx.ReadTimeout("Simulated Project Service timeout", request=request)
            if self.force_connect_error:
                raise httpx.ConnectError("Simulated Project connection error", request=request)
            if self.force_status is not None:
                return httpx.Response(self.force_status, json={"detail": "forced"})

            auth = request.headers.get("authorization", "")
            token = auth.removeprefix("Bearer ").strip() if auth.startswith("Bearer ") else ""
            user = self.identity_state.users_by_token.get(token)
            if user is None:
                return httpx.Response(401, json={"error": {"code": "UNAUTHORIZED"}})

            raw_id = request.url.path.rstrip("/").split("/")[-1]
            try:
                proj_uuid = uuid.UUID(raw_id)
            except ValueError:
                return httpx.Response(422, json={"error": {"code": "VALIDATION_ERROR"}})

            project = self.projects.get(proj_uuid)
            if project is None:
                return httpx.Response(404, json={"error": {"code": "PROJECT_NOT_FOUND"}})

            role = user["role"]
            user_id = user["id"]
            if role == "ADMIN":
                return httpx.Response(200, json=project)
            if role == "OWNER":
                if project["owner_id"] == user_id:
                    return httpx.Response(200, json=project)
                return httpx.Response(403, json={"error": {"code": "FORBIDDEN"}})
            if role == "FREELANCER":
                active_assignment = project.get("active_assignment")
                assigned_id = (
                    active_assignment.get("freelancer_id")
                    if isinstance(active_assignment, dict)
                    else None
                )
                if project["status"] == "OPEN" or assigned_id == user_id:
                    return httpx.Response(200, json=project)
                return httpx.Response(403, json={"error": {"code": "FORBIDDEN"}})
            return httpx.Response(403, json={"error": {"code": "FORBIDDEN"}})

        return httpx.MockTransport(handler)


@dataclass
class MockGitHubState:
    oauth_tokens_by_code: dict[str, dict[str, Any]] = field(
        default_factory=lambda: {
            "valid-code": {
                "access_token": "gho_super_secret_oauth_token_123456",
                "scope": "repo read:user",
                "token_type": "bearer",
            },
            "second-code": {
                "access_token": "gho_refreshed_secret_oauth_token_7890",
                "scope": "repo read:user",
                "token_type": "bearer",
            },
            "other-gh-user-code": {
                "access_token": "gho_other_github_user_token_9999",
                "scope": "repo read:user",
                "token_type": "bearer",
            },
        }
    )
    users_by_token: dict[str, dict[str, Any]] = field(
        default_factory=lambda: {
            "gho_super_secret_oauth_token_123456": {
                "id": 424242,
                "login": "alice-gh",
                "name": "Alice GitHub",
            },
            "gho_refreshed_secret_oauth_token_7890": {
                "id": 424242,
                "login": "alice-gh-renamed",
                "name": "Alice GitHub",
            },
            "gho_other_github_user_token_9999": {
                "id": 999999,
                "login": "bob-different-gh",
                "name": "Bob GitHub",
            },
        }
    )
    repos: dict[str, dict[str, Any]] = field(
        default_factory=lambda: {
            "alice-gh/sample-api": {
                "id": 1001,
                "name": "sample-api",
                "full_name": "alice-gh/sample-api",
                "owner": {"login": "alice-gh"},
                "private": True,
                "default_branch": "main",
                "description": "FastAPI backend repository",
                "html_url": "https://github.com/alice-gh/sample-api",
                "archived": False,
                "fork": False,
                "permissions": {"admin": True, "maintain": True, "push": True, "pull": True},
            },
            "alice-gh/second-repo": {
                "id": 1002,
                "name": "second-repo",
                "full_name": "alice-gh/second-repo",
                "owner": {"login": "alice-gh"},
                "private": False,
                "default_branch": "master",
                "description": "Public library repo",
                "html_url": "https://github.com/alice-gh/second-repo",
                "archived": False,
                "fork": True,
                "permissions": {"admin": True, "maintain": False, "push": True, "pull": True},
            },
            "alice-gh/no-pull-repo": {
                "id": 1003,
                "name": "no-pull-repo",
                "full_name": "alice-gh/no-pull-repo",
                "owner": {"login": "alice-gh"},
                "private": True,
                "default_branch": "main",
                "description": None,
                "html_url": "https://github.com/alice-gh/no-pull-repo",
                "archived": False,
                "fork": False,
                "permissions": {"admin": False, "maintain": False, "push": False, "pull": False},
            },
        }
    )
    branches: list[dict[str, Any]] = field(
        default_factory=lambda: [
            {"name": "main", "commit": {"sha": "abc1234000000000000000000000000000000001"}, "protected": True},
            {"name": "feat/payments", "commit": {"sha": "def5678000000000000000000000000000000002"}, "protected": False},
        ]
    )
    contents_by_path: dict[str, Any] = field(
        default_factory=lambda: {
            "": [
                {"name": "README.md", "path": "README.md", "type": "file", "size": 18, "sha": "blob1"},
                {"name": "src", "path": "src", "type": "dir", "size": 0, "sha": "tree1"},
            ],
            "README.md": {
                "type": "file",
                "name": "README.md",
                "path": "README.md",
                "sha": "blob1",
                "size": 18,
                "encoding": "base64",
                "content": base64.b64encode(b"# Sample API\nHello").decode("ascii"),
            },
            "assets/logo.png": {
                "type": "file",
                "name": "logo.png",
                "path": "assets/logo.png",
                "sha": "blob-bin",
                "size": 8,
                "encoding": "base64",
                "content": base64.b64encode(b"\x89PNG\r\n\x1a\x00").decode("ascii"),
            },
        }
    )
    commits: list[dict[str, Any]] = field(
        default_factory=lambda: [
            {
                "sha": "abc1234000000000000000000000000000000001",
                "html_url": "https://github.com/alice-gh/sample-api/commit/abc1234",
                "commit": {
                    "message": "Initial API scaffold",
                    "author": {"name": "Alice", "date": "2026-10-01T12:00:00Z"},
                },
                "author": {"login": "alice-gh"},
            }
        ]
    )
    list_has_next_page: bool = False
    forced_token_status: int | None = None
    forced_api_status: int | None = None
    forced_rate_limit_403: bool = False
    force_timeout: bool = False
    token_exchange_calls: int = 0
    api_calls: list[httpx.Request] = field(default_factory=list)

    def build_transport(self) -> httpx.MockTransport:
        def handler(request: httpx.Request) -> httpx.Response:
            self.api_calls.append(request)
            assert request.url.host in {"github.com", "api.github.com"}
            if self.force_timeout:
                raise httpx.ReadTimeout("Simulated GitHub timeout", request=request)

            if request.url.host == "github.com" and request.url.path == "/login/oauth/access_token":
                self.token_exchange_calls += 1
                if self.forced_token_status is not None:
                    return httpx.Response(self.forced_token_status, json={"error": "server_error"})
                body = request.content.decode("utf-8")
                from urllib.parse import parse_qs

                form = parse_qs(body)
                code = (form.get("code") or [""])[0]
                token_payload = self.oauth_tokens_by_code.get(code)
                if token_payload is None:
                    return httpx.Response(200, json={"error": "bad_verification_code"})
                return httpx.Response(200, json=token_payload)

            # Enforce read-only API requests to api.github.com
            assert request.method == "GET", f"Non-GET call forbidden: {request.method} {request.url}"

            if self.forced_rate_limit_403:
                return httpx.Response(
                    403,
                    headers={"x-ratelimit-remaining": "0"},
                    json={"message": "API rate limit exceeded"},
                )
            if self.forced_api_status is not None:
                return httpx.Response(self.forced_api_status, json={"message": "forced error"})

            auth = request.headers.get("authorization", "")
            token = auth.removeprefix("Bearer ").strip()
            gh_user = self.users_by_token.get(token)
            if gh_user is None:
                return httpx.Response(401, json={"message": "Bad credentials"})

            path = request.url.path
            if path == "/user":
                return httpx.Response(200, json=gh_user)

            if path == "/user/repos":
                headers = (
                    {"Link": '<https://api.github.com/user/repos?page=2>; rel="next"'}
                    if self.list_has_next_page
                    else {}
                )
                return httpx.Response(
                    200,
                    headers=headers,
                    json=[
                        self.repos["alice-gh/sample-api"],
                        self.repos["alice-gh/second-repo"],
                    ],
                )

            if path.startswith("/repos/"):
                rest = path[len("/repos/") :]
                parts = rest.split("/")
                if len(parts) < 2:
                    return httpx.Response(404, json={"message": "Not Found"})
                repo_full_name = f"{parts[0]}/{parts[1]}"
                repo_obj = self.repos.get(repo_full_name)
                if repo_obj is None:
                    return httpx.Response(404, json={"message": "Not Found"})

                if len(parts) == 2:
                    return httpx.Response(200, json=repo_obj)
                if len(parts) == 3 and parts[2] == "branches":
                    return httpx.Response(200, json=self.branches)
                if len(parts) == 3 and parts[2] == "commits":
                    return httpx.Response(200, json=self.commits)
                if parts[2] == "contents":
                    rel_path = "/".join(parts[3:])
                    content_obj = self.contents_by_path.get(rel_path)
                    if content_obj is None:
                        return httpx.Response(404, json={"message": "Not Found"})
                    return httpx.Response(200, json=content_obj)

            return httpx.Response(404, json={"message": "Not Found"})

        return httpx.MockTransport(handler)


def build_test_settings(db_path: str, **overrides: Any) -> Settings:
    """Create isolated Settings backed by a temporary SQLite file."""
    params: dict[str, Any] = {
        "APP_ENV": "test",
        "DEBUG": False,
        "DATABASE_URL": f"sqlite+aiosqlite:///{db_path}",
        "IDENTITY_SERVICE_URL": "http://identity-test.local:8001",
        "PROJECT_SERVICE_URL": "http://project-test.local:8002",
        "GITHUB_CLIENT_ID": "test-client-id",
        "GITHUB_CLIENT_SECRET": "test-client-secret-value-never-leaked",
        "GITHUB_OAUTH_CALLBACK_URL": "http://localhost:8000/api/v1/github/oauth/callback",
        "GITHUB_OAUTH_SCOPES": "repo read:user",
        "GITHUB_TOKEN_ENCRYPTION_KEY": TEST_ENCRYPTION_KEY,
        "GITHUB_API_PAGE_SIZE": 50,
        "GITHUB_API_MAX_PAGES": 3,
        "GITHUB_CONTENTS_MAX_BYTES": 262144,
        "GITHUB_CONTENTS_MAX_ENTRIES": 200,
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
def mock_github_state() -> MockGitHubState:
    return MockGitHubState()


@pytest.fixture
def test_settings(tmp_path) -> Settings:
    db_file = (tmp_path / "test_github.db").as_posix()
    return build_test_settings(db_file)


@pytest_asyncio.fixture
async def github_app(
    test_settings: Settings,
    mock_identity_state: MockIdentityState,
    mock_project_state: MockProjectState,
    mock_github_state: MockGitHubState,
) -> AsyncIterator[FastAPI]:
    from app.main import create_app

    app = create_app(
        settings=test_settings,
        identity_transport=mock_identity_state.build_transport(),
        project_transport=mock_project_state.build_transport(),
        github_transport=mock_github_state.build_transport(),
    )
    async with app.state.engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    async with app.router.lifespan_context(app):
        yield app


@pytest_asyncio.fixture
async def client(github_app: FastAPI) -> AsyncIterator[AsyncClient]:
    transport = ASGITransport(app=github_app)
    async with AsyncClient(transport=transport, base_url="http://testserver") as async_client:
        yield async_client


@pytest_asyncio.fixture
async def db_session(github_app: FastAPI) -> AsyncIterator[AsyncSession]:
    async with github_app.state.session_factory() as session:
        yield session


async def connect_owner_github_account(
    client: AsyncClient,
    *,
    token: str = TEST_OWNER_TOKEN,
    project_id: uuid.UUID = PROJECT_ID,
    code: str = "valid-code",
) -> dict[str, Any]:
    """Helper driving real OAuth authorize -> callback flow to establish an ACTIVE connection."""
    auth_resp = await client.get(
        "/api/v1/github/oauth/authorize",
        params={"project_id": str(project_id)},
        headers=auth_headers(token),
        follow_redirects=False,
    )
    assert auth_resp.status_code == 302
    location = auth_resp.headers["location"]
    from urllib.parse import parse_qs, urlparse

    raw_state = parse_qs(urlparse(location).query)["state"][0]
    cb_resp = await client.get(
        "/api/v1/github/oauth/callback",
        params={"code": code, "state": raw_state},
    )
    assert cb_resp.status_code == 200
    return json.loads(cb_resp.content)

