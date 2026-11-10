"""Standardized exceptions and Gateway-compatible JSON error handlers for GitHub Service."""

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


class GitHubServiceError(Exception):
    """Base domain/application exception for GitHub Service."""

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


class BadRequestError(GitHubServiceError):
    def __init__(
        self,
        message: str = "The request could not be processed.",
        error_code: str = "BAD_REQUEST",
    ) -> None:
        super().__init__(
            status_code=status.HTTP_400_BAD_REQUEST,
            error_code=error_code,
            message=message,
        )


class UnauthorizedError(GitHubServiceError):
    def __init__(self, message: str = "Authentication credentials were not provided.") -> None:
        super().__init__(
            status_code=status.HTTP_401_UNAUTHORIZED,
            error_code="UNAUTHORIZED",
            message=message,
            headers={"WWW-Authenticate": "Bearer"},
        )


class ForbiddenError(GitHubServiceError):
    def __init__(self, message: str = "You do not have permission to perform this action.") -> None:
        super().__init__(
            status_code=status.HTTP_403_FORBIDDEN,
            error_code="FORBIDDEN",
            message=message,
        )


class NotFoundError(GitHubServiceError):
    def __init__(
        self,
        message: str = "The requested resource was not found.",
        error_code: str = "NOT_FOUND",
    ) -> None:
        super().__init__(
            status_code=status.HTTP_404_NOT_FOUND,
            error_code=error_code,
            message=message,
        )


class ConflictError(GitHubServiceError):
    def __init__(
        self,
        message: str = "Request conflicts with the current resource state.",
        error_code: str = "CONFLICT",
    ) -> None:
        super().__init__(
            status_code=status.HTTP_409_CONFLICT,
            error_code=error_code,
            message=message,
        )


class ValidationError(GitHubServiceError):
    def __init__(self, message: str = "Request validation failed.") -> None:
        super().__init__(
            status_code=_HTTP_422_STATUS,
            error_code="VALIDATION_ERROR",
            message=message,
        )


class GitHubRateLimitError(GitHubServiceError):
    def __init__(
        self,
        message: str = "GitHub API rate limit exceeded. Please try again later.",
    ) -> None:
        super().__init__(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            error_code="GITHUB_RATE_LIMITED",
            message=message,
        )


class GitHubUpstreamError(GitHubServiceError):
    def __init__(
        self,
        message: str = "Upstream GitHub service failed to process the request.",
        error_code: str = "GITHUB_UPSTREAM_ERROR",
    ) -> None:
        super().__init__(
            status_code=status.HTTP_502_BAD_GATEWAY,
            error_code=error_code,
            message=message,
        )


class UpstreamServiceUnavailableError(GitHubServiceError):
    def __init__(
        self,
        message: str = "Required verification service is temporarily unavailable.",
        error_code: str = "SERVICE_UNAVAILABLE",
    ) -> None:
        super().__init__(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            error_code=error_code,
            message=message,
        )


class UpstreamTimeoutError(GitHubServiceError):
    def __init__(
        self,
        message: str = "Upstream service timed out while processing the request.",
        error_code: str = "UPSTREAM_TIMEOUT",
    ) -> None:
        super().__init__(
            status_code=status.HTTP_504_GATEWAY_TIMEOUT,
            error_code=error_code,
            message=message,
        )


def resolve_request_id(request: Request | None = None) -> str:
    """Resolve active correlation request ID from request state or contextvars."""
    if request is not None:
        state_id = getattr(request.state, "request_id", None)
        if isinstance(state_id, str) and state_id:
            return state_id
    ctx_id = request_id_ctx.get("-")
    if ctx_id and ctx_id != "-":
        return ctx_id
    return str(uuid.uuid4())


def _sanitize_error_message(message: str, fallback: str = "Request failed.") -> str:
    """Scrub internal URLs and limit length before returning external messages."""
    cleaned = " ".join(message.strip().split())
    if not cleaned:
        return fallback
    return _INTERNAL_URL_RE.sub("[redacted-url]", cleaned)[:300]


def build_error_response(
    *,
    status_code: int,
    error_code: str,
    message: str,
    request_id: str,
    headers: dict[str, str] | None = None,
) -> JSONResponse:
    """Build standardized JSON error response compatible with API Gateway."""
    merged_headers = dict(headers or {})
    merged_headers["X-Request-ID"] = request_id
    return JSONResponse(
        status_code=status_code,
        content={
            "success": False,
            "error": {
                "code": error_code,
                "message": _sanitize_error_message(message),
            },
            "request_id": request_id,
        },
        headers=merged_headers,
    )


def register_exception_handlers(app: FastAPI) -> None:
    """Register centralized exception handlers on the GitHub Service FastAPI app."""

    @app.exception_handler(GitHubServiceError)
    async def handle_github_service_error(
        request: Request,
        exc: GitHubServiceError,
    ) -> JSONResponse:
        request_id = resolve_request_id(request)
        logger.info(
            "Handled GitHub Service error",
            extra={
                "event": "github_service_error",
                "method": request.method,
                "path": request.url.path,
                "status_code": exc.status_code,
                "error_code": exc.error_code,
            },
        )
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
            field_prefix = f"{'.'.join(loc_parts)}: " if loc_parts else ""
            raw_msg = str(first_err.get("msg", "Request validation failed."))
            message = f"{field_prefix}{raw_msg}"[:250]
        else:
            message = "Request validation failed."
        return build_error_response(
            status_code=_HTTP_422_STATUS,
            error_code="VALIDATION_ERROR",
            message=message,
            request_id=request_id,
        )

    @app.exception_handler(StarletteHTTPException)
    async def handle_http_exception(
        request: Request,
        exc: StarletteHTTPException,
    ) -> JSONResponse:
        request_id = resolve_request_id(request)
        if exc.status_code == status.HTTP_401_UNAUTHORIZED:
            error_code = "UNAUTHORIZED"
            message = "Authentication credentials were not provided."
        elif exc.status_code == status.HTTP_403_FORBIDDEN:
            error_code = "FORBIDDEN"
            message = "You do not have permission to perform this action."
        elif exc.status_code == status.HTTP_404_NOT_FOUND:
            error_code = "NOT_FOUND"
            message = "The requested GitHub service resource was not found."
        elif exc.status_code == status.HTTP_405_METHOD_NOT_ALLOWED:
            error_code = "METHOD_NOT_ALLOWED"
            message = "HTTP method is not allowed for this endpoint."
        else:
            error_code = "VALIDATION_ERROR"
            message = exc.detail if isinstance(exc.detail, str) else "Request failed."

        return build_error_response(
            status_code=exc.status_code,
            error_code=error_code,
            message=message,
            request_id=request_id,
        )

    @app.exception_handler(SQLAlchemyError)
    async def handle_database_error(request: Request, _exc: SQLAlchemyError) -> JSONResponse:
        request_id = resolve_request_id(request)
        logger.error(
            "Database operation failed",
            extra={
                "event": "database_error",
                "request_id": request_id,
                "method": request.method,
                "path": request.url.path,
                "status_code": status.HTTP_500_INTERNAL_SERVER_ERROR,
                "error_code": "INTERNAL_SERVER_ERROR",
            },
        )
        return build_error_response(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            error_code="INTERNAL_SERVER_ERROR",
            message="An unexpected internal server error occurred.",
            request_id=request_id,
        )

    @app.exception_handler(Exception)
    async def handle_unexpected_error(request: Request, _exc: Exception) -> JSONResponse:
        request_id = resolve_request_id(request)
        logger.error(
            "Unhandled GitHub Service exception",
            extra={
                "event": "unhandled_exception",
                "request_id": request_id,
                "method": request.method,
                "path": request.url.path,
                "status_code": status.HTTP_500_INTERNAL_SERVER_ERROR,
                "error_code": "INTERNAL_SERVER_ERROR",
            },
        )
        return build_error_response(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            error_code="INTERNAL_SERVER_ERROR",
            message="An unexpected internal server error occurred.",
            request_id=request_id,
        )

