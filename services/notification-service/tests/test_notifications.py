"""Pytest fixtures and Notification Service integration tests."""

from __future__ import annotations

import uuid
from pathlib import Path

import httpx
import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select

from app.clients.identity_client import IdentityClient
from app.core.config import Settings
from app.main import create_app
from app.models import Base, EmailDeliveryLog

USER_1_ID = uuid.UUID("11111111-1111-4111-8111-111111111111")
USER_2_ID = uuid.UUID("22222222-2222-4222-8222-222222222222")
ADMIN_ID = uuid.UUID("55555555-5555-4555-8555-555555555555")

USER_1_TOKEN = "tok-u1"
USER_2_TOKEN = "tok-u2"
ADMIN_TOKEN = "tok-admin"
INTERNAL_KEY = "test-internal-key-secret"


@pytest_asyncio.fixture
async def app(tmp_path: Path):
    db_file = tmp_path / "notif_test.db"
    settings = Settings(
        APP_ENV="test",
        DATABASE_URL=f"sqlite+aiosqlite:///{db_file.as_posix()}",
        INTERNAL_SERVICE_KEY=INTERNAL_KEY,
    )
    application = create_app(settings_override=settings)
    users = {
        USER_1_TOKEN: {"id": str(USER_1_ID), "full_name": "Alice", "email": "a@example.com", "role": "OWNER"},
        USER_2_TOKEN: {"id": str(USER_2_ID), "full_name": "Charlie", "email": "c@example.com", "role": "FREELANCER"},
        ADMIN_TOKEN: {"id": str(ADMIN_ID), "full_name": "Admin", "email": "admin@example.com", "role": "ADMIN"},
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
    async with application.state.engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield application
    await application.state.engine.dispose()


@pytest_asyncio.fixture
async def client(app):
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://testserver") as c:
        yield c


@pytest.mark.asyncio
async def test_notification_intake_dedupe_isolation_and_preferences(client: AsyncClient, app) -> None:
    assert (await client.get("/health")).status_code == 200
    assert (await client.get("/ready")).status_code == 200

    # Non-admin user cannot publish notification events directly
    assert (
        await client.post(
            "/api/v1/notifications/events",
            json={
                "user_id": str(USER_1_ID),
                "event_type": "ASSIGNMENT_CREATED",
                "title": "Forbid",
                "message": "Should fail.",
            },
            headers={"Authorization": f"Bearer {USER_1_TOKEN}"},
        )
    ).status_code == 403

    # Wrong internal key rejected
    assert (
        await client.post(
            "/api/v1/notifications/events",
            json={
                "user_id": str(USER_1_ID),
                "event_type": "ASSIGNMENT_CREATED",
                "title": "Forbid",
                "message": "Should fail.",
            },
            headers={"X-Service-Key": "wrong-key"},
        )
    ).status_code == 403

    # Valid internal service event ingestion + idempotent deduplication
    ev_payload = {
        "user_id": str(USER_1_ID),
        "event_type": "SUBMISSION_SUBMITTED",
        "title": "New Submission",
        "message": "Charlie submitted v1.",
        "resource_type": "project",
        "resource_id": str(uuid.uuid4()),
        "idempotency_key": "sub-ev-1",
    }
    first = await client.post(
        "/api/v1/notifications/events",
        json=ev_payload,
        headers={"X-Service-Key": INTERNAL_KEY},
    )
    assert first.status_code == 201
    dup = await client.post(
        "/api/v1/notifications/events",
        json=ev_payload,
        headers={"X-Service-Key": INTERNAL_KEY},
    )
    assert dup.status_code == 201
    assert dup.json()["id"] == first.json()["id"]

    # Isolation: USER_2 cannot see USER_1's notification or mark it read
    u2_list = await client.get(
        "/api/v1/notifications",
        headers={"Authorization": f"Bearer {USER_2_TOKEN}"},
    )
    assert u2_list.status_code == 200
    assert u2_list.json()["total"] == 0
    assert (
        await client.post(
            f"/api/v1/notifications/{first.json()['id']}/read",
            headers={"Authorization": f"Bearer {USER_2_TOKEN}"},
        )
    ).status_code == 403

    # USER_1 lists & marks read
    u1_list = await client.get(
        "/api/v1/notifications?unread_only=true",
        headers={"Authorization": f"Bearer {USER_1_TOKEN}"},
    )
    assert u1_list.json()["total"] == 1
    assert u1_list.json()["unread_count"] == 1

    marked = await client.post(
        f"/api/v1/notifications/{first.json()['id']}/read",
        headers={"Authorization": f"Bearer {USER_1_TOKEN}"},
    )
    assert marked.json()["is_read"] is True

    # Preferences disable email & verify EmailDeliveryLog records SKIPPED_PREFERENCE
    pref_upd = await client.put(
        "/api/v1/notifications/preferences",
        json={"in_app_enabled": True, "email_enabled": False, "muted_event_types": []},
        headers={"Authorization": f"Bearer {USER_1_TOKEN}"},
    )
    assert pref_upd.status_code == 200
    assert pref_upd.json()["email_enabled"] is False

    ev2 = await client.post(
        "/api/v1/notifications/events",
        json={
            "user_id": str(USER_1_ID),
            "event_type": "ESCROW_RELEASED",
            "title": "Payout Completed",
            "message": "Escrow paid.",
            "idempotency_key": "esc-ev-2",
        },
        headers={"X-Service-Key": INTERNAL_KEY},
    )
    assert ev2.status_code == 201

    async with app.state.session_factory() as session:
        logs = list((await session.execute(select(EmailDeliveryLog).order_by(EmailDeliveryLog.created_at.asc()))).scalars().all())
        assert [l.delivery_status for l in logs] == ["SENT_MOCK", "SKIPPED_PREFERENCE"]

    read_all = await client.post(
        "/api/v1/notifications/read-all",
        headers={"Authorization": f"Bearer {USER_1_TOKEN}"},
    )
    assert read_all.status_code == 200
    assert read_all.json()["marked_count"] == 1
