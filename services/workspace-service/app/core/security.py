"""Security primitives, ref validation, and CorrelationIDMiddleware for Workspace Service."""

from __future__ import annotations

import enum
import re
import time
import uuid
from dataclasses import dataclass

from fastapi import Request
from fastapi.security import HTTPAuthorizationCredentials
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from app.core.exceptions import BadRequestError
from app.core.logging import get_logger, request_id_ctx

logger = get_logger("correlation")

REQUEST_ID_HEADER = "X-Request-ID"
_REQUEST_ID_HEADER_BYTES = b"x-request-id"
_UUID_CANONICAL_RE = re.compile(
    r"^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$"
)
_GIT_REF_RE = re.compile(r"^[A-Za-z0-9._/@+-]{1,200}$")


class UserRole(str, enum.Enum):
    """Supported CodeBridge platform roles resolved from Identity Service."""

    OWNER = "OWNER"
    FREELANCER = "FREELANCER"
    ADMIN = "ADMIN"


@dataclass(frozen=True, slots=True)
class AuthenticatedUser:
    """Verified user identity returned by Identity Service."""

    id: uuid.UUID
    role: UserRole
    is_active: bool
    is_verified: bool = False


def parse_or_generate_request_id(raw_value: str | None) -> str:
    """Preserve a valid incoming canonical UUID X-Request-ID or generate a fresh UUIDv4."""
    if raw_value and _UUID_CANONICAL_RE.match(raw_value):
        try:
            return str(uuid.UUID(raw_value))
        except ValueError:
            pass
    return str(uuid.uuid4())


def get_request_id(request: Request) -> str:
    """Retrieve active correlation request ID from request state or contextvars."""
    return getattr(request.state, "request_id", None) or request_id_ctx.get("-")


def extract_bearer_token(credentials: HTTPAuthorizationCredentials | None) -> str | None:
    """Extract raw bearer token without trusting client claims locally."""
    if (
        credentials is None
        or credentials.scheme.lower() != "bearer"
        or not credentials.credentials
        or not credentials.credentials.strip()
    ):
        return None
    return credentials.credentials.strip()


def validate_git_ref(raw_ref: str | None) -> str | None:
    """Validate optional Git branch, tag, or commit SHA reference safely."""
    if raw_ref is None:
        return None
    cleaned = raw_ref.strip()
    if not cleaned:
        return None
    if (
        cleaned.startswith("-")
        or cleaned.startswith("/")
        or cleaned.endswith("/")
        or cleaned.endswith(".")
        or ".." in cleaned
        or "//" in cleaned
        or "\\" in cleaned
        or "\x00" in cleaned
        or not _GIT_REF_RE.match(cleaned)
    ):
        raise BadRequestError("Invalid Git reference.", error_code="INVALID_REF")
    return cleaned


class CorrelationIDMiddleware:
    """ASGI middleware enforcing a canonical UUID X-Request-ID per HTTP request."""

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        incoming_raw: str | None = None
        for header_name, header_value in scope.get("headers", ()):
            if header_name.lower() == _REQUEST_ID_HEADER_BYTES:
                incoming_raw = header_value.decode("latin-1", errors="ignore")
                break

        request_id = parse_or_generate_request_id(incoming_raw)
        scope.setdefault("state", {})["request_id"] = request_id
        token = request_id_ctx.set(request_id)

        start_time = time.perf_counter()
        status_code: int | None = None

        async def send_wrapper(message: Message) -> None:
            nonlocal status_code
            if message["type"] == "http.response.start":
                status_code = int(message["status"])
                raw_headers = [
                    (name, val)
                    for name, val in message.get("headers", ())
                    if name.lower() != _REQUEST_ID_HEADER_BYTES
                ]
                raw_headers.append((_REQUEST_ID_HEADER_BYTES, request_id.encode("ascii")))
                message["headers"] = raw_headers
            await send(message)

        try:
            await self.app(scope, receive, send_wrapper)
        except Exception:
            if status_code is None:
                status_code = 500
            raise
        finally:
            duration_ms = round((time.perf_counter() - start_time) * 1000.0, 2)
            logger.info(
                "HTTP request completed",
                extra={
                    "event": "http_request",
                    "method": scope.get("method", "UNKNOWN"),
                    "path": scope.get("path", "/"),
                    "status_code": status_code,
                    "duration_ms": duration_ms,
                },
            )
            request_id_ctx.reset(token)

