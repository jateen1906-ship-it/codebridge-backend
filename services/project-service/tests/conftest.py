"""Shared pytest fixtures, async SQLite DB isolation, and configurable Identity mock."""

from __future__ import annotations

import json
import uuid
from collections.abc import AsyncIterator, Awaitable, Callable
from dataclasses import dataclass, field
from datetime import date, timedelta
from pathlib import Path
from typing import Any

import httpx
import pytest
import pytest_asyncio
from fastapi import FastAPI
from sqlalchemy import event
from sqlalchemy.ext.asyncio import AsyncEngine

from app.core.config import Settings
from app.main import create_app
from app.models.base import Base

OWNER_ID = uuid.UUID("11111111-1111-4111-8111-111111111111")
OWNER_2_ID = uuid.UUID("22222222-2222-4222-8222-222222222222")
FREELANCER_ID = uuid.UUID("33333333-3333-4333-8333-333333333333")
FREELANCER_PROFILE_ID = uuid.UUID("33333333-aaaa-4333-8333-333333333333")
FREELANCER_2_ID = uuid.UUID("44444444-4444-4444-8444-444444444444")
FREELANCER_2_PROFILE_ID = uuid.UUID("44444444-aaaa-4444-8444-444444444444")
ADMIN_ID = uuid.UUID("55555555-5555-4555-8555-555555555555")
INACTIVE_USER_ID = uuid.UUID("66666666-6666-4666-8666-666666666666")
MISSING_FREELANCER_ID = uuid.UUID("77777777-7777-4777-8777-777777777777")
WRONG_ROLE_FREELANCER_ID = uuid.UUID("88888888-8888-4888-8888-888888888888")

OWNER_TOKEN = "token-owner-1-secret-jwt-value"
OWNER_2_TOKEN = "token-owner-2-secret-jwt-value"
FREELANCER_TOKEN = "token-freelancer-1-secret-jwt-value"
FREELANCER_2_TOKEN = "token-freelancer-2-secret-jwt-value"
ADMIN_TOKEN = "token-admin-secret-jwt-value"
INACTIVE_USER_TOKEN = "token-inactive-secret-jwt-value"
INVALID_TOKEN = "token-invalid-or-expired-value"


@dataclass
class RecordedIdentityRequest:
    method: str
    path: str
    headers: dict[str, str]


@dataclass
class MockIdentityRouter:
    """Configurable AsyncBaseTransport handler simulating CodeBridge Identity Service."""

    auth_mode: str = "normal"
    freelancer_mode: str = "normal"
    before_freelancer_lookup: Callable[[uuid.UUID], Awaitable[None]] | None = None
    recorded_requests: list[RecordedIdentityRequest] = field(default_factory=list)

    def _tokens(self) -> dict[str, dict[str, Any]]:
        return {
            OWNER_TOKEN: {
                "id": str(OWNER_ID),
                "full_name": "Primary Owner",
                "email": "owner1@codebridge.dev",
                "role": "OWNER",
                "is_active": True,
                "is_verified": True,
            },
            OWNER_2_TOKEN: {
                "id": str(OWNER_2_ID),
                "full_name": "Secondary Owner",
                "email": "owner2@codebridge.dev",
                "role": "OWNER",
                "is_active": True,
                "is_verified": True,
            },
            FREELANCER_TOKEN: {
                "id": str(FREELANCER_ID),
                "full_name": "Alice Freelancer",
                "email": "freelancer1@codebridge.dev",
                "role": "FREELANCER",
                "is_active": True,
                "is_verified": True,
            },
            FREELANCER_2_TOKEN: {
                "id": str(FREELANCER_2_ID),
                "full_name": "Bob Freelancer",
                "email": "freelancer2@codebridge.dev",
                "role": "FREELANCER",
                "is_active": True,
                "is_verified": False,
            },
            ADMIN_TOKEN: {
                "id": str(ADMIN_ID),
                "full_name": "Platform Admin",
                "email": "admin@codebridge.dev",
                "role": "ADMIN",
                "is_active": True,
                "is_verified": True,
            },
            INACTIVE_USER_TOKEN: {
                "id": str(INACTIVE_USER_ID),
                "full_name": "Inactive User",
                "email": "inactive@codebridge.dev",
                "role": "OWNER",
                "is_active": False,
                "is_verified": False,
            },
        }

    def _freelancers(self) -> dict[str, dict[str, Any]]:
        return {
            str(FREELANCER_ID): {
                "id": str(FREELANCER_PROFILE_ID),
                "user_id": str(FREELANCER_ID),
                "full_name": "Alice Freelancer",
                "skills": ["Python", "FastAPI"],
                "experience_years": 5,
                "availability": "AVAILABLE",
                "is_verified": True,
            },
            str(FREELANCER_PROFILE_ID): {
                "id": str(FREELANCER_PROFILE_ID),
                "user_id": str(FREELANCER_ID),
                "full_name": "Alice Freelancer",
                "skills": ["Python", "FastAPI"],
                "experience_years": 5,
                "availability": "AVAILABLE",
                "is_verified": True,
            },
            str(FREELANCER_2_ID): {
                "id": str(FREELANCER_2_PROFILE_ID),
                "user_id": str(FREELANCER_2_ID),
                "full_name": "Bob Freelancer",
                "skills": ["React", "TypeScript"],
                "experience_years": 3,
                "availability": "AVAILABLE",
                "is_verified": False,
            },
            str(WRONG_ROLE_FREELANCER_ID): {
                "id": str(uuid.uuid4()),
                "user_id": str(WRONG_ROLE_FREELANCER_ID),
                "full_name": "Wrong Role User",
                "skills": ["Python"],
                "experience_years": 2,
                "availability": "AVAILABLE",
                "is_verified": True,
                "role": "OWNER",
                "is_active": True,
            },
        }

    async def handle(self, request: httpx.Request) -> httpx.Response:
        self.recorded_requests.append(
            RecordedIdentityRequest(
                method=request.method,
                path=request.url.path,
                headers={k.lower(): v for k, v in request.headers.items()},
            )
        )

        if request.method == "GET" and request.url.path == "/api/v1/auth/me":
            return await self._handle_auth_me(request)

        if request.method == "GET" and request.url.path.startswith("/api/v1/freelancers/"):
            target_id_str = request.url.path.removeprefix("/api/v1/freelancers/")
            return await self._handle_freelancer_lookup(target_id_str, request)

        return httpx.Response(404, json={"detail": "Not found"})

    async def _handle_auth_me(self, request: httpx.Request) -> httpx.Response:
        if self.auth_mode == "timeout":
            raise httpx.ReadTimeout("Simulated Identity Service timeout", request=request)
        if self.auth_mode == "connect_error":
            raise httpx.ConnectError("Simulated Identity connection refused", request=request)
        if self.auth_mode == "500":
            return httpx.Response(500, json={"error": "Internal failure"})
        if self.auth_mode == "403":
            return httpx.Response(
                403,
                json={"success": False, "error": {"code": "FORBIDDEN", "message": "Forbidden."}},
            )
        if self.auth_mode == "malformed_json":
            return httpx.Response(200, content=b"{not-valid-json", headers={"content-type": "application/json"})
        if self.auth_mode == "missing_field":
            return httpx.Response(200, json={"id": str(OWNER_ID)})

        auth_header = request.headers.get("authorization", "")
        if not auth_header.startswith("Bearer "):
            return httpx.Response(
                401,
                json={"success": False, "error": {"code": "UNAUTHORIZED", "message": "Missing token."}},
            )
        token = auth_header.removeprefix("Bearer ").strip()
        user_payload = self._tokens().get(token)
        if user_payload is None:
            return httpx.Response(
                401,
                json={"success": False, "error": {"code": "INVALID_TOKEN", "message": "Invalid token."}},
            )
        return httpx.Response(200, json=user_payload)

    async def _handle_freelancer_lookup(
        self,
        target_id_str: str,
        request: httpx.Request,
    ) -> httpx.Response:
        if self.before_freelancer_lookup is not None:
            await self.before_freelancer_lookup(uuid.UUID(target_id_str))

        if self.freelancer_mode == "timeout":
            raise httpx.ReadTimeout("Simulated freelancer lookup timeout", request=request)
        if self.freelancer_mode == "connect_error":
            raise httpx.ConnectError("Simulated connection failure", request=request)
        if self.freelancer_mode == "500":
            return httpx.Response(500, json={"error": "Unexpected failure"})
        if self.freelancer_mode == "malformed_json":
            return httpx.Response(200, content=b"<html>502 Bad Gateway</html>")

        record = self._freelancers().get(target_id_str)
        if record is None:
            return httpx.Response(
                404,
                json={
                    "success": False,
                    "error": {"code": "USER_NOT_FOUND", "message": "Freelancer not found."},
                },
            )

        payload = dict(record)
        if self.freelancer_mode == "wrong_role_owner":
            payload["role"] = "OWNER"
        elif self.freelancer_mode == "wrong_role_admin":
            payload["role"] = "ADMIN"
        elif self.freelancer_mode == "inactive_flag":
            payload["is_active"] = False
        elif self.freelancer_mode == "missing_fields":
            payload = {"id": str(FREELANCER_PROFILE_ID)}

        return httpx.Response(200, content=json.dumps(payload), headers={"content-type": "application/json"})


def auth_headers(token: str, *, request_id: str | None = None) -> dict[str, str]:
    headers = {"Authorization": f"Bearer {token}"}
    if request_id is not None:
        headers["X-Request-ID"] = request_id
    return headers


def future_deadline(days: int = 30) -> str:
    return (date.today() + timedelta(days=days)).isoformat()


def build_project_payload(**overrides: Any) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "title": "Fix Stripe payment webhook callback",
        "description": "Payment gateway returns 200 but final order status stays PENDING.",
        "expected_result": "Webhook verifies HMAC signature and updates transaction status reliably.",
        "budget": "5000.00",
        "deadline": future_deadline(30),
        "required_skills": ["Python", "FastAPI", "Webhooks"],
    }
    payload.update(overrides)
    return payload


@pytest.fixture
def identity_mock() -> MockIdentityRouter:
    return MockIdentityRouter()


@pytest.fixture
def test_settings(tmp_path: Path) -> Settings:
    db_file = tmp_path / "project_test.db"
    return Settings(
        APP_NAME="CodeBridge Project Service (Test)",
        APP_ENV="test",
        DEBUG=False,
        DATABASE_URL=f"sqlite+aiosqlite:///{db_file.as_posix()}",
        IDENTITY_SERVICE_URL="http://internal-identity-mock:8001",
        IDENTITY_SERVICE_TIMEOUT_SECONDS=2.0,
        DEFAULT_CURRENCY="INR",
        LOG_LEVEL="INFO",
    )


@pytest_asyncio.fixture
async def project_app(
    test_settings: Settings,
    identity_mock: MockIdentityRouter,
) -> AsyncIterator[FastAPI]:
    transport = httpx.MockTransport(identity_mock.handle)
    application = create_app(test_settings, identity_transport=transport)
    engine: AsyncEngine = application.state.engine

    @event.listens_for(engine.sync_engine, "connect")
    def _ensure_sqlite_fk(dbapi_connection: Any, _connection_record: Any) -> None:
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.close()

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    async with application.router.lifespan_context(application):
        yield application


@pytest_asyncio.fixture
async def client(project_app: FastAPI) -> AsyncIterator[httpx.AsyncClient]:
    transport = httpx.ASGITransport(app=project_app)
    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as async_client:
        yield async_client


async def create_sample_project(
    client: httpx.AsyncClient,
    *,
    token: str = OWNER_TOKEN,
    **overrides: Any,
) -> dict[str, Any]:
    res = await client.post(
        "/api/v1/projects",
        headers=auth_headers(token),
        json=build_project_payload(**overrides),
    )
    assert res.status_code == 201, res.text
    return res.json()

