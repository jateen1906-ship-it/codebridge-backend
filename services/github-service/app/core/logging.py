"""Structured JSON logging for GitHub Service without exposing tokens, OAuth codes, or secrets."""

from __future__ import annotations

import json
import logging
import re
import sys
from contextvars import ContextVar
from datetime import datetime, timezone
from typing import Any

request_id_ctx: ContextVar[str] = ContextVar("request_id", default="-")

_SENSITIVE_KEYS = frozenset(
    {
        "authorization",
        "access_token",
        "encrypted_access_token",
        "token",
        "code",
        "client_secret",
        "encryption_key",
        "secret",
        "database_url",
        "cookie",
        "state",
    }
)

_ALLOWED_EXTRA_KEYS = (
    "event",
    "method",
    "path",
    "status_code",
    "duration_ms",
    "error_code",
    "user_id",
    "role",
    "project_id",
    "connection_id",
    "github_user_id",
    "repository_full_name",
    "target_service",
)

_SECRET_PATTERN = re.compile(
    r"(Bearer\s+[A-Za-z0-9\-._~+/]+=*|gh[opu]_[A-Za-z0-9_]+|client_secret=[^&\s]+)",
    re.IGNORECASE,
)


class RequestContextFilter(logging.Filter):
    """Attach active correlation request ID from contextvars."""

    def filter(self, record: logging.LogRecord) -> bool:
        if not hasattr(record, "request_id"):
            record.request_id = request_id_ctx.get("-")
        return True


class JsonLogFormatter(logging.Formatter):
    """Format log records as single-line structured JSON with strict secret scrubbing."""

    def __init__(self, service_name: str = "github-service") -> None:
        super().__init__()
        self.service_name = service_name

    def format(self, record: logging.LogRecord) -> str:
        safe_message = _SECRET_PATTERN.sub("[REDACTED]", record.getMessage())
        payload: dict[str, Any] = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "service": self.service_name,
            "request_id": getattr(record, "request_id", request_id_ctx.get("-")),
            "message": safe_message,
        }

        for key in _ALLOWED_EXTRA_KEYS:
            value = getattr(record, key, None)
            if value is not None:
                payload[key] = "[REDACTED]" if key.lower() in _SENSITIVE_KEYS else value

        if record.exc_info and record.exc_info[0] is not None:
            payload["exception_type"] = record.exc_info[0].__name__

        return json.dumps(payload, default=str)


def configure_logging(level: str = "INFO", service_name: str = "github-service") -> logging.Logger:
    """Configure GitHub Service structured JSON logging."""
    numeric_level = getattr(logging, level.upper(), logging.INFO)
    logger = logging.getLogger("github_service")
    logger.setLevel(numeric_level)
    logger.propagate = False

    for existing_handler in list(logger.handlers):
        logger.removeHandler(existing_handler)

    handler = logging.StreamHandler(sys.stdout)
    handler.setLevel(numeric_level)
    handler.addFilter(RequestContextFilter())
    handler.setFormatter(JsonLogFormatter(service_name=service_name))
    logger.addHandler(handler)
    return logger


def get_logger(name: str = "github_service") -> logging.Logger:
    """Return a child logger under the GitHub Service logger hierarchy."""
    if name.startswith("github_service"):
        return logging.getLogger(name)
    return logging.getLogger(f"github_service.{name}")

