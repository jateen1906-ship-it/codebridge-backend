"""Structured JSON logging for Observability Service."""

from __future__ import annotations

import json
import logging
import sys
from contextvars import ContextVar
from datetime import UTC, datetime
from typing import Any

request_id_ctx: ContextVar[str] = ContextVar("request_id", default="-")
_SERVICE_LABEL = "observability-service"


class StructuredJSONFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = {
            "timestamp": datetime.now(UTC).isoformat(),
            "level": record.levelname,
            "service": _SERVICE_LABEL,
            "logger": record.name,
            "request_id": request_id_ctx.get("-"),
            "message": record.getMessage(),
        }
        for attr in ("event", "method", "path", "status_code", "duration_ms", "service_name"):
            value = getattr(record, attr, None)
            if value is not None:
                payload[attr] = value
        return json.dumps(payload, default=str)


def configure_logging(log_level: str = "INFO") -> None:
    root = logging.getLogger()
    root.setLevel(getattr(logging, log_level.upper(), logging.INFO))
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(StructuredJSONFormatter())
    root.handlers.clear()
    root.addHandler(handler)


def get_logger(name: str) -> logging.Logger:
    return logging.getLogger(f"{_SERVICE_LABEL}.{name}")
