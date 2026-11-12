"""Structured JSON logging and secret-scrubbing utilities for Audit Service."""

from __future__ import annotations

import contextvars
import json
import logging
import re
from datetime import datetime, timezone
from typing import Any

request_id_ctx: contextvars.ContextVar[str] = contextvars.ContextVar("request_id", default="-")
_TOKEN_RE = re.compile(
    r"(Bearer\s+[A-Za-z0-9\-._~+/]+=*|gh[pousr]_[A-Za-z0-9_]+|sk-[A-Za-z0-9\-_]{12,})",
    re.IGNORECASE,
)
_SENSITIVE_KEY_PARTS = (
    "token",
    "secret",
    "password",
    "passwd",
    "authorization",
    "api_key",
    "apikey",
    "credential",
    "private_key",
    "cookie",
    "session_key",
)


def scrub_text(value: str) -> str:
    return _TOKEN_RE.sub("[REDACTED]", value)


def scrub_metadata(obj: Any) -> Any:
    """Recursively scrub secrets and credentials from arbitrary audit metadata."""
    if isinstance(obj, dict):
        sanitized: dict[str, Any] = {}
        for k, v in obj.items():
            key_lower = str(k).lower()
            if any(part in key_lower for part in _SENSITIVE_KEY_PARTS):
                sanitized[str(k)] = "[REDACTED]"
            else:
                sanitized[str(k)] = scrub_metadata(v)
        return sanitized
    if isinstance(obj, list):
        return [scrub_metadata(item) for item in obj]
    if isinstance(obj, str):
        return scrub_text(obj)[:1000]
    return obj


class JsonLogFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "service": "audit-service",
            "request_id": getattr(record, "request_id", request_id_ctx.get("-")),
            "message": scrub_text(record.getMessage()),
        }
        for key in ("event", "method", "path", "status_code", "duration_ms"):
            val = getattr(record, key, None)
            if val is not None:
                payload[key] = scrub_text(val) if isinstance(val, str) else val
        return json.dumps(payload, default=str)


def configure_logging(level: str = "INFO") -> None:
    handler = logging.StreamHandler()
    handler.setFormatter(JsonLogFormatter())
    root = logging.getLogger()
    root.handlers.clear()
    root.addHandler(handler)
    root.setLevel(getattr(logging, level.upper(), logging.INFO))
    logging.getLogger("uvicorn.access").handlers.clear()
    logging.getLogger("uvicorn.access").propagate = False


def get_logger(name: str) -> logging.Logger:
    return logging.getLogger(f"audit_service.{name}")
