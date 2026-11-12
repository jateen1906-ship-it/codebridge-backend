"""Environment-based configuration for CodeBridge Verification Service."""

from __future__ import annotations

from typing import Literal
from urllib.parse import urlparse

from pydantic import Field, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

_DEV_DEFAULT_DATABASE_URL = (
    "postgresql+asyncpg://postgres:change_me_local_only@localhost:5432/codebridge_verification"
)
_DEV_SIGNING_SECRET = "dev-verification-hmac-secret-change-me-32chars"
_DEV_INTERNAL_KEY = "dev-internal-service-key-change-me"


def _validate_http_url(value: str, field_name: str) -> str:
    cleaned = value.strip().rstrip("/")
    parsed = urlparse(cleaned)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise ValueError(f"{field_name} must be a valid HTTP or HTTPS URL.")
    if parsed.username or parsed.password:
        raise ValueError(f"{field_name} must not contain embedded credentials.")
    return cleaned


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=True,
        extra="ignore",
    )

    APP_NAME: str = "CodeBridge Verification Service"
    APP_VERSION: str = "1.0.0"
    APP_ENV: Literal["development", "staging", "production", "test"] = "development"
    DEBUG: bool = False
    HOST: str = "0.0.0.0"
    PORT: int = Field(default=8007, ge=1, le=65535)
    API_V1_PREFIX: str = "/api/v1"
    LOG_LEVEL: Literal["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"] = "INFO"

    DATABASE_URL: str = _DEV_DEFAULT_DATABASE_URL
    DB_ECHO: bool = False
    DATABASE_POOL_SIZE: int = Field(default=10, ge=1, le=100)
    DATABASE_MAX_OVERFLOW: int = Field(default=20, ge=0, le=200)

    IDENTITY_SERVICE_URL: str = "http://localhost:8001"
    PROJECT_SERVICE_URL: str = "http://localhost:8002"
    GITHUB_SERVICE_URL: str = "http://localhost:8003"
    REVIEW_SERVICE_URL: str = "http://localhost:8006"
    NOTIFICATION_SERVICE_URL: str = "http://localhost:8009"
    AUDIT_SERVICE_URL: str = "http://localhost:8011"
    SERVICE_CLIENT_TIMEOUT_SECONDS: float = Field(default=10.0, gt=0.0, le=60.0)
    INTERNAL_SERVICE_KEY: str = _DEV_INTERNAL_KEY

    VERIFICATION_SIGNING_SECRET: str = _DEV_SIGNING_SECRET
    VERIFICATION_SIGNING_KEY_ID: str = "cb-verify-v1"

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

    @field_validator(
        "APP_NAME",
        "HOST",
        "DATABASE_URL",
        "VERIFICATION_SIGNING_SECRET",
        "VERIFICATION_SIGNING_KEY_ID",
        "INTERNAL_SERVICE_KEY",
    )
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
        allowed = ("postgresql+asyncpg://", "sqlite+aiosqlite://")
        if not value.startswith(allowed):
            raise ValueError(
                "DATABASE_URL must use an async driver (postgresql+asyncpg:// or sqlite+aiosqlite://)."
            )
        return value

    @field_validator(
        "IDENTITY_SERVICE_URL",
        "PROJECT_SERVICE_URL",
        "GITHUB_SERVICE_URL",
        "REVIEW_SERVICE_URL",
        "NOTIFICATION_SERVICE_URL",
        "AUDIT_SERVICE_URL",
    )
    @classmethod
    def _validate_urls(cls, value: str, info) -> str:
        return _validate_http_url(value, info.field_name)

    @model_validator(mode="after")
    def _validate_production_rules(self) -> "Settings":
        if len(self.VERIFICATION_SIGNING_SECRET) < 16:
            raise ValueError("VERIFICATION_SIGNING_SECRET must be at least 16 characters.")
        if self.APP_ENV in {"staging", "production"}:
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
                self.VERIFICATION_SIGNING_SECRET == _DEV_SIGNING_SECRET
                or "change_me" in self.VERIFICATION_SIGNING_SECRET
                or len(self.VERIFICATION_SIGNING_SECRET) < 32
            ):
                raise ValueError(
                    "Production/staging require an explicit strong VERIFICATION_SIGNING_SECRET."
                )
            if self.INTERNAL_SERVICE_KEY == _DEV_INTERNAL_KEY or len(self.INTERNAL_SERVICE_KEY) < 24:
                raise ValueError("Production/staging require a strong explicit INTERNAL_SERVICE_KEY.")
        return self


def load_settings() -> Settings:
    return Settings()
