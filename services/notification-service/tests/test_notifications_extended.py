"""Extended Notification Service tests covering auth, preferences, WebSocket streaming, and readiness."""

from __future__ import annotations

import uuid

import httpx
import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import create_async_engine
from starlette.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from tests.test_notifications import INTERNAL_KEY, USER_1_ID, USER_1_TOKEN, USER_2_TOKEN, app, client


@pytest.mark.asyncio
async def test_degraded_database_readiness_returns_503(client: AsyncClient, app) -> None:
    original_engine = app.state.engine
    broken_engine = create_async_engine("sqlite+aiosqlite:///Z:/nonexistent_dir_xyz/missing.db")
    app.state.engine = broken_engine
    try:
        res = await client.get("/ready")
        assert res.status_code == 503
        assert res.json()["status"] == "not_ready"
    finally:
        app.state.engine = original_engine
        await broken_engine.dispose()


@pytest.mark.asyncio
async def test_auth_missing_validation_and_muted_events(client: AsyncClient) -> None:
    assert (await client.get("/api/v1/notifications")).status_code == 401
    assert (await client.get("/api/v1/notifications/preferences")).status_code == 401

    assert (
        await client.post(
            f"/api/v1/notifications/{uuid.uuid4()}/read",
            headers={"Authorization": f"Bearer {USER_1_TOKEN}"},
        )
    ).status_code == 404

    assert (
        await client.put(
            "/api/v1/notifications/preferences",
            json={"in_app_enabled": True, "email_enabled": True, "muted_event_types": [], "extra": 1},
            headers={"Authorization": f"Bearer {USER_1_TOKEN}"},
        )
    ).status_code == 422

    # Muted event type is auto-marked read
    await client.put(
        "/api/v1/notifications/preferences",
        json={"in_app_enabled": True, "email_enabled": True, "muted_event_types": ["NOISY_EVENT"]},
        headers={"Authorization": f"Bearer {USER_1_TOKEN}"},
    )
    muted = await client.post(
        "/api/v1/notifications/events",
        json={
            "user_id": str(USER_1_ID),
            "event_type": "NOISY_EVENT",
            "title": "Muted",
            "message": "Should be auto read.",
        },
        headers={"X-Service-Key": INTERNAL_KEY},
    )
    assert muted.status_code == 201
    assert muted.json()["is_read"] is True


@pytest.mark.asyncio
async def test_identity_outage_returns_503(client: AsyncClient, app) -> None:
    original_transport = app.state.identity_client._transport
    app.state.identity_client._transport = httpx.MockTransport(
        lambda req: (_ for _ in ()).throw(httpx.ConnectError("id down", request=req))
    )
    try:
        res = await client.get(
            "/api/v1/notifications",
            headers={"Authorization": f"Bearer {USER_1_TOKEN}"},
        )
        assert res.status_code == 503
    finally:
        app.state.identity_client._transport = original_transport


def test_websocket_live_notifications_and_disconnect_handling(app) -> None:
    with TestClient(app) as sync_client:
        # Unauthenticated WS rejected cleanly
        with pytest.raises(WebSocketDisconnect):
            with sync_client.websocket_connect("/api/v1/notifications/ws"):
                pass

        with sync_client.websocket_connect(f"/api/v1/notifications/ws?token={USER_1_TOKEN}") as ws1:
            welcome = ws1.receive_json()
            assert welcome["event"] == "connected"
            assert welcome["user_id"] == str(USER_1_ID)

            ws1.send_text("ping")
            assert ws1.receive_json() == {"event": "pong"}

            # Publishing event for USER_1 pushes immediately over WS
            post_res = sync_client.post(
                "/api/v1/notifications/events",
                json={
                    "user_id": str(USER_1_ID),
                    "event_type": "REVIEW_APPROVED",
                    "title": "Approved!",
                    "message": "Your deliverable was approved.",
                },
                headers={"X-Service-Key": INTERNAL_KEY},
            )
            assert post_res.status_code == 201
            pushed = ws1.receive_json()
            assert pushed["event"] == "notification.created"
            assert pushed["data"]["event_type"] == "REVIEW_APPROVED"

            # Unrelated USER_2 does not receive USER_1 events
            with sync_client.websocket_connect(f"/api/v1/notifications/ws?token={USER_2_TOKEN}") as ws2:
                assert ws2.receive_json()["event"] == "connected"
                sync_client.post(
                    "/api/v1/notifications/events",
                    json={
                        "user_id": str(USER_1_ID),
                        "event_type": "ANOTHER_EVENT",
                        "title": "For U1 only",
                        "message": "Isolated.",
                    },
                    headers={"X-Service-Key": INTERNAL_KEY},
                )
                assert ws1.receive_json()["data"]["event_type"] == "ANOTHER_EVENT"
                ws2.send_text("ping")
                assert ws2.receive_json() == {"event": "pong"}

