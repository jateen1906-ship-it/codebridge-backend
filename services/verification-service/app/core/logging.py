"""Structured JSON logging and request-ID correlation context."""

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


def _scrub_text(value: str) -> str:
    return _TOKEN_RE.sub("[REDACTED]", value)


class JsonLogFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "service": "verification-service",
            "request_id": getattr(record, "request_id", request_id_ctx.get("-")),
            "message": _scrub_text(record.getMessage()),
        }
        for key in ("event", "method", "path", "status_code", "duration_ms", "error_code"):
            val = getattr(record, key, None)
            if val is not None:
                payload[key] = _scrub_text(val) if isinstance(val, str) else val
        return json.dumps(payload, default=str)


def configure_logging(level: str = "INFO") -> None:
    handler = logging.StreamHandler()
    handler.setFormatter(JsonLogFormatter())
    root = logging.getLogger()
    root.handlers.clear()
    root.addHandler(handler)
    root.setLevel(getattr(logging, level.upper(), logging.INFO))
    for name in ("uvicorn.access",):
        logging.getLogger(name).handlers.clear()
        logging.getLogger(name).propagate = False


def get_logger(name: str) -> logging.Logger:
    return logging.getLogger(f"verification_service.{name}")
