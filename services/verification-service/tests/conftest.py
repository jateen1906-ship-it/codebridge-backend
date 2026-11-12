"""Pytest fixtures and mock upstream services for Verification Service."""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import httpx
import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient

from app.clients.service_clients import (
    GitHubMetadataClient,
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
APPROVED_SUBMISSION_ID = uuid.UUID("bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb")
UNAPPROVED_SUBMISSION_ID = uuid.UUID("cccccccc-cccc-4ccc-8ccc-cccccccccccc")

OWNER_TOKEN = "token-owner-alice"
OTHER_OWNER_TOKEN = "token-owner-bob"
FREELANCER_TOKEN = "token-freelancer-charlie"
ADMIN_TOKEN = "token-admin"


def auth_headers(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


@dataclass
class MockReviewState:
    submissions: dict[uuid.UUID, dict[str, Any]] = field(
        default_factory=lambda: {
            APPROVED_SUBMISSION_ID: {
                "id": str(APPROVED_SUBMISSION_ID),
                "project_id": str(PROJECT_ID),
                "freelancer_id": str(FREELANCER_ID),
                "owner_id": str(OWNER_ID),
                "title": "Approved Fix v2",
                "status": "APPROVED",
                "commit_sha": "deadbeef1234",
                "reviewed_at": datetime.now(timezone.utc).isoformat(),
            },
            UNAPPROVED_SUBMISSION_ID: {
                "id": str(UNAPPROVED_SUBMISSION_ID),
                "project_id": str(PROJECT_ID),
                "freelancer_id": str(FREELANCER_ID),
                "owner_id": str(OWNER_ID),
                "title": "Pending Submission",
                "status": "UNDER_REVIEW",
                "commit_sha": "abc000",
                "reviewed_at": None,
            },
        }
    )

    def build_transport(self) -> httpx.MockTransport:
        def handler(request: httpx.Request) -> httpx.Response:
            sub_id = uuid.UUID(request.url.path.split("/")[-1])
            item = self.submissions.get(sub_id)
            if item is None:
                return httpx.Response(404, json={"error": {"code": "SUBMISSION_NOT_FOUND"}})
            return httpx.Response(200, json=item)

        return httpx.MockTransport(handler)


@pytest.fixture
def mock_review_state() -> MockReviewState:
    return MockReviewState()


@pytest_asyncio.fixture
async def app(tmp_path: Path, mock_review_state: MockReviewState):
    db_file = tmp_path / "verification_test.db"
    settings = Settings(
        APP_ENV="test",
        DATABASE_URL=f"sqlite+aiosqlite:///{db_file.as_posix()}",
        VERIFICATION_SIGNING_SECRET="test-signing-secret-with-plenty-of-entropy-1234",
    )
    application = create_app(settings_override=settings)

    users = {
        OWNER_TOKEN: {
            "id": str(OWNER_ID),
            "full_name": "Alice Owner",
            "email": "alice@example.com",
            "role": "OWNER",
        },
        OTHER_OWNER_TOKEN: {
            "id": str(OTHER_OWNER_ID),
            "full_name": "Bob Owner",
            "email": "bob@example.com",
            "role": "OWNER",
        },
        FREELANCER_TOKEN: {
            "id": str(FREELANCER_ID),
            "full_name": "Charlie Dev",
            "email": "charlie@example.com",
            "role": "FREELANCER",
        },
        ADMIN_TOKEN: {
            "id": str(ADMIN_ID),
            "full_name": "Admin",
            "email": "admin@example.com",
            "role": "ADMIN",
        },
    }

    def id_handler(req: httpx.Request) -> httpx.Response:
        tok = req.headers.get("authorization", "").removeprefix("Bearer ").strip()
        user = users.get(tok)
        return httpx.Response(200, json=user) if user else httpx.Response(401)

    def proj_handler(req: httpx.Request) -> httpx.Response:
        pid = uuid.UUID(req.url.path.split("/")[-1])
        if pid != PROJECT_ID:
            return httpx.Response(404)
        return httpx.Response(
            200,
            json={
                "id": str(PROJECT_ID),
                "owner_id": str(OWNER_ID),
                "title": "Fix Payment Webhook",
                "status": "COMPLETED",
                "active_assignment": {"freelancer_id": str(FREELANCER_ID), "status": "COMPLETED"},
            },
        )

    def gh_handler(_req: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={"connected": True, "repository": {"full_name": "alice-gh/sample-api"}},
        )

    application.state.identity_client = IdentityClient(
        base_url="http://id.local",
        timeout_seconds=5.0,
        transport=httpx.MockTransport(id_handler),
    )
    application.state.project_client = ProjectClient(
        base_url="http://proj.local",
        timeout_seconds=5.0,
        transport=httpx.MockTransport(proj_handler),
    )
    application.state.review_client = ReviewClient(
        base_url="http://rev.local",
        timeout_seconds=5.0,
        transport=mock_review_state.build_transport(),
    )
    application.state.github_client = GitHubMetadataClient(
        base_url="http://gh.local",
        timeout_seconds=5.0,
        transport=httpx.MockTransport(gh_handler),
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
