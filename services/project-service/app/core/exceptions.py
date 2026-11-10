"""Standardized exceptions and Gateway-compatible JSON error handlers for Project Service."""

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


class ProjectServiceError(Exception):
    """Base domain/application exception for Project Service."""

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


class ValidationError(ProjectServiceError):
    def __init__(
        self,
        message: str = "Request validation failed.",
        status_code: int = _HTTP_422_STATUS,
    ) -> None:
        super().__init__(
            status_code=status_code,
            error_code="VALIDATION_ERROR",
            message=message,
        )


class UnauthorizedError(ProjectServiceError):
    def __init__(self, message: str = "Authentication credentials were not provided.") -> None:
        super().__init__(
            status_code=status.HTTP_401_UNAUTHORIZED,
            error_code="UNAUTHORIZED",
            message=message,
            headers={"WWW-Authenticate": "Bearer"},
        )


class ForbiddenError(ProjectServiceError):
    def __init__(self, message: str = "You do not have permission to perform this action.") -> None:
        super().__init__(
            status_code=status.HTTP_403_FORBIDDEN,
            error_code="FORBIDDEN",
            message=message,
        )


class ProjectNotFoundError(ProjectServiceError):
    def __init__(self, message: str = "Project not found.") -> None:
        super().__init__(
            status_code=status.HTTP_404_NOT_FOUND,
            error_code="PROJECT_NOT_FOUND",
            message=message,
        )


class ProjectAlreadyAssignedError(ProjectServiceError):
    def __init__(self, message: str = "Project already has an active assignment.") -> None:
        super().__init__(
            status_code=status.HTTP_409_CONFLICT,
            error_code="PROJECT_ALREADY_ASSIGNED",
            message=message,
        )


class InvalidProjectStatusError(ProjectServiceError):
    def __init__(
        self,
        message: str = "Action is not permitted for the current project status.",
        status_code: int = status.HTTP_409_CONFLICT,
    ) -> None:
        super().__init__(
            status_code=status_code,
            error_code="INVALID_PROJECT_STATUS",
            message=message,
        )


class FreelancerNotFoundError(ProjectServiceError):
    def __init__(self, message: str = "Target freelancer was not found.") -> None:
        super().__init__(
            status_code=status.HTTP_404_NOT_FOUND,
            error_code="FREELANCER_NOT_FOUND",
            message=message,
        )


class InvalidFreelancerError(ProjectServiceError):
    def __init__(self, message: str = "Target user is not an eligible freelancer.") -> None:
        super().__init__(
            status_code=status.HTTP_400_BAD_REQUEST,
            error_code="INVALID_FREELANCER",
            message=message,
        )


class IdentityServiceUnavailableError(ProjectServiceError):
    def __init__(
        self,
        message: str = "Identity verification service is temporarily unavailable.",
    ) -> None:
        super().__init__(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            error_code="IDENTITY_SERVICE_UNAVAILABLE",
            message=message,
        )


class ConcurrentAssignmentConflictError(ProjectServiceError):
    def __init__(
        self,
        message: str = "Concurrent assignment detected; project was already assigned.",
    ) -> None:
        super().__init__(
            status_code=status.HTTP_409_CONFLICT,
            error_code="CONCURRENT_ASSIGNMENT_CONFLICT",
            message=message,
        )


def resolve_request_id(request: Request | None = None) -> str:
    """Resolve canonical correlation request ID from request state or contextvars."""
    if request is not None:
        state_id = getattr(request.state, "request_id", None)
        if isinstance(state_id, str) and state_id:
            return state_id
    ctx_id = request_id_ctx.get("-")
    if ctx_id and ctx_id != "-":
        return ctx_id
    return str(uuid.uuid4())


def _sanitize_error_message(message: str, fallback: str = "Request failed.") -> str:
    """Remove internal URLs or excessive whitespace before sending messages externally."""
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
    """Register global exception handlers on the Project Service FastAPI app."""

    @app.exception_handler(ProjectServiceError)
    async def handle_project_error(request: Request, exc: ProjectServiceError) -> JSONResponse:
        request_id = resolve_request_id(request)
        logger.info(
            "Handled Project Service error",
            extra={
                "event": "project_service_error",
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
            error_code = "PROJECT_NOT_FOUND"
            message = "The requested project resource was not found."
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
            "Unhandled Project Service exception",
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

