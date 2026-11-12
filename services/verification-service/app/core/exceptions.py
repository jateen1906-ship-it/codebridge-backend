"""Domain exceptions and Gateway-compatible JSON error handlers for Verification Service."""

from __future__ import annotations

import re
import uuid

from fastapi import FastAPI, Request, status
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from sqlalchemy.exc import SQLAlchemyError
from starlette.exceptions import HTTPException as StarletteHTTPException

from app.core.logging import get_logger, request_id_ctx

logger = get_logger("exceptions")

_HTTP_422_STATUS = getattr(status, "HTTP_422_UNPROCESSABLE_CONTENT", 422)
_INTERNAL_URL_RE = re.compile(r"https?://[^\s/$.?#].[^\s]*", re.IGNORECASE)
_SECRET_RE = re.compile(
    r"(Bearer\s+[A-Za-z0-9\-._~+/]+=*|gh[pousr]_[A-Za-z0-9_]+|sk-[A-Za-z0-9\-_]{12,})",
    re.IGNORECASE,
)


class VerificationServiceError(Exception):
    def __init__(
        self,
        *,
        status_code: int,
        error_code: str,
        message: str,
        headers: dict[str, str] | None = None,
    ) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.error_code = error_code
        self.message = message
        self.headers = headers or {}


class UnauthorizedError(VerificationServiceError):
    def __init__(self, message: str = "Authentication credentials were not provided.") -> None:
        super().__init__(
            status_code=status.HTTP_401_UNAUTHORIZED,
            error_code="UNAUTHORIZED",
            message=message,
            headers={"WWW-Authenticate": "Bearer"},
        )


class ForbiddenError(VerificationServiceError):
    def __init__(self, message: str = "You do not have permission to perform this action.") -> None:
        super().__init__(
            status_code=status.HTTP_403_FORBIDDEN,
            error_code="FORBIDDEN",
            message=message,
        )


class NotFoundError(VerificationServiceError):
    def __init__(
        self,
        message: str = "Verification record not found.",
        error_code: str = "VERIFICATION_NOT_FOUND",
    ) -> None:
        super().__init__(
            status_code=status.HTTP_404_NOT_FOUND,
            error_code=error_code,
            message=message,
        )


class ConflictError(VerificationServiceError):
    def __init__(
        self,
        message: str = "Verification state conflict.",
        error_code: str = "VERIFICATION_CONFLICT",
    ) -> None:
        super().__init__(
            status_code=status.HTTP_409_CONFLICT,
            error_code=error_code,
            message=message,
        )


class UpstreamServiceUnavailableError(VerificationServiceError):
    def __init__(
        self,
        message: str = "Required upstream service is temporarily unavailable.",
        error_code: str = "SERVICE_UNAVAILABLE",
    ) -> None:
        super().__init__(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            error_code=error_code,
            message=message,
        )


def resolve_request_id(request: Request | None = None) -> str:
    if request is not None:
        state_id = getattr(request.state, "request_id", None)
        if isinstance(state_id, str) and state_id:
            return state_id
    ctx_id = request_id_ctx.get("-")
    if ctx_id and ctx_id != "-":
        return ctx_id
    return str(uuid.uuid4())


def sanitize_error_message(message: str, fallback: str = "Request failed.") -> str:
    cleaned = " ".join(message.strip().split())
    if not cleaned:
        return fallback
    scrubbed = _INTERNAL_URL_RE.sub("[redacted-url]", cleaned)
    scrubbed = _SECRET_RE.sub("[REDACTED]", scrubbed)
    return scrubbed[:300]


def build_error_response(
    *,
    status_code: int,
    error_code: str,
    message: str,
    request_id: str,
    headers: dict[str, str] | None = None,
) -> JSONResponse:
    merged_headers = dict(headers or {})
    merged_headers["X-Request-ID"] = request_id
    return JSONResponse(
        status_code=status_code,
        content={
            "success": False,
            "error": {"code": error_code, "message": sanitize_error_message(message)},
            "request_id": request_id,
        },
        headers=merged_headers,
    )


def register_exception_handlers(app: FastAPI) -> None:
    @app.exception_handler(VerificationServiceError)
    async def handle_domain_error(
        request: Request,
        exc: VerificationServiceError,
    ) -> JSONResponse:
        request_id = resolve_request_id(request)
        return build_error_response(
            status_code=exc.status_code,
            error_code=exc.error_code,
            message=exc.message,
            request_id=request_id,
            headers=exc.headers,
        )

    @app.exception_handler(RequestValidationError)
    async def handle_validation_error(
        request: Request,
        exc: RequestValidationError,
    ) -> JSONResponse:
        request_id = resolve_request_id(request)
        first_err = exc.errors()[0] if exc.errors() else None
        if first_err:
            loc_parts = [str(part) for part in first_err.get("loc", ()) if part != "body"]
            prefix = f"{'.'.join(loc_parts)}: " if loc_parts else ""
            message = f"{prefix}{first_err.get('msg', 'Request validation failed.')}"[:250]
        else:
            message = "Request validation failed."
        return build_error_response(
            status_code=_HTTP_422_STATUS,
            error_code="VALIDATION_ERROR",
            message=message,
            request_id=request_id,
        )

    @app.exception_handler(StarletteHTTPException)
    async def handle_http_exception(request: Request, exc: StarletteHTTPException) -> JSONResponse:
        request_id = resolve_request_id(request)
        code = "NOT_FOUND" if exc.status_code == 404 else "VALIDATION_ERROR"
        msg = (
            "The requested Verification resource was not found."
            if exc.status_code == 404
            else (exc.detail if isinstance(exc.detail, str) else "Request failed.")
        )
        return build_error_response(
            status_code=exc.status_code,
            error_code=code,
            message=msg,
            request_id=request_id,
        )

    @app.exception_handler(SQLAlchemyError)
    async def handle_db_error(request: Request, _exc: SQLAlchemyError) -> JSONResponse:
        return build_error_response(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            error_code="INTERNAL_SERVER_ERROR",
            message="An unexpected internal server error occurred.",
            request_id=resolve_request_id(request),
        )

    @app.exception_handler(Exception)
    async def handle_unexpected_error(request: Request, _exc: Exception) -> JSONResponse:
        return build_error_response(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            error_code="INTERNAL_SERVER_ERROR",
            message="An unexpected internal server error occurred.",
            request_id=resolve_request_id(request),
        )
