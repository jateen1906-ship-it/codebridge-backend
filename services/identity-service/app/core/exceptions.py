"""Standardized Identity Service exceptions and error handlers compatible with API Gateway."""

from __future__ import annotations

import uuid

from fastapi import FastAPI, Request, status
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from sqlalchemy.exc import SQLAlchemyError
from starlette.exceptions import HTTPException as StarletteHTTPException

from app.core.logging import get_logger, request_id_ctx

logger = get_logger("exceptions")


class IdentityError(Exception):
    """Base domain/application exception for Identity Service."""

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


_HTTP_422_STATUS = getattr(status, "HTTP_422_UNPROCESSABLE_CONTENT", 422)


class ValidationError(IdentityError):
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


class InvalidCredentialsError(IdentityError):
    def __init__(self, message: str = "Invalid email or password.") -> None:
        super().__init__(
            status_code=status.HTTP_401_UNAUTHORIZED,
            error_code="INVALID_CREDENTIALS",
            message=message,
            headers={"WWW-Authenticate": "Bearer"},
        )


class EmailAlreadyExistsError(IdentityError):
    def __init__(self, message: str = "An account with this email already exists.") -> None:
        super().__init__(
            status_code=status.HTTP_409_CONFLICT,
            error_code="EMAIL_ALREADY_EXISTS",
            message=message,
        )


class UnauthorizedError(IdentityError):
    def __init__(self, message: str = "Authentication credentials were not provided.") -> None:
        super().__init__(
            status_code=status.HTTP_401_UNAUTHORIZED,
            error_code="UNAUTHORIZED",
            message=message,
            headers={"WWW-Authenticate": "Bearer"},
        )


class ForbiddenError(IdentityError):
    def __init__(self, message: str = "You do not have permission to perform this action.") -> None:
        super().__init__(
            status_code=status.HTTP_403_FORBIDDEN,
            error_code="FORBIDDEN",
            message=message,
        )


class TokenExpiredError(IdentityError):
    def __init__(self, message: str = "Token has expired.") -> None:
        super().__init__(
            status_code=status.HTTP_401_UNAUTHORIZED,
            error_code="TOKEN_EXPIRED",
            message=message,
            headers={"WWW-Authenticate": "Bearer"},
        )


class InvalidTokenError(IdentityError):
    def __init__(self, message: str = "Invalid authentication token.") -> None:
        super().__init__(
            status_code=status.HTTP_401_UNAUTHORIZED,
            error_code="INVALID_TOKEN",
            message=message,
            headers={"WWW-Authenticate": "Bearer"},
        )


class TokenRevokedError(IdentityError):
    def __init__(self, message: str = "Token has been revoked.") -> None:
        super().__init__(
            status_code=status.HTTP_401_UNAUTHORIZED,
            error_code="TOKEN_REVOKED",
            message=message,
            headers={"WWW-Authenticate": "Bearer"},
        )


class UserNotFoundError(IdentityError):
    def __init__(self, message: str = "User not found.") -> None:
        super().__init__(
            status_code=status.HTTP_404_NOT_FOUND,
            error_code="USER_NOT_FOUND",
            message=message,
        )


def resolve_request_id(request: Request | None = None) -> str:
    """Get correlation request ID from request state or contextvars."""
    if request is not None:
        state_id = getattr(request.state, "request_id", None)
        if isinstance(state_id, str) and state_id:
            return state_id
    ctx_id = request_id_ctx.get("-")
    if ctx_id and ctx_id != "-":
        return ctx_id
    return str(uuid.uuid4())


def build_error_response(
    *,
    status_code: int,
    error_code: str,
    message: str,
    request_id: str,
    headers: dict[str, str] | None = None,
) -> JSONResponse:
    """Build standard JSON error response compatible with API Gateway."""
    merged_headers = dict(headers or {})
    merged_headers["X-Request-ID"] = request_id
    return JSONResponse(
        status_code=status_code,
        content={
            "success": False,
            "error": {
                "code": error_code,
                "message": message,
            },
            "request_id": request_id,
        },
        headers=merged_headers,
    )


def register_exception_handlers(app: FastAPI) -> None:
    """Register global exception handlers for Identity Service."""

    @app.exception_handler(IdentityError)
    async def handle_identity_error(request: Request, exc: IdentityError) -> JSONResponse:
        request_id = resolve_request_id(request)
        logger.info(
            "Handled identity error",
            extra={
                "event": "identity_error",
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
            error_code = "USER_NOT_FOUND" if "freelancers" in request.url.path else "VALIDATION_ERROR"
            message = "The requested resource was not found."
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
    async def handle_database_error(request: Request, exc: SQLAlchemyError) -> JSONResponse:
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
    async def handle_unexpected_error(request: Request, exc: Exception) -> JSONResponse:
        request_id = resolve_request_id(request)
        logger.error(
            "Unhandled Identity Service exception",
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

