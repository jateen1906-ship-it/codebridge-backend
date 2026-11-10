"""API Gateway configuration using Pydantic Settings."""

from __future__ import annotations

import json
from typing import Annotated, Literal
from urllib.parse import urlparse

from pydantic import Field, field_validator, model_validator
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict


def _validate_http_url(value: str, field_name: str) -> str:
    """Validate that a configured service URL is a non-empty HTTP/HTTPS URL."""
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{field_name} must be a non-empty HTTP or HTTPS URL.")

    cleaned = value.strip().rstrip("/")
    parsed = urlparse(cleaned)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise ValueError(
            f"{field_name} must be a valid HTTP or HTTPS URL with a host (got {value!r})."
        )
    if parsed.username or parsed.password:
        raise ValueError(f"{field_name} must not contain embedded credentials.")
    return cleaned


class Settings(BaseSettings):
    """Environment-driven settings for the CodeBridge API Gateway."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=True,
        extra="ignore",
    )

    # Application settings
    APP_NAME: str = "CodeBridge API Gateway"
    APP_VERSION: str = "1.0.0"
    APP_ENV: Literal["development", "staging", "production", "test"] = "development"
    DEBUG: bool = False
    HOST: str = "0.0.0.0"
    PORT: int = Field(default=8000, ge=1, le=65535)
    API_V1_PREFIX: str = "/api/v1"
    LOG_LEVEL: Literal["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"] = "INFO"

    # CORS settings
    CORS_ORIGINS: Annotated[list[str], NoDecode] = Field(
        default_factory=lambda: ["http://localhost:3000", "http://127.0.0.1:3000"]
    )
    CORS_ALLOW_METHODS: Annotated[list[str], NoDecode] = Field(
        default_factory=lambda: ["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"]
    )
    CORS_ALLOW_HEADERS: Annotated[list[str], NoDecode] = Field(
        default_factory=lambda: [
            "Authorization",
            "Content-Type",
            "X-Request-ID",
            "Accept",
        ]
    )
    CORS_ALLOW_CREDENTIALS: bool = False

    # HTTPX transport configuration
    REQUEST_TIMEOUT_SECONDS: float = Field(default=10.0, gt=0.0, le=300.0)
    CONNECT_TIMEOUT_SECONDS: float = Field(default=5.0, gt=0.0, le=60.0)
    HTTP_MAX_CONNECTIONS: int = Field(default=100, ge=1, le=2000)
    HTTP_MAX_KEEPALIVE_CONNECTIONS: int = Field(default=20, ge=0, le=500)

    # Downstream microservice base URLs
    IDENTITY_SERVICE_URL: str = "http://localhost:8001"
    PROJECT_SERVICE_URL: str = "http://localhost:8002"
    GITHUB_SERVICE_URL: str = "http://localhost:8003"
    CODE_CONTEXT_SERVICE_URL: str = "http://localhost:8004"
    WORKSPACE_SERVICE_URL: str = "http://localhost:8005"
    REVIEW_SERVICE_URL: str = "http://localhost:8006"
    VERIFICATION_SERVICE_URL: str = "http://localhost:8007"
    PAYMENT_SERVICE_URL: str = "http://localhost:8008"
    NOTIFICATION_SERVICE_URL: str = "http://localhost:8009"
    ADMIN_SERVICE_URL: str = "http://localhost:8010"
    AUDIT_SERVICE_URL: str = "http://localhost:8011"
    OBSERVABILITY_SERVICE_URL: str = "http://localhost:8012"

    @field_validator("DEBUG", mode="before")
    @classmethod
    def _parse_debug_flag(cls, value: object) -> bool:
        if isinstance(value, bool):
            return value
        if isinstance(value, str):
            normalized = value.strip().lower()
            if normalized in {"1", "true", "yes", "on"}:
                return True
            if normalized in {"0", "false", "no", "off", "release"}:
                return False
        raise ValueError("DEBUG must be a valid boolean value (true/false).")

    @field_validator("APP_NAME", "HOST")
    @classmethod
    def _validate_non_empty_text(cls, value: str, info) -> str:
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

    @field_validator("CORS_ORIGINS", "CORS_ALLOW_METHODS", "CORS_ALLOW_HEADERS", mode="before")
    @classmethod
    def _parse_string_list(cls, value: object) -> list[str]:
        if isinstance(value, str):
            stripped = value.strip()
            if not stripped:
                return []
            if stripped.startswith("["):
                parsed = json.loads(stripped)
                if not isinstance(parsed, list):
                    raise ValueError("Expected a JSON list of strings.")
                return [str(item).strip() for item in parsed if str(item).strip()]
            return [item.strip() for item in stripped.split(",") if item.strip()]
        if isinstance(value, list):
            return [str(item).strip() for item in value if str(item).strip()]
        raise ValueError("Expected a comma-separated string or list of strings.")

    @field_validator(
        "IDENTITY_SERVICE_URL",
        "PROJECT_SERVICE_URL",
        "GITHUB_SERVICE_URL",
        "CODE_CONTEXT_SERVICE_URL",
        "WORKSPACE_SERVICE_URL",
        "REVIEW_SERVICE_URL",
        "VERIFICATION_SERVICE_URL",
        "PAYMENT_SERVICE_URL",
        "NOTIFICATION_SERVICE_URL",
        "ADMIN_SERVICE_URL",
        "AUDIT_SERVICE_URL",
        "OBSERVABILITY_SERVICE_URL",
    )
    @classmethod
    def _validate_service_urls(cls, value: str, info) -> str:
        return _validate_http_url(value, info.field_name)

    @model_validator(mode="after")
    def _validate_cross_field_rules(self) -> "Settings":
        if self.CORS_ALLOW_CREDENTIALS and "*" in self.CORS_ORIGINS:
            raise ValueError(
                "CORS_ALLOW_CREDENTIALS cannot be enabled when wildcard '*' is in CORS_ORIGINS."
            )
        if self.APP_ENV == "production" and "*" in self.CORS_ORIGINS:
            raise ValueError(
                "Wildcard '*' CORS_ORIGINS is not permitted when APP_ENV=production."
            )
        for origin in self.CORS_ORIGINS:
            if origin == "*":
                continue
            parsed = urlparse(origin)
            if parsed.scheme not in {"http", "https"} or not parsed.netloc:
                raise ValueError(
                    f"Invalid CORS origin {origin!r}. Origins must include http:// or https://."
                )
        if self.HTTP_MAX_KEEPALIVE_CONNECTIONS > self.HTTP_MAX_CONNECTIONS:
            raise ValueError(
                "HTTP_MAX_KEEPALIVE_CONNECTIONS must not exceed HTTP_MAX_CONNECTIONS."
            )
        return self


def load_settings() -> Settings:
    """Load and validate Gateway settings, raising clear errors on misconfiguration."""
    return Settings()
