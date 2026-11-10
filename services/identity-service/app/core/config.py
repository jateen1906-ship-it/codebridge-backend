"""Environment-based configuration and security validation for Identity Service."""

from __future__ import annotations

from typing import Literal

from pydantic import Field, SecretStr, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

_DEV_DEFAULT_JWT_SECRET = "dev-only-insecure-secret-key-change-before-production-32bytes"
_DEV_DEFAULT_DATABASE_URL = (
    "postgresql+asyncpg://postgres:change_me_local_only@localhost:5432/codebridge_identity"
)
_INSECURE_SECRET_MARKERS = ("dev-only", "insecure", "change_me", "change-before", "placeholder")


class Settings(BaseSettings):
    """Pydantic Settings for the CodeBridge Identity Service."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=True,
        extra="ignore",
    )

    APP_NAME: str = "CodeBridge Identity Service"
    APP_VERSION: str = "1.0.0"
    APP_ENV: Literal["development", "staging", "production", "test"] = "development"
    DEBUG: bool = False
    HOST: str = "0.0.0.0"
    PORT: int = Field(default=8001, ge=1, le=65535)
    API_V1_PREFIX: str = "/api/v1"
    LOG_LEVEL: Literal["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"] = "INFO"

    # Database configuration
    DATABASE_URL: str = _DEV_DEFAULT_DATABASE_URL
    DB_ECHO: bool = False
    DB_POOL_SIZE: int = Field(default=10, ge=1, le=100)
    DB_MAX_OVERFLOW: int = Field(default=20, ge=0, le=200)

    # JWT & hashing configuration
    JWT_SECRET_KEY: SecretStr = SecretStr(_DEV_DEFAULT_JWT_SECRET)
    JWT_ALGORITHM: Literal["HS256", "HS384", "HS512"] = "HS256"
    ACCESS_TOKEN_EXPIRE_MINUTES: int = Field(default=15, ge=1, le=1440)
    REFRESH_TOKEN_EXPIRE_DAYS: int = Field(default=7, ge=1, le=90)
    PASSWORD_HASHING_SCHEME: Literal["argon2"] = "argon2"

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

    @field_validator("APP_NAME", "HOST", "DATABASE_URL")
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

    @field_validator("JWT_SECRET_KEY")
    @classmethod
    def _validate_jwt_secret_length(cls, value: SecretStr) -> SecretStr:
        secret = value.get_secret_value().strip()
        if len(secret) < 32:
            raise ValueError("JWT_SECRET_KEY must be at least 32 characters long.")
        return SecretStr(secret)

    @model_validator(mode="after")
    def _enforce_production_security(self) -> "Settings":
        if self.APP_ENV in {"production", "staging"}:
            secret = self.JWT_SECRET_KEY.get_secret_value().lower()
            if any(marker in secret for marker in _INSECURE_SECRET_MARKERS):
                raise ValueError(
                    "Production/staging environments require a non-default, strong JWT_SECRET_KEY."
                )
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
        return self


def load_settings() -> Settings:
    """Load and validate Identity Service settings."""
    return Settings()

