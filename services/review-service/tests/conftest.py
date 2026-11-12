"""Pytest fixtures and HTTPX mock transports for Review Service."""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import httpx
import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient

from app.clients.identity_client import IdentityClient
from app.clients.project_client import ProjectClient
from app.clients.workspace_client import TelemetryRelayClient, WorkspaceClient
from app.core.config import Settings
from app.main import create_app
from app.models import Base

OWNER_ID = uuid.UUID("11111111-1111-4111-8111-111111111111")
OTHER_OWNER_ID = uuid.UUID("22222222-2222-4222-8222-222222222222")
FREELANCER_ID = uuid.UUID("33333333-3333-4333-8333-333333333333")
OTHER_FREELANCER_ID = uuid.UUID("44444444-4444-4444-8444-444444444444")
ADMIN_ID = uuid.UUID("55555555-5555-4555-8555-555555555555")

ASSIGNED_PROJECT_ID = uuid.UUID("aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa")
OPEN_PROJECT_ID = uuid.UUID("cccccccc-cccc-4ccc-8ccc-cccccccccccc")
COMPLETED_PROJECT_ID = uuid.UUID("dddddddd-dddd-4ddd-8ddd-dddddddddddd")

OWNER_TOKEN = "token-owner-alice"
OTHER_OWNER_TOKEN = "token-owner-bob"
FREELANCER_TOKEN = "token-freelancer-charlie"
OTHER_FREELANCER_TOKEN = "token-freelancer-dana"
ADMIN_TOKEN = "token-admin"


def auth_headers(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


@dataclass
class MockIdentityState:
    users_by_token: dict[str, dict[str, Any]] = field(
        default_factory=lambda: {
            OWNER_TOKEN: {
                "id": str(OWNER_ID),
                "full_name": "Alice Owner",
                "email": "alice@example.com",
                "role": "OWNER",
                "is_active": True,
                "is_verified": True,
            },
            OTHER_OWNER_TOKEN: {
                "id": str(OTHER_OWNER_ID),
                "full_name": "Bob Owner",
                "email": "bob@example.com",
                "role": "OWNER",
                "is_active": True,
                "is_verified": True,
            },
            FREELANCER_TOKEN: {
                "id": str(FREELANCER_ID),
                "full_name": "Charlie Dev",
                "email": "charlie@example.com",
                "role": "FREELANCER",
                "is_active": True,
                "is_verified": True,
            },
            OTHER_FREELANCER_TOKEN: {
                "id": str(OTHER_FREELANCER_ID),
                "full_name": "Dana Dev",
                "email": "dana@example.com",
                "role": "FREELANCER",
                "is_active": True,
                "is_verified": True,
            },
            ADMIN_TOKEN: {
                "id": str(ADMIN_ID),
                "full_name": "Admin",
                "email": "admin@example.com",
                "role": "ADMIN",
                "is_active": True,
                "is_verified": True,
            },
        }
    )

    def build_transport(self) -> httpx.MockTransport:
        def handler(request: httpx.Request) -> httpx.Response:
            auth = request.headers.get("authorization", "")
            token = auth.removeprefix("Bearer ").strip() if auth.startswith("Bearer ") else ""
            user = self.users_by_token.get(token)
            if user is None:
                return httpx.Response(401, json={"error": {"code": "UNAUTHORIZED"}})
            return httpx.Response(200, json=user)

        return httpx.MockTransport(handler)


@dataclass
class MockProjectState:
    identity_state: MockIdentityState = field(default_factory=MockIdentityState)
    projects: dict[uuid.UUID, dict[str, Any]] = field(
        default_factory=lambda: {
            ASSIGNED_PROJECT_ID: {
                "id": str(ASSIGNED_PROJECT_ID),
                "owner_id": str(OWNER_ID),
                "title": "Fix Payment Webhook",
                "status": "ASSIGNED",
                "active_assignment": {
                    "id": str(uuid.uuid4()),
                    "freelancer_id": str(FREELANCER_ID),
                    "status": "ACTIVE",
                },
            },
            OPEN_PROJECT_ID: {
                "id": str(OPEN_PROJECT_ID),
                "owner_id": str(OWNER_ID),
                "title": "Open Project",
                "status": "OPEN",
                "active_assignment": None,
            },
            COMPLETED_PROJECT_ID: {
                "id": str(COMPLETED_PROJECT_ID),
                "owner_id": str(OWNER_ID),
                "title": "Done Project",
                "status": "COMPLETED",
                "active_assignment": None,
            },
        }
    )
    force_transition_status: int | None = None
    transitions: list[dict[str, Any]] = field(default_factory=list)

    def build_transport(self) -> httpx.MockTransport:
        import json

        def handler(request: httpx.Request) -> httpx.Response:
            auth = request.headers.get("authorization", "")
            token = auth.removeprefix("Bearer ").strip() if auth.startswith("Bearer ") else ""
            user = self.identity_state.users_by_token.get(token)
            if user is None:
                return httpx.Response(401, json={"error": {"code": "UNAUTHORIZED"}})

            parts = [p for p in request.url.path.split("/") if p]
            if request.method == "POST" and parts[-2:] == ["workflow", "transition"]:
                if self.force_transition_status is not None:
                    return httpx.Response(self.force_transition_status, json={"detail": "forced"})
                proj_uuid = uuid.UUID(parts[-3])
                proj = self.projects.get(proj_uuid)
                if proj is None:
                    return httpx.Response(404, json={"error": {"code": "PROJECT_NOT_FOUND"}})
                payload = json.loads(request.content.decode("utf-8"))
                self.transitions.append(payload)
                target = payload["target_status"]
                if target == "COMPLETED" and not payload.get("approval_verified"):
                    return httpx.Response(409, json={"error": {"code": "PROJECT_STATUS_CONFLICT"}})
                proj["status"] = target
                if target == "COMPLETED" and proj.get("active_assignment"):
                    proj["active_assignment"]["status"] = "COMPLETED"
                return httpx.Response(200, json=proj)

            proj_uuid = uuid.UUID(parts[-1])
            proj = self.projects.get(proj_uuid)
            if proj is None:
                return httpx.Response(404, json={"error": {"code": "PROJECT_NOT_FOUND"}})

            if user["role"] == "ADMIN":
                return httpx.Response(200, json=proj)
            if user["role"] == "OWNER":
                return (
                    httpx.Response(200, json=proj)
                    if proj["owner_id"] == user["id"]
                    else httpx.Response(403, json={"error": {"code": "FORBIDDEN"}})
                )
            aa = proj.get("active_assignment")
            assigned_id = aa.get("freelancer_id") if isinstance(aa, dict) else None
            if proj["status"] == "OPEN" or assigned_id == user["id"]:
                return httpx.Response(200, json=proj)
            return httpx.Response(403, json={"error": {"code": "FORBIDDEN"}})

        return httpx.MockTransport(handler)


@dataclass
class MockTelemetryState:
    notifications: list[dict[str, Any]] = field(default_factory=list)
    audits: list[dict[str, Any]] = field(default_factory=list)

    def build_transport(self) -> httpx.MockTransport:
        import json

        def handler(request: httpx.Request) -> httpx.Response:
            payload = json.loads(request.content.decode("utf-8"))
            if request.url.path.endswith("/notifications/events"):
                self.notifications.append(payload)
            elif request.url.path.endswith("/audit/events"):
                self.audits.append(payload)
            return httpx.Response(201, json={"status": "recorded"})

        return httpx.MockTransport(handler)


@pytest.fixture
def mock_identity_state() -> MockIdentityState:
    return MockIdentityState()


@pytest.fixture
def mock_project_state(mock_identity_state: MockIdentityState) -> MockProjectState:
    return MockProjectState(identity_state=mock_identity_state)


@pytest.fixture
def mock_telemetry_state() -> MockTelemetryState:
    return MockTelemetryState()


@pytest_asyncio.fixture
async def app(
    tmp_path: Path,
    mock_identity_state: MockIdentityState,
    mock_project_state: MockProjectState,
    mock_telemetry_state: MockTelemetryState,
):
    db_file = tmp_path / "review_test.db"
    settings = Settings(
        APP_ENV="test",
        DATABASE_URL=f"sqlite+aiosqlite:///{db_file.as_posix()}",
        IDENTITY_SERVICE_URL="http://identity-test.local:8001",
        PROJECT_SERVICE_URL="http://project-test.local:8002",
        WORKSPACE_SERVICE_URL="http://workspace-test.local:8005",
        NOTIFICATION_SERVICE_URL="http://notification-test.local:8009",
        AUDIT_SERVICE_URL="http://audit-test.local:8011",
    )
    application = create_app(settings_override=settings)
    application.state.identity_client = IdentityClient(
        base_url=settings.IDENTITY_SERVICE_URL,
        timeout_seconds=5.0,
        transport=mock_identity_state.build_transport(),
    )
    application.state.project_client = ProjectClient(
        base_url=settings.PROJECT_SERVICE_URL,
        timeout_seconds=5.0,
        transport=mock_project_state.build_transport(),
    )
    application.state.workspace_client = WorkspaceClient(
        base_url=settings.WORKSPACE_SERVICE_URL,
        timeout_seconds=5.0,
        transport=httpx.MockTransport(lambda req: httpx.Response(404)),
    )
    application.state.telemetry_client = TelemetryRelayClient(
        notification_base_url=settings.NOTIFICATION_SERVICE_URL,
        audit_base_url=settings.AUDIT_SERVICE_URL,
        internal_service_key=settings.INTERNAL_SERVICE_KEY,
        timeout_seconds=5.0,
        transport=mock_telemetry_state.build_transport(),
    )

    async with application.state.engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    yield application
    await application.state.engine.dispose()


@pytest_asyncio.fixture
async def client(app):
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://testserver") as async_client:
        yield async_client

