"""Structured JSON logging without exposing secrets or sensitive payloads."""

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
        "proxy-authorization",
        "cookie",
        "set-cookie",
        "x-api-key",
        "api_key",
        "token",
        "access_token",
        "refresh_token",
        "secret",
        "password",
        "body",
        "request_body",
        "response_body",
    }
)

_ALLOWED_EXTRA_KEYS = (
    "method",
    "path",
    "status_code",
    "duration_ms",
    "target_service",
    "error_code",
    "event",
)


class RequestContextFilter(logging.Filter):
    """Attach the active correlation request ID from contextvars to every log record."""

    def filter(self, record: logging.LogRecord) -> bool:
        if not hasattr(record, "request_id"):
            record.request_id = request_id_ctx.get("-")
        return True


class JsonLogFormatter(logging.Formatter):
    """Render log records as single-line structured JSON."""

    def __init__(self, service_name: str = "api-gateway") -> None:
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


def configure_logging(level: str = "INFO", service_name: str = "api-gateway") -> logging.Logger:
    """Configure Gateway root/application structured JSON logging."""
    numeric_level = getattr(logging, level.upper(), logging.INFO)
    logger = logging.getLogger("api_gateway")
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


def get_logger(name: str = "api_gateway") -> logging.Logger:
    """Return a child logger under the configured Gateway logger hierarchy."""
    if name.startswith("api_gateway"):
        return logging.getLogger(name)
    return logging.getLogger(f"api_gateway.{name}")

