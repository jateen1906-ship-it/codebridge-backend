"""Shared pytest fixtures for isolated asynchronous Identity Service testing."""

from __future__ import annotations

from collections.abc import AsyncIterator
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
from app.models.user import User, UserRole
from app.schemas.user import AdminUserCreateRequest
from app.services.user_service import UserService

TEST_JWT_SECRET = "test-suite-super-strong-jwt-signing-secret-key-64-bytes-minimum!!"


@pytest.fixture
def test_settings(tmp_path: Path) -> Settings:
    """Create isolated per-test Settings backed by a fresh async SQLite database."""
    db_file = tmp_path / "identity_test.db"
    return Settings(
        APP_NAME="CodeBridge Identity Service (Test)",
        APP_ENV="test",
        DEBUG=False,
        DATABASE_URL=f"sqlite+aiosqlite:///{db_file.as_posix()}",
        JWT_SECRET_KEY=TEST_JWT_SECRET,
        JWT_ALGORITHM="HS256",
        ACCESS_TOKEN_EXPIRE_MINUTES=15,
        REFRESH_TOKEN_EXPIRE_DAYS=7,
        LOG_LEVEL="INFO",
    )


@pytest_asyncio.fixture
async def identity_app(test_settings: Settings) -> AsyncIterator[FastAPI]:
    """Create FastAPI app with fresh database schema and manage lifespan cleanly."""
    application = create_app(test_settings)
    engine: AsyncEngine = application.state.engine

    @event.listens_for(engine.sync_engine, "connect")
    def _ensure_fk(dbapi_connection: Any, _connection_record: Any) -> None:
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.close()

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    async with application.router.lifespan_context(application):
        yield application


@pytest_asyncio.fixture
async def client(identity_app: FastAPI) -> AsyncIterator[httpx.AsyncClient]:
    """Provide an HTTPX AsyncClient wired directly to the ASGI Identity Service."""
    transport = httpx.ASGITransport(app=identity_app)
    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as async_client:
        yield async_client


async def register_user(
    client: httpx.AsyncClient,
    *,
    full_name: str = "Demo User",
    email: str = "demo@example.com",
    password: str = "StrongPass123!",
    role: str = "OWNER",
) -> httpx.Response:
    """Helper to call public user registration endpoint."""
    return await client.post(
        "/api/v1/auth/register",
        json={
            "full_name": full_name,
            "email": email,
            "password": password,
            "role": role,
        },
    )


async def login_user(
    client: httpx.AsyncClient,
    *,
    email: str,
    password: str,
) -> httpx.Response:
    """Helper to call login endpoint."""
    return await client.post(
        "/api/v1/auth/login",
        json={
            "email": email,
            "password": password,
        },
    )


async def provision_admin_account(
    identity_app: FastAPI,
    *,
    full_name: str = "Platform Admin",
    email: str = "admin@codebridge.dev",
    password: str = "AdminStrong123!",
) -> User:
    """Provision an ADMIN user via administrative service (never public registration)."""
    session_factory = identity_app.state.session_factory
    async with session_factory() as session:
        service = UserService(session)
        return await service.provision_user_by_admin(
            AdminUserCreateRequest(
                full_name=full_name,
                email=email,
                password=password,
                role=UserRole.ADMIN,
                is_verified=True,
            )
        )
