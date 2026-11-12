"""Environment-based configuration, limits, ChromaDB, and AI provider settings."""

from __future__ import annotations

from typing import Literal
from urllib.parse import urlparse

from pydantic import Field, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

_DEV_DEFAULT_DATABASE_URL = (
    "postgresql+asyncpg://postgres:change_me_local_only@localhost:5432/codebridge_code_context"
)

DEFAULT_SUPPORTED_EXTENSIONS = (
    ".py,.js,.jsx,.ts,.tsx,.go,.java,.c,.h,.cpp,.hpp,.cc,.cs,.php,.rb,.rs,"
    ".html,.css,.scss,.json,.yaml,.yml,.toml,.md,.sql,.sh,.bash,.zsh"
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
    """Pydantic Settings for CodeBridge Code Context Service."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=True,
        extra="ignore",
    )

    APP_NAME: str = "CodeBridge Code Context Service"
    APP_VERSION: str = "1.0.0"
    APP_ENV: Literal["development", "staging", "production", "test"] = "development"
    DEBUG: bool = False
    HOST: str = "0.0.0.0"
    PORT: int = Field(default=8004, ge=1, le=65535)
    API_V1_PREFIX: str = "/api/v1"
    LOG_LEVEL: Literal["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"] = "INFO"

    # Database configuration
    DATABASE_URL: str = _DEV_DEFAULT_DATABASE_URL
    DB_ECHO: bool = False
    DATABASE_POOL_SIZE: int = Field(default=10, ge=1, le=100)
    DATABASE_MAX_OVERFLOW: int = Field(default=20, ge=0, le=200)

    # Synchronous HTTP/REST peer microservices
    IDENTITY_SERVICE_URL: str = "http://localhost:8001"
    PROJECT_SERVICE_URL: str = "http://localhost:8002"
    GITHUB_SERVICE_URL: str = "http://localhost:8003"
    SERVICE_CLIENT_TIMEOUT_SECONDS: float = Field(default=10.0, gt=0.0, le=60.0)

    # ChromaDB vector store settings
    CHROMA_MODE: Literal["persistent", "ephemeral"] = "persistent"
    CHROMA_PERSIST_DIRECTORY: str = "./chroma_data"
    CHROMA_COLLECTION_PREFIX: str = "codebridge_project"

    # Configurable Embedding Provider
    EMBEDDING_PROVIDER: Literal["openai", "ollama", "mock"] = "mock"
    EMBEDDING_MODEL: str = "text-embedding-3-small"
    EMBEDDING_API_BASE_URL: str = "https://api.openai.com/v1"
    EMBEDDING_API_KEY: str | None = None
    EMBEDDING_TIMEOUT_SECONDS: float = Field(default=15.0, gt=0.0, le=120.0)
    EMBEDDING_BATCH_SIZE: int = Field(default=32, ge=1, le=256)
    EMBEDDING_DIMENSION: int = Field(default=64, ge=8, le=4096)

    # Configurable LLM Provider
    LLM_PROVIDER: Literal["openai", "ollama", "mock", "none"] = "none"
    LLM_MODEL: str = "gpt-4o-mini"
    LLM_API_BASE_URL: str = "https://api.openai.com/v1"
    LLM_API_KEY: str | None = None
    LLM_TIMEOUT_SECONDS: float = Field(default=25.0, gt=0.0, le=120.0)
    LLM_TEMPERATURE: float = Field(default=0.1, ge=0.0, le=1.0)
    LLM_MAX_TOKENS: int = Field(default=900, ge=64, le=4096)
    LLM_FALLBACK_ON_ERROR: bool = True

    # Chunking and RAG retrieval settings
    CHUNK_SIZE: int = Field(default=1200, ge=100, le=8000)
    CHUNK_OVERLAP: int = Field(default=200, ge=0, le=2000)
    TOP_K_DEFAULT: int = Field(default=5, ge=1, le=50)
    TOP_K_MAX: int = Field(default=15, ge=1, le=50)

    # Bounded repository ingestion limits
    MAX_FILES_PER_INDEX: int = Field(default=250, ge=1, le=5000)
    MAX_DIRECTORY_TRAVERSALS: int = Field(default=100, ge=1, le=2000)
    MAX_DIRECTORY_DEPTH: int = Field(default=10, ge=1, le=30)
    MAX_FILE_BYTES: int = Field(default=131072, ge=256, le=2097152)
    MAX_TOTAL_REPOSITORY_BYTES: int = Field(default=3145728, ge=1024, le=52428800)
    MAX_INDEX_DURATION_SECONDS: float = Field(default=60.0, gt=0.0, le=600.0)
    MAX_CONTENT_RESPONSE_BYTES: int = Field(default=16384, ge=512, le=131072)
    INDEX_RETENTION_VERSIONS: int = Field(default=2, ge=1, le=20)
    SUPPORTED_EXTENSIONS: str = DEFAULT_SUPPORTED_EXTENSIONS

    @property
    def supported_extension_set(self) -> frozenset[str]:
        """Normalized lowercase file extensions supported for indexing."""
        items = set()
        for raw in self.SUPPORTED_EXTENSIONS.replace(" ", ",").split(","):
            ext = raw.strip().lower()
            if ext:
                if not ext.startswith("."):
                    ext = f".{ext}"
                items.add(ext)
        return frozenset(items)

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
        "CHROMA_PERSIST_DIRECTORY",
        "CHROMA_COLLECTION_PREFIX",
        "EMBEDDING_MODEL",
        "LLM_MODEL",
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
        "EMBEDDING_API_BASE_URL",
        "LLM_API_BASE_URL",
    )
    @classmethod
    def _validate_urls(cls, value: str, info) -> str:
        return _validate_http_url(value, info.field_name)

    @field_validator("EMBEDDING_API_KEY", "LLM_API_KEY", mode="before")
    @classmethod
    def _clean_optional_secret(cls, value: object) -> str | None:
        if value is None:
            return None
        if not isinstance(value, str):
            raise ValueError("API key must be a string.")
        cleaned = value.strip()
        return cleaned or None

    @model_validator(mode="after")
    def _validate_cross_field_rules(self) -> "Settings":
        if self.CHUNK_OVERLAP >= self.CHUNK_SIZE:
            raise ValueError("CHUNK_OVERLAP must be strictly smaller than CHUNK_SIZE.")
        if self.TOP_K_DEFAULT > self.TOP_K_MAX:
            raise ValueError("TOP_K_DEFAULT must not exceed TOP_K_MAX.")
        if self.MAX_FILE_BYTES > self.MAX_TOTAL_REPOSITORY_BYTES:
            raise ValueError("MAX_FILE_BYTES must not exceed MAX_TOTAL_REPOSITORY_BYTES.")

        if self.EMBEDDING_PROVIDER == "openai" and not self.EMBEDDING_API_KEY:
            if self.APP_ENV in {"staging", "production"}:
                raise ValueError(
                    "EMBEDDING_API_KEY is required when EMBEDDING_PROVIDER='openai' in staging/production."
                )

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
            if self.CHROMA_MODE != "persistent":
                raise ValueError("CHROMA_MODE must be 'persistent' in production/staging.")
            if self.EMBEDDING_PROVIDER == "mock" or self.LLM_PROVIDER == "mock":
                raise ValueError(
                    "Mock embedding/LLM providers are not permitted in production/staging."
                )
            if self.LLM_PROVIDER == "openai" and not self.LLM_API_KEY:
                raise ValueError(
                    "LLM_API_KEY is required when LLM_PROVIDER='openai' in staging/production."
                )
        return self


def load_settings() -> Settings:
    """Load and validate Code Context Service configuration."""
    return Settings()
