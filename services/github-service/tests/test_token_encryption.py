"""Tests for AES-256-GCM token encryption, config hardening, health, readiness, & logging."""

from __future__ import annotations

import base64
import json
import logging

import pytest
from httpx import AsyncClient

from app.core.config import Settings, decode_aes256_key
from app.core.encryption import (
    REVOKED_TOKEN_SENTINEL,
    TokenEncryptionError,
    TokenEncryptionService,
)
from app.core.logging import JsonLogFormatter
from tests.conftest import (
    ALTERNATE_ENCRYPTION_KEY,
    OTHER_OWNER_ID,
    OWNER_ID,
    TEST_ENCRYPTION_KEY,
)


def test_aes_256_gcm_roundtrip_uniqueness_aad_and_tamper_detection() -> None:
    key1 = decode_aes256_key(TEST_ENCRYPTION_KEY)
    key2 = decode_aes256_key(ALTERNATE_ENCRYPTION_KEY)
    svc1 = TokenEncryptionService(key1)
    svc2 = TokenEncryptionService(key2)

    plaintext = "gho_secret_token_987654321"
    ct1 = svc1.encrypt(plaintext, owner_id=OWNER_ID)
    ct2 = svc1.encrypt(plaintext, owner_id=OWNER_ID)

    assert ct1 != ct2  # unique random 96-bit nonce per encryption
    assert ct1.startswith("v1:")
    assert plaintext not in ct1
    assert svc1.decrypt(ct1, owner_id=OWNER_ID) == plaintext

    # Wrong key
    with pytest.raises(TokenEncryptionError):
        svc2.decrypt(ct1, owner_id=OWNER_ID)

    # Wrong owner_id (AAD mismatch)
    with pytest.raises(TokenEncryptionError):
        svc1.decrypt(ct1, owner_id=OTHER_OWNER_ID)

    # Tampered ciphertext
    tampered = ct1[:-4] + ("AAAA" if not ct1.endswith("AAAA") else "BBBB")
    with pytest.raises(TokenEncryptionError):
        svc1.decrypt(tampered, owner_id=OWNER_ID)

    # Revoked sentinel and malformed format
    with pytest.raises(TokenEncryptionError):
        svc1.decrypt(REVOKED_TOKEN_SENTINEL, owner_id=OWNER_ID)
    with pytest.raises(TokenEncryptionError):
        svc1.decrypt("plaintext-legacy-invalid", owner_id=OWNER_ID)


def test_production_config_validation_rejects_weak_or_placeholder_settings() -> None:
    with pytest.raises(ValueError, match="32 bytes"):
        decode_aes256_key(base64.urlsafe_b64encode(b"short-key").decode("ascii"))

    with pytest.raises(ValueError, match="GITHUB_TOKEN_ENCRYPTION_KEY"):
        Settings(
            APP_ENV="production",
            DEBUG=False,
            DATABASE_URL="postgresql+asyncpg://prod:secret@db.internal:5432/codebridge_github",
            GITHUB_CLIENT_ID="real-client-id",
            GITHUB_CLIENT_SECRET="real-client-secret",
        )

    with pytest.raises(ValueError, match="GITHUB_CLIENT_SECRET"):
        Settings(
            APP_ENV="staging",
            DEBUG=False,
            DATABASE_URL="postgresql+asyncpg://prod:secret@db.internal:5432/codebridge_github",
            GITHUB_CLIENT_ID="real-client-id",
            GITHUB_TOKEN_ENCRYPTION_KEY=TEST_ENCRYPTION_KEY,
        )


def test_json_log_formatter_redacts_tokens_and_secrets() -> None:
    formatter = JsonLogFormatter(service_name="github-service")
    record = logging.LogRecord(
        name="github_service.test",
        level=logging.INFO,
        pathname=__file__,
        lineno=1,
        msg="Upstream call failed with Bearer gho_1234567890abcdef and client_secret=topsecret",
        args=(),
        exc_info=None,
    )
    record.access_token = "gho_should_never_appear"
    record.encrypted_access_token = "v1:secret"
    rendered = formatter.format(record)
    parsed = json.loads(rendered)

    assert "gho_1234567890abcdef" not in rendered
    assert "gho_should_never_appear" not in rendered
    assert "topsecret" not in rendered
    assert "v1:secret" not in rendered
    assert "access_token" not in parsed
    assert "encrypted_access_token" not in parsed
    assert parsed["message"].count("[REDACTED]") == 2


@pytest.mark.asyncio
async def test_health_and_readiness_endpoints(client: AsyncClient, github_app) -> None:
    health = await client.get("/health")
    assert health.status_code == 200
    assert health.json()["status"] == "healthy"
    assert health.json()["service"] == "github-service"

    ready = await client.get("/ready")
    assert ready.status_code == 200
    assert ready.json() == {
        "status": "ready",
        "service": "github-service",
        "version": "1.0.0",
        "database": "connected",
    }

    # Broken DB session factory -> 503 without leaking URL or key
    original_factory = github_app.state.session_factory

    def _broken_factory():
        raise RuntimeError("sqlite+aiosqlite:///secret-db-path simulated down")

    github_app.state.session_factory = _broken_factory
    try:
        unready = await client.get("/ready")
        assert unready.status_code == 503
        assert unready.json()["status"] == "not_ready"
        assert unready.json()["database"] == "unavailable"
        assert "secret-db-path" not in unready.text
    finally:
        github_app.state.session_factory = original_factory
