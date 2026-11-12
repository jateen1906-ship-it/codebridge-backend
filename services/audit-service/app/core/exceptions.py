"""Exceptions and error handlers for Audit Service."""

from __future__ import annotations

import uuid

from fastapi import FastAPI, Request, status
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from sqlalchemy.exc import SQLAlchemyError
from starlette.exceptions import HTTPException as StarletteHTTPException

from app.core.logging import request_id_ctx

_HTTP_422_STATUS = getattr(status, "HTTP_422_UNPROCESSABLE_CONTENT", 422)


class AuditServiceError(Exception):
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


class UnauthorizedError(AuditServiceError):
    def __init__(self, message: str = "Authentication credentials were not provided.") -> None:
        super().__init__(
            status_code=status.HTTP_401_UNAUTHORIZED,
            error_code="UNAUTHORIZED",
            message=message,
            headers={"WWW-Authenticate": "Bearer"},
        )


class ForbiddenError(AuditServiceError):
    def __init__(self, message: str = "You do not have permission to access audit logs.") -> None:
        super().__init__(
            status_code=status.HTTP_403_FORBIDDEN,
            error_code="FORBIDDEN",
            message=message,
        )


class NotFoundError(AuditServiceError):
    def __init__(self, message: str = "Resource not found.", error_code: str = "NOT_FOUND") -> None:
        super().__init__(
            status_code=status.HTTP_404_NOT_FOUND,
            error_code=error_code,
            message=message,
        )


class UpstreamServiceUnavailableError(AuditServiceError):
    def __init__(self, message: str = "Required upstream service is temporarily unavailable.") -> None:
        super().__init__(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            error_code="SERVICE_UNAVAILABLE",
            message=message,
        )


def resolve_request_id(request: Request | None = None) -> str:
    if request is not None:
        state_id = getattr(request.state, "request_id", None)
        if isinstance(state_id, str) and state_id:
            return state_id
    ctx_id = request_id_ctx.get("-")
    return ctx_id if ctx_id and ctx_id != "-" else str(uuid.uuid4())


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
            "error": {"code": error_code, "message": message[:300]},
            "request_id": request_id,
        },
        headers=merged_headers,
    )


def register_exception_handlers(app: FastAPI) -> None:
    @app.exception_handler(AuditServiceError)
    async def handle_domain_error(request: Request, exc: AuditServiceError) -> JSONResponse:
        return build_error_response(
            status_code=exc.status_code,
            error_code=exc.error_code,
            message=exc.message,
            request_id=resolve_request_id(request),
            headers=exc.headers,
        )

    @app.exception_handler(RequestValidationError)
    async def handle_validation_error(request: Request, exc: RequestValidationError) -> JSONResponse:
        first = exc.errors()[0] if exc.errors() else None
        msg = str(first.get("msg", "Request validation failed.")) if first else "Request validation failed."
        return build_error_response(
            status_code=_HTTP_422_STATUS,
            error_code="VALIDATION_ERROR",
            message=msg,
            request_id=resolve_request_id(request),
        )

    @app.exception_handler(StarletteHTTPException)
    async def handle_http_exception(request: Request, exc: StarletteHTTPException) -> JSONResponse:
        code = "METHOD_NOT_ALLOWED" if exc.status_code == 405 else ("NOT_FOUND" if exc.status_code == 404 else "VALIDATION_ERROR")
        return build_error_response(
            status_code=exc.status_code,
            error_code=code,
            message=exc.detail if isinstance(exc.detail, str) else "Request failed.",
            request_id=resolve_request_id(request),
        )

    @app.exception_handler(SQLAlchemyError)
    async def handle_db_error(request: Request, _exc: SQLAlchemyError) -> JSONResponse:
        return build_error_response(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            error_code="INTERNAL_SERVER_ERROR",
            message="An unexpected internal server error occurred.",
            request_id=resolve_request_id(request),
        )
