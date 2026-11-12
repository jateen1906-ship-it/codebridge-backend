"""Environment-based configuration and startup validation for GitHub Service."""

from __future__ import annotations

import base64
from typing import Literal
from urllib.parse import urlparse

from pydantic import Field, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

_DEV_DEFAULT_DATABASE_URL = (
    "postgresql+asyncpg://postgres:change_me_local_only@localhost:5432/codebridge_github"
)
_DEV_DEFAULT_ENCRYPTION_KEY = "REVWX09OTFlfSU5TRUNVUkVfS0VZX0RPX05PVF9VU0U="
_DEV_DEFAULT_CLIENT_ID = "dev-github-client-id-placeholder"
_DEV_DEFAULT_CLIENT_SECRET = "dev-github-client-secret-placeholder"


def _validate_http_url(value: str, field_name: str) -> str:
    """Validate an HTTP/HTTPS URL cleanly and reject embedded credentials."""
    cleaned = value.strip().rstrip("/")
    parsed = urlparse(cleaned)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise ValueError(f"{field_name} must be a valid HTTP or HTTPS URL.")
    if parsed.username or parsed.password:
        raise ValueError(f"{field_name} must not contain embedded credentials.")
    return cleaned


def decode_aes256_key(raw_key: str) -> bytes:
    """Decode a URL-safe or standard base64 string into an exact 32-byte AES-256 key."""
    cleaned = raw_key.strip()
    if not cleaned:
        raise ValueError("GITHUB_TOKEN_ENCRYPTION_KEY must not be empty.")
    try:
        decoded = base64.urlsafe_b64decode(cleaned.encode("ascii"))
    except Exception as exc:
        raise ValueError("GITHUB_TOKEN_ENCRYPTION_KEY must be valid base64.") from exc
    if len(decoded) != 32:
        raise ValueError(
            "GITHUB_TOKEN_ENCRYPTION_KEY must decode to exactly 32 bytes (256-bit AES-GCM key)."
        )
    return decoded


class Settings(BaseSettings):
    """Pydantic Settings for CodeBridge GitHub Service."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=True,
        extra="ignore",
    )

    APP_NAME: str = "CodeBridge GitHub Service"
    APP_VERSION: str = "1.0.0"
    APP_ENV: Literal["development", "staging", "production", "test"] = "development"
    DEBUG: bool = False
    HOST: str = "0.0.0.0"
    PORT: int = Field(default=8003, ge=1, le=65535)
    API_V1_PREFIX: str = "/api/v1"
    LOG_LEVEL: Literal["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"] = "INFO"

    # Database configuration
    DATABASE_URL: str = _DEV_DEFAULT_DATABASE_URL
    DB_ECHO: bool = False
    DATABASE_POOL_SIZE: int = Field(default=10, ge=1, le=100)
    DATABASE_MAX_OVERFLOW: int = Field(default=20, ge=0, le=200)

    # Synchronous HTTP/REST peer integrations
    IDENTITY_SERVICE_URL: str = "http://localhost:8001"
    PROJECT_SERVICE_URL: str = "http://localhost:8002"
    SERVICE_CLIENT_TIMEOUT_SECONDS: float = Field(default=5.0, gt=0.0, le=60.0)

    # GitHub OAuth App & GitHub API settings
    GITHUB_CLIENT_ID: str = _DEV_DEFAULT_CLIENT_ID
    GITHUB_CLIENT_SECRET: str = _DEV_DEFAULT_CLIENT_SECRET
    GITHUB_OAUTH_CALLBACK_URL: str = "http://localhost:8000/api/v1/github/oauth/callback"
    GITHUB_OAUTH_SCOPES: str = "repo read:user"
    GITHUB_TOKEN_ENCRYPTION_KEY: str = _DEV_DEFAULT_ENCRYPTION_KEY
    GITHUB_HTTP_TIMEOUT_SECONDS: float = Field(default=10.0, gt=0.0, le=60.0)
    OAUTH_STATE_EXPIRE_SECONDS: int = Field(default=600, ge=30, le=3600)

    # Optional non-secret frontend redirect destinations after OAuth callback
    FRONTEND_OAUTH_SUCCESS_URL: str | None = None
    FRONTEND_OAUTH_FAILURE_URL: str | None = None

    # Pagination & repository read safety caps
    GITHUB_API_PAGE_SIZE: int = Field(default=50, ge=1, le=100)
    GITHUB_API_MAX_PAGES: int = Field(default=5, ge=1, le=20)
    GITHUB_CONTENTS_MAX_BYTES: int = Field(default=262144, ge=1024, le=5242880)
    GITHUB_CONTENTS_MAX_ENTRIES: int = Field(default=200, ge=1, le=1000)
    GITHUB_ARCHIVE_MAX_BYTES: int = Field(default=20_971_520, ge=1024, le=104_857_600)

    # Strictly test-only base URL overrides for live E2E test harness against a local mock GitHub HTTP server
    GITHUB_TEST_API_BASE_URL: str | None = None
    GITHUB_TEST_OAUTH_TOKEN_URL: str | None = None

    @property
    def encryption_key_bytes(self) -> bytes:
        return decode_aes256_key(self.GITHUB_TOKEN_ENCRYPTION_KEY)

    @field_validator("DEBUG", mode="before")
    @classmethod
    def _parse_debug(cls, value: object) -> bool:
        if isinstance(value, bool):
            return value
        if isinstance(value, str):
            normalized = value.strip().lower()
            if normalized in {"1", "true", "yes", "on"}:
                return True
            if normalized in {"0", "false", "no", "off", "release"}:
                return False
        raise ValueError("DEBUG must be a valid boolean value.")

    @field_validator("APP_NAME", "HOST", "DATABASE_URL", "GITHUB_CLIENT_ID", "GITHUB_CLIENT_SECRET")
    @classmethod
    def _require_non_empty(cls, value: str, info) -> str:
        cleaned = value.strip()
        if not cleaned:
            raise ValueError(f"{info.field_name} must not be empty.")
        return cleaned

    @field_validator("API_V1_PREFIX")
    @classmethod
    def _validate_api_prefix(cls, value: str) -> str:
        cleaned = value.strip().rstrip("/")
        if not cleaned or not cleaned.startswith("/"):
            raise ValueError("API_V1_PREFIX must start with '/' and not be empty.")
        return cleaned

    @field_validator("DATABASE_URL")
    @classmethod
    def _validate_async_database_url(cls, value: str) -> str:
        allowed_prefixes = ("postgresql+asyncpg://", "sqlite+aiosqlite://")
        if not value.startswith(allowed_prefixes):
            raise ValueError(
                "DATABASE_URL must use an async driver (postgresql+asyncpg:// or sqlite+aiosqlite://)."
            )
        return value

    @field_validator("IDENTITY_SERVICE_URL", "PROJECT_SERVICE_URL", "GITHUB_OAUTH_CALLBACK_URL")
    @classmethod
    def _validate_service_urls(cls, value: str, info) -> str:
        return _validate_http_url(value, info.field_name)

    @field_validator("FRONTEND_OAUTH_SUCCESS_URL", "FRONTEND_OAUTH_FAILURE_URL", mode="before")
    @classmethod
    def _validate_optional_redirect_urls(cls, value: object, info) -> str | None:
        if value is None:
            return None
        if not isinstance(value, str):
            raise ValueError(f"{info.field_name} must be a valid URL string.")
        cleaned = value.strip()
        if not cleaned:
            return None
        return _validate_http_url(cleaned, info.field_name)

    @field_validator("GITHUB_OAUTH_SCOPES")
    @classmethod
    def _normalize_scopes(cls, value: str) -> str:
        tokens = [part.strip() for part in value.replace(",", " ").split() if part.strip()]
        if not tokens:
            raise ValueError("GITHUB_OAUTH_SCOPES must contain at least one scope (e.g. 'repo read:user').")
        seen: set[str] = set()
        deduped: list[str] = []
        for token in tokens:
            if token not in seen:
                seen.add(token)
                deduped.append(token)
        return " ".join(deduped)

    @field_validator("GITHUB_TOKEN_ENCRYPTION_KEY")
    @classmethod
    def _validate_encryption_key(cls, value: str) -> str:
        decode_aes256_key(value)
        return value.strip()

    @model_validator(mode="after")
    def _enforce_environment_security(self) -> "Settings":
        if self.APP_ENV != "test" and (
            self.GITHUB_TEST_API_BASE_URL is not None or self.GITHUB_TEST_OAUTH_TOKEN_URL is not None
        ):
            raise ValueError(
                "GITHUB_TEST_API_BASE_URL and GITHUB_TEST_OAUTH_TOKEN_URL are forbidden outside APP_ENV=test."
            )
        if self.APP_ENV in {"production", "staging"}:
            if self.DATABASE_URL == _DEV_DEFAULT_DATABASE_URL or "change_me" in self.DATABASE_URL:
                raise ValueError(
                    "Production/staging environments require an explicit non-default DATABASE_URL."
                )
            if not self.DATABASE_URL.startswith("postgresql+asyncpg://"):
                raise ValueError(
                    "Production/staging environments must use PostgreSQL (postgresql+asyncpg://)."
                )
            if self.DEBUG:
                raise ValueError("DEBUG must be false in production/staging environments.")
            if (
                self.GITHUB_CLIENT_ID == _DEV_DEFAULT_CLIENT_ID
                or "placeholder" in self.GITHUB_CLIENT_ID.lower()
            ):
                raise ValueError(
                    "Production/staging environments require a real GITHUB_CLIENT_ID."
                )
            if (
                self.GITHUB_CLIENT_SECRET == _DEV_DEFAULT_CLIENT_SECRET
                or "placeholder" in self.GITHUB_CLIENT_SECRET.lower()
            ):
                raise ValueError(
                    "Production/staging environments require a real GITHUB_CLIENT_SECRET."
                )
            if self.GITHUB_TOKEN_ENCRYPTION_KEY == _DEV_DEFAULT_ENCRYPTION_KEY:
                raise ValueError(
                    "Production/staging environments require a unique GITHUB_TOKEN_ENCRYPTION_KEY."
                )
        return self


def load_settings() -> Settings:
    """Load and validate GitHub Service configuration from environment variables."""
    return Settings()

