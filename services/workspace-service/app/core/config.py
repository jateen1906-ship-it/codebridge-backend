"""Environment-based configuration, resource caps, and archive limits for Workspace Service."""

from __future__ import annotations

from typing import Literal
from urllib.parse import urlparse

from pydantic import Field, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

_DEV_DEFAULT_DATABASE_URL = (
    "postgresql+asyncpg://postgres:change_me_local_only@localhost:5432/codebridge_workspace"
)


def _validate_http_url(value: str, field_name: str) -> str:
    cleaned = value.strip().rstrip("/")
    parsed = urlparse(cleaned)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise ValueError(f"{field_name} must be a valid HTTP or HTTPS URL.")
    if parsed.username or parsed.password:
        raise ValueError(f"{field_name} must not contain embedded credentials.")
    return cleaned


class Settings(BaseSettings):
    """Pydantic Settings for CodeBridge Workspace Service."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=True,
        extra="ignore",
    )

    APP_NAME: str = "CodeBridge Workspace Service"
    APP_VERSION: str = "1.0.0"
    APP_ENV: Literal["development", "staging", "production", "test"] = "development"
    DEBUG: bool = False
    HOST: str = "0.0.0.0"
    PORT: int = Field(default=8005, ge=1, le=65535)
    API_V1_PREFIX: str = "/api/v1"
    LOG_LEVEL: Literal["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"] = "INFO"

    # Database configuration
    DATABASE_URL: str = _DEV_DEFAULT_DATABASE_URL
    DB_ECHO: bool = False
    DATABASE_POOL_SIZE: int = Field(default=10, ge=1, le=100)
    DATABASE_MAX_OVERFLOW: int = Field(default=20, ge=0, le=200)

    # Peer microservices (synchronous HTTPX)
    IDENTITY_SERVICE_URL: str = "http://localhost:8001"
    PROJECT_SERVICE_URL: str = "http://localhost:8002"
    GITHUB_SERVICE_URL: str = "http://localhost:8003"
    SERVICE_CLIENT_TIMEOUT_SECONDS: float = Field(default=10.0, gt=0.0, le=60.0)

    # Docker runtime & sandbox configuration
    WORKSPACE_DOCKER_MODE: Literal["docker", "mock"] = "mock"
    WORKSPACE_STORAGE_ROOT: str = "./workspace_data"
    WORKSPACE_DEFAULT_IMAGE: str = "python:3.13-slim"
    WORKSPACE_ALLOWED_IMAGES: str = "python:3.13-slim,node:20-slim,ubuntu:24.04"
    WORKSPACE_CPU_LIMIT: float = Field(default=1.0, gt=0.0, le=4.0)
    WORKSPACE_MEMORY_LIMIT_MB: int = Field(default=512, ge=64, le=4096)
    WORKSPACE_PIDS_LIMIT: int = Field(default=128, ge=16, le=1024)
    WORKSPACE_NETWORK_DISABLED: bool = True
    WORKSPACE_TTL_HOURS: int = Field(default=24, ge=1, le=168)
    WORKSPACE_MAX_ACTIVE_PER_USER: int = Field(default=5, ge=1, le=25)

    # Archive extraction safety bounds
    WORKSPACE_MAX_ARCHIVE_BYTES: int = Field(default=10_485_760, ge=1024, le=104_857_600)
    WORKSPACE_MAX_EXTRACT_BYTES: int = Field(default=52_428_800, ge=4096, le=524_288_000)
    WORKSPACE_MAX_EXTRACT_FILES: int = Field(default=2000, ge=1, le=20000)
    WORKSPACE_MAX_COMPRESSION_RATIO: float = Field(default=50.0, ge=2.0, le=500.0)
    WORKSPACE_LOG_TAIL_LINES: int = Field(default=200, ge=10, le=1000)

    @property
    def allowed_image_set(self) -> frozenset[str]:
        return frozenset(
            item.strip() for item in self.WORKSPACE_ALLOWED_IMAGES.split(",") if item.strip()
        )

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
        "WORKSPACE_STORAGE_ROOT",
        "WORKSPACE_DEFAULT_IMAGE",
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

    @field_validator("IDENTITY_SERVICE_URL", "PROJECT_SERVICE_URL", "GITHUB_SERVICE_URL")
    @classmethod
    def _validate_urls(cls, value: str, info) -> str:
        return _validate_http_url(value, info.field_name)

    @model_validator(mode="after")
    def _validate_cross_field_rules(self) -> "Settings":
        if self.WORKSPACE_DEFAULT_IMAGE not in self.allowed_image_set:
            raise ValueError("WORKSPACE_DEFAULT_IMAGE must be included in WORKSPACE_ALLOWED_IMAGES.")
        if self.WORKSPACE_MAX_ARCHIVE_BYTES > self.WORKSPACE_MAX_EXTRACT_BYTES:
            raise ValueError("WORKSPACE_MAX_ARCHIVE_BYTES must not exceed WORKSPACE_MAX_EXTRACT_BYTES.")

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
            if self.WORKSPACE_DOCKER_MODE != "docker":
                raise ValueError("WORKSPACE_DOCKER_MODE must be 'docker' in production/staging.")
            if not self.WORKSPACE_NETWORK_DISABLED:
                raise ValueError("WORKSPACE_NETWORK_DISABLED must be true in production/staging.")
        return self


def load_settings() -> Settings:
    """Load and validate Workspace Service configuration."""
    return Settings()

