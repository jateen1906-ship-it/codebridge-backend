"""Security, logging, configuration, correlation ID, and OpenAPI contract tests."""

from __future__ import annotations

import io
import json
import logging
import uuid
from collections.abc import Generator
from contextlib import contextmanager

import httpx
import pytest
from fastapi import FastAPI
from pydantic import ValidationError as PydanticValidationError
from sqlalchemy import select
from sqlalchemy.exc import SQLAlchemyError

from app.core.config import Settings
from app.core.logging import JsonLogFormatter, RequestContextFilter
from app.core.security import decode_token, hash_jti
from app.models.refresh_token import RefreshToken
from app.models.user import User
from app.repositories.user_repository import UserRepository
from tests.conftest import login_user, register_user


@contextmanager
def _capture_identity_json_logs() -> Generator[io.StringIO, None, None]:
    logger = logging.getLogger("identity_service")
    stream = io.StringIO()
    handler = logging.StreamHandler(stream)
    handler.setLevel(logging.DEBUG)
    handler.addFilter(RequestContextFilter())
    handler.setFormatter(JsonLogFormatter(service_name="identity-service"))
    logger.addHandler(handler)
    try:
        yield stream
    finally:
        logger.removeHandler(handler)


@pytest.mark.asyncio
async def test_health_endpoint_and_correlation_id_middleware(client: httpx.AsyncClient) -> None:
    res_auto = await client.get("/health")
    assert res_auto.status_code == 200
    assert res_auto.json() == {
        "status": "healthy",
        "service": "identity-service",
        "version": "1.0.0",
    }
    auto_id = res_auto.headers.get("X-Request-ID")
    assert auto_id is not None
    uuid.UUID(auto_id)

    incoming_id = str(uuid.uuid4())
    res_preserved = await client.get("/health", headers={"X-Request-ID": incoming_id})
    assert res_preserved.status_code == 200
    assert res_preserved.headers.get("X-Request-ID") == incoming_id

    res_malformed = await client.get("/health", headers={"X-Request-ID": "not-a-valid-uuid\r\n"})
    assert res_malformed.status_code == 200
    replaced_id = res_malformed.headers.get("X-Request-ID")
    assert replaced_id != "not-a-valid-uuid\r\n"
    uuid.UUID(replaced_id)


@pytest.mark.asyncio
async def test_argon2_password_hash_and_sha256_jti_storage(
    client: httpx.AsyncClient,
    identity_app: FastAPI,
    test_settings: Settings,
) -> None:
    plaintext_password = "UltraSecretPassword99!"
    await register_user(
        client,
        full_name="Security Check",
        email="security.check@example.com",
        password=plaintext_password,
        role="OWNER",
    )
    tokens = (
        await login_user(
            client,
            email="security.check@example.com",
            password=plaintext_password,
        )
    ).json()

    refresh_claims = decode_token(
        token=tokens["refresh_token"],
        expected_type="refresh",
        settings=test_settings,
    )

    async with identity_app.state.session_factory() as session:
        user = (
            await session.execute(select(User).where(User.email == "security.check@example.com"))
        ).scalar_one()
        assert user.password_hash.startswith("$argon2")
        assert plaintext_password not in user.password_hash

        rt = (await session.execute(select(RefreshToken))).scalar_one()
        assert rt.jti_hash == hash_jti(refresh_claims.jti)
        assert str(refresh_claims.jti) not in rt.jti_hash


@pytest.mark.asyncio
async def test_logs_never_expose_passwords_tokens_hashes_or_secrets(
    client: httpx.AsyncClient,
    test_settings: Settings,
) -> None:
    secret_password = "MyVerySecretLogTestPassword123!"
    with _capture_identity_json_logs() as log_stream:
        await register_user(
            client,
            full_name="Log Privacy",
            email="logprivacy@example.com",
            password=secret_password,
            role="FREELANCER",
        )
        tokens = (
            await login_user(
                client,
                email="logprivacy@example.com",
                password=secret_password,
            )
        ).json()
        await client.post(
            "/api/v1/auth/refresh",
            json={"refresh_token": tokens["refresh_token"]},
        )
        # Also trigger an invalid credentials error
        await login_user(
            client,
            email="logprivacy@example.com",
            password="WrongAttemptPassword123!",
        )

    raw_logs = log_stream.getvalue()
    assert raw_logs.strip()
    lines = [json.loads(line) for line in raw_logs.strip().splitlines()]
    assert any(entry.get("event") == "user_registered" for entry in lines)
    assert any(entry.get("event") == "user_login" for entry in lines)

    forbidden_substrings = [
        secret_password,
        "WrongAttemptPassword123!",
        "$argon2",
        tokens["access_token"],
        tokens["refresh_token"],
        test_settings.JWT_SECRET_KEY.get_secret_value(),
    ]
    for secret in forbidden_substrings:
        assert secret not in raw_logs


@pytest.mark.asyncio
async def test_database_error_returns_sanitized_500_without_leaks(
    client: httpx.AsyncClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def broken_get_by_email(*_args, **_kwargs):
        raise SQLAlchemyError(
            "FATAL: password authentication failed for postgresql+asyncpg://postgres:secret@db:5432/prod"
        )

    monkeypatch.setattr(UserRepository, "get_by_email", broken_get_by_email)

    res = await login_user(client, email="any@example.com", password="AnyPassword123!")
    assert res.status_code == 500
    body = res.json()
    assert body["success"] is False
    assert body["error"]["code"] == "INTERNAL_SERVER_ERROR"
    text_body = json.dumps(body).lower()
    assert "postgresql" not in text_body
    assert "traceback" not in text_body
    assert "sqlalchemy" not in text_body


def test_production_settings_fail_on_insecure_or_missing_secrets() -> None:
    # Weak short JWT secret
    with pytest.raises(PydanticValidationError):
        Settings(
            APP_ENV="production",
            DATABASE_URL="postgresql+asyncpg://prod:strongpass@db.internal:5432/identity",
            JWT_SECRET_KEY="too-short",
        )

    # Default placeholder JWT secret rejected in production
    with pytest.raises(PydanticValidationError):
        Settings(
            APP_ENV="production",
            DATABASE_URL="postgresql+asyncpg://prod:strongpass@db.internal:5432/identity",
        )

    # SQLite rejected in production
    with pytest.raises(PydanticValidationError):
        Settings(
            APP_ENV="production",
            DATABASE_URL="sqlite+aiosqlite:///./prod.db",
            JWT_SECRET_KEY="a-very-strong-production-random-key-of-64-characters-1234567890!!",
        )

    # Debug=True rejected in staging
    with pytest.raises(PydanticValidationError):
        Settings(
            APP_ENV="staging",
            DEBUG=True,
            DATABASE_URL="postgresql+asyncpg://staging:strongpass@db.internal:5432/identity",
            JWT_SECRET_KEY="a-very-strong-staging-random-key-of-64-characters-1234567890!!",
        )

    # Valid production configuration succeeds
    valid_prod = Settings(
        APP_ENV="production",
        DEBUG=False,
        DATABASE_URL="postgresql+asyncpg://produser:StrongDBPass99@db.internal:5432/codebridge_identity",
        JWT_SECRET_KEY="valid-production-entropy-signing-key-64-characters-long-abcdef!!",
    )
    assert valid_prod.APP_ENV == "production"


@pytest.mark.asyncio
async def test_openapi_documentation_complete(client: httpx.AsyncClient) -> None:
    schema_res = await client.get("/openapi.json")
    assert schema_res.status_code == 200
    spec = schema_res.json()

    expected_paths = {
        "/health",
        "/api/v1/auth/register",
        "/api/v1/auth/login",
        "/api/v1/auth/refresh",
        "/api/v1/auth/logout",
        "/api/v1/auth/me",
        "/api/v1/auth/admin/users",
        "/api/v1/auth/admin/users/{user_id}/status",
        "/api/v1/profile",
        "/api/v1/freelancers",
        "/api/v1/freelancers/me",
        "/api/v1/freelancers/{freelancer_id}",
    }
    assert expected_paths.issubset(set(spec["paths"].keys()))
    assert "HTTPBearer" in spec["components"]["securitySchemes"]
    assert "ErrorResponse" in spec["components"]["schemas"]


@pytest.mark.asyncio
async def test_identity_ready_endpoint_200_and_503_on_db_failure(
    client: httpx.AsyncClient,
    identity_app: FastAPI,
) -> None:
    from sqlalchemy.ext.asyncio import create_async_engine

    ok_res = await client.get("/ready")
    assert ok_res.status_code == 200
    assert ok_res.json()["status"] == "ready"

    orig_engine = identity_app.state.engine
    broken_engine = create_async_engine("postgresql+asyncpg://bad:bad@127.0.0.1:1/bad")
    identity_app.state.engine = broken_engine
    try:
        fail_res = await client.get("/ready")
        assert fail_res.status_code == 503
        assert fail_res.json()["status"] == "not_ready"
    finally:
        identity_app.state.engine = orig_engine
        await broken_engine.dispose()
