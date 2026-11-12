"""Pytest fixtures and mock upstream transports for Payment Service."""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import httpx
import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient

from app.clients.service_clients import (
    IdentityClient,
    ProjectClient,
    ReviewClient,
    TelemetryRelayClient,
)
from app.core.config import Settings
from app.main import create_app
from app.models import Base

OWNER_ID = uuid.UUID("11111111-1111-4111-8111-111111111111")
OTHER_OWNER_ID = uuid.UUID("22222222-2222-4222-8222-222222222222")
FREELANCER_ID = uuid.UUID("33333333-3333-4333-8333-333333333333")
ADMIN_ID = uuid.UUID("55555555-5555-4555-8555-555555555555")

PROJECT_ID = uuid.UUID("aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa")
SECOND_PROJECT_ID = uuid.UUID("bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb")

OWNER_TOKEN = "token-owner-alice"
OTHER_OWNER_TOKEN = "token-owner-bob"
FREELANCER_TOKEN = "token-freelancer-charlie"
ADMIN_TOKEN = "token-admin"


def auth_headers(token: str, *, idem: str | None = None) -> dict[str, str]:
    hdrs = {"Authorization": f"Bearer {token}"}
    if idem:
        hdrs["Idempotency-Key"] = idem
    return hdrs


@dataclass
class MockReviewState:
    approved_projects: set[uuid.UUID] = field(default_factory=set)

    def build_transport(self) -> httpx.MockTransport:
        def handler(request: httpx.Request) -> httpx.Response:
            pid = uuid.UUID(request.url.path.split("/")[-1])
            approved = pid in self.approved_projects
            return httpx.Response(
                200,
                json={
                    "project_id": str(pid),
                    "approved": approved,
                    "approved_submission_id": str(uuid.uuid4()) if approved else None,
                    "approved_freelancer_id": str(FREELANCER_ID) if approved else None,
                },
            )

        return httpx.MockTransport(handler)


@pytest.fixture
def mock_review_state() -> MockReviewState:
    return MockReviewState()


@pytest_asyncio.fixture
async def app(tmp_path: Path, mock_review_state: MockReviewState):
    db_file = tmp_path / "payment_test.db"
    settings = Settings(
        APP_ENV="test",
        DATABASE_URL=f"sqlite+aiosqlite:///{db_file.as_posix()}",
    )
    application = create_app(settings_override=settings)

    users: dict[str, dict[str, Any]] = {
        OWNER_TOKEN: {"id": str(OWNER_ID), "full_name": "Alice", "email": "alice@example.com", "role": "OWNER"},
        OTHER_OWNER_TOKEN: {"id": str(OTHER_OWNER_ID), "full_name": "Bob", "email": "bob@example.com", "role": "OWNER"},
        FREELANCER_TOKEN: {"id": str(FREELANCER_ID), "full_name": "Charlie", "email": "charlie@example.com", "role": "FREELANCER"},
        ADMIN_TOKEN: {"id": str(ADMIN_ID), "full_name": "Admin", "email": "admin@example.com", "role": "ADMIN"},
    }
    projects = {
        PROJECT_ID: {
            "id": str(PROJECT_ID),
            "owner_id": str(OWNER_ID),
            "title": "Fix Payment Webhook",
            "status": "ASSIGNED",
            "budget": "1500.00",
            "currency": "USD",
            "active_assignment": {"freelancer_id": str(FREELANCER_ID), "status": "ACTIVE"},
        },
        SECOND_PROJECT_ID: {
            "id": str(SECOND_PROJECT_ID),
            "owner_id": str(OWNER_ID),
            "title": "Cancelled/Refundable Project",
            "status": "OPEN",
            "budget": "750.00",
            "currency": "USD",
            "active_assignment": None,
        },
    }

    application.state.identity_client = IdentityClient(
        base_url="http://id.local",
        timeout_seconds=5.0,
        transport=httpx.MockTransport(
            lambda req: httpx.Response(200, json=users[req.headers["authorization"].removeprefix("Bearer ").strip()])
            if req.headers.get("authorization", "").removeprefix("Bearer ").strip() in users
            else httpx.Response(401)
        ),
    )
    application.state.project_client = ProjectClient(
        base_url="http://proj.local",
        timeout_seconds=5.0,
        transport=httpx.MockTransport(
            lambda req: httpx.Response(200, json=projects[uuid.UUID(req.url.path.split("/")[-1])])
            if uuid.UUID(req.url.path.split("/")[-1]) in projects
            else httpx.Response(404)
        ),
    )
    application.state.review_client = ReviewClient(
        base_url="http://rev.local",
        timeout_seconds=5.0,
        transport=mock_review_state.build_transport(),
    )
    application.state.telemetry_client = TelemetryRelayClient(
        notification_base_url="http://notif.local",
        audit_base_url="http://audit.local",
        internal_service_key=settings.INTERNAL_SERVICE_KEY,
        timeout_seconds=5.0,
        transport=httpx.MockTransport(lambda _r: httpx.Response(201, json={"ok": True})),
    )

    async with application.state.engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    yield application
    await application.state.engine.dispose()


@pytest_asyncio.fixture
async def client(app):
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://testserver") as c:
        yield c
