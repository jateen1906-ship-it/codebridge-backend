"""Structured JSON logging without leaking passwords, tokens, hashes, or secrets."""

from __future__ import annotations

import json
import logging
import sys
from contextvars import ContextVar
from datetime import datetime, timezone
from typing import Any

request_id_ctx: ContextVar[str] = ContextVar("request_id", default="-")

_SENSITIVE_KEYS = frozenset(
    {
        "authorization",
        "password",
        "password_hash",
        "token",
        "access_token",
        "refresh_token",
        "jti",
        "jti_hash",
        "secret",
        "jwt_secret_key",
        "cookie",
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
)


class RequestContextFilter(logging.Filter):
    """Attach the active correlation request ID from contextvars."""

    def filter(self, record: logging.LogRecord) -> bool:
        if not hasattr(record, "request_id"):
            record.request_id = request_id_ctx.get("-")
        return True


class JsonLogFormatter(logging.Formatter):
    """Format log records as single-line structured JSON with secret redaction."""

    def __init__(self, service_name: str = "identity-service") -> None:
        super().__init__()
        self.service_name = service_name

    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "service": self.service_name,
            "request_id": getattr(record, "request_id", request_id_ctx.get("-")),
            "message": record.getMessage(),
        }

        for key in _ALLOWED_EXTRA_KEYS:
            value = getattr(record, key, None)
            if value is not None:
                payload[key] = "[REDACTED]" if key.lower() in _SENSITIVE_KEYS else value

        if record.exc_info and record.exc_info[0] is not None:
            payload["exception_type"] = record.exc_info[0].__name__

        return json.dumps(payload, default=str)


def configure_logging(level: str = "INFO", service_name: str = "identity-service") -> logging.Logger:
    """Configure Identity Service structured JSON logging."""
    numeric_level = getattr(logging, level.upper(), logging.INFO)
    logger = logging.getLogger("identity_service")
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


def get_logger(name: str = "identity_service") -> logging.Logger:
    """Return a child logger under the Identity Service hierarchy."""
    if name.startswith("identity_service"):
        return logging.getLogger(name)
    return logging.getLogger(f"identity_service.{name}")

