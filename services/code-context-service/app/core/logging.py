"""Structured JSON logging and lightweight observability counters without secret exposure."""

from __future__ import annotations

import json
import logging
import re
import sys
import threading
from contextvars import ContextVar
from datetime import datetime, timezone
from typing import Any

request_id_ctx: ContextVar[str] = ContextVar("request_id", default="-")

_SENSITIVE_KEYS = frozenset(
    {
        "authorization",
        "access_token",
        "api_key",
        "embedding_api_key",
        "llm_api_key",
        "token",
        "secret",
        "database_url",
        "content",
        "source_code",
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
    "job_id",
    "index_version",
    "files_indexed",
    "files_skipped",
    "chunks_generated",
    "retrieval_count",
    "target_service",
)

_SECRET_PATTERN = re.compile(
    r"(Bearer\s+[A-Za-z0-9\-._~+/]+=*|gh[pousr]_[A-Za-z0-9_]+|sk-[A-Za-z0-9\-_]{12,}|api_key=[^&\s]+)",
    re.IGNORECASE,
)


class MetricsRegistry:
    """Thread-safe in-memory counters for Code Context indexing, retrieval, & provider health."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._counters: dict[str, float] = {
            "indexing_jobs_started": 0,
            "indexing_jobs_completed": 0,
            "indexing_jobs_failed": 0,
            "indexing_duration_ms_total": 0.0,
            "files_indexed_total": 0,
            "files_skipped_total": 0,
            "chunks_generated_total": 0,
            "queries_executed": 0,
            "chunks_retrieved_total": 0,
            "embedding_provider_failures": 0,
            "llm_provider_failures": 0,
            "insights_generated": 0,
        }

    def increment(self, name: str, amount: float = 1) -> None:
        with self._lock:
            self._counters[name] = self._counters.get(name, 0) + amount

    def snapshot(self) -> dict[str, float]:
        with self._lock:
            return dict(self._counters)


class RequestContextFilter(logging.Filter):
    """Attach active correlation request ID from contextvars."""

    def filter(self, record: logging.LogRecord) -> bool:
        if not hasattr(record, "request_id"):
            record.request_id = request_id_ctx.get("-")
        return True


class JsonLogFormatter(logging.Formatter):
    """Format log records as single-line structured JSON with strict secret scrubbing."""

    def __init__(self, service_name: str = "code-context-service") -> None:
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


def configure_logging(
    level: str = "INFO",
    service_name: str = "code-context-service",
) -> logging.Logger:
    """Configure Code Context Service structured JSON logging."""
    numeric_level = getattr(logging, level.upper(), logging.INFO)
    logger = logging.getLogger("code_context_service")
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


def get_logger(name: str = "code_context_service") -> logging.Logger:
    """Return a child logger under the Code Context Service hierarchy."""
    if name.startswith("code_context_service"):
        return logging.getLogger(name)
    return logging.getLogger(f"code_context_service.{name}")

