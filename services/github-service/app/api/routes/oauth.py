"""GitHub OAuth App authorization initiation, callback, and connection disconnection routes."""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, Query, Request, status
from fastapi.responses import JSONResponse, RedirectResponse
from sqlalchemy.ext.asyncio import AsyncSession

from app.clients.github_client import GitHubHTTPClient
from app.clients.project_client import ProjectClient
from app.core.config import Settings
from app.core.database import get_db_session
from app.core.dependencies import (
    AuthContext,
    get_encryption_service,
    get_github_client,
    get_optional_auth_context,
    get_project_client,
    get_settings,
    require_owner,
)
from app.core.encryption import TokenEncryptionService
from app.core.security import get_request_id
from app.schemas.common import ErrorResponse
from app.schemas.oauth import GitHubConnectionDisconnectResponse, OAuthCallbackResponse
from app.services.oauth_service import GitHubOAuthService

router = APIRouter(prefix="/github", tags=["GitHub OAuth"])


@router.get(
    "/oauth/authorize",
    status_code=status.HTTP_302_FOUND,
    summary="Initiate GitHub OAuth authorization redirect (OWNER only)",
    description=(
        "Verifies caller is an active OWNER who owns the target CodeBridge project, persists "
        "a cryptographic single-use state hash bound to owner and project, and redirects to GitHub."
    ),
    responses={
        302: {"description": "Redirect to fixed https://github.com/login/oauth/authorize."},
        401: {"model": ErrorResponse, "description": "Unauthorized."},
        403: {"model": ErrorResponse, "description": "Forbidden (OWNER & ownership required)."},
        404: {"model": ErrorResponse, "description": "Project not found."},
        503: {"model": ErrorResponse, "description": "Identity or Project Service unavailable."},
    },
)
async def initiate_github_oauth(
    request: Request,
    project_id: uuid.UUID = Query(..., description="CodeBridge project UUID being connected."),
    auth_ctx: AuthContext = Depends(require_owner),
    session: AsyncSession = Depends(get_db_session),
    settings: Settings = Depends(get_settings),
    encryption_service: TokenEncryptionService = Depends(get_encryption_service),
    project_client: ProjectClient = Depends(get_project_client),
    github_client: GitHubHTTPClient = Depends(get_github_client),
) -> RedirectResponse:
    service = GitHubOAuthService(
        session=session,
        settings=settings,
        encryption_service=encryption_service,
        project_client=project_client,
        github_client=github_client,
    )
    authorize_url = await service.initiate_oauth(
        project_id=project_id,
        auth_ctx=auth_ctx,
        request_id=get_request_id(request),
    )
    return RedirectResponse(
        url=authorize_url,
        status_code=status.HTTP_302_FOUND,
        headers={"Cache-Control": "no-store"},
    )


@router.get(
    "/oauth/callback",
    response_model=None,
    summary="Handle GitHub OAuth callback and persist encrypted owner token",
    description=(
        "Validates single-use cryptographic state bound to owner and project, exchanges the "
        "authorization code with GitHub, encrypts the token with AES-256-GCM at rest, and "
        "returns safe metadata or redirects to FRONTEND_OAUTH_SUCCESS_URL without secrets."
    ),
    responses={
        200: {"model": OAuthCallbackResponse, "description": "GitHub OAuth connection saved."},
        302: {"description": "Optional non-secret frontend redirect when configured."},
        400: {"model": ErrorResponse, "description": "Invalid, tampered, denied, or expired OAuth state/callback."},
        403: {"model": ErrorResponse, "description": "Authenticated caller does not own OAuth state."},
        409: {"model": ErrorResponse, "description": "OAuth state already used or GitHub account conflict."},
        502: {"model": ErrorResponse, "description": "Upstream GitHub OAuth or API failure."},
        504: {"model": ErrorResponse, "description": "Upstream GitHub timeout."},
    },
)
async def handle_github_oauth_callback(
    request: Request,
    code: str | None = Query(default=None),
    state: str | None = Query(default=None),
    error: str | None = Query(default=None),
    optional_auth_ctx: AuthContext | None = Depends(get_optional_auth_context),
    session: AsyncSession = Depends(get_db_session),
    settings: Settings = Depends(get_settings),
    encryption_service: TokenEncryptionService = Depends(get_encryption_service),
    project_client: ProjectClient = Depends(get_project_client),
    github_client: GitHubHTTPClient = Depends(get_github_client),
) -> JSONResponse | RedirectResponse:
    service = GitHubOAuthService(
        session=session,
        settings=settings,
        encryption_service=encryption_service,
        project_client=project_client,
        github_client=github_client,
    )
    payload, redirect_url = await service.handle_callback(
        code=code,
        state=state,
        oauth_error=error,
        optional_auth_ctx=optional_auth_ctx,
        query_keys=set(request.query_params.keys()),
    )
    if redirect_url is not None:
        return RedirectResponse(
            url=redirect_url,
            status_code=status.HTTP_302_FOUND,
            headers={"Cache-Control": "no-store"},
        )
    return JSONResponse(
        status_code=status.HTTP_200_OK,
        content=payload.model_dump(mode="json"),
        headers={"Cache-Control": "no-store"},
    )


@router.delete(
    "/connection",
    response_model=GitHubConnectionDisconnectResponse,
    summary="Disconnect owner GitHub OAuth connection and unlink inactive projects (OWNER only)",
    description=(
        "Disconnects the owner's active GitHub OAuth connection, wipes encrypted token material, "
        "and disconnects linked project repositories unless any linked project has an active workflow."
    ),
    responses={
        401: {"model": ErrorResponse, "description": "Unauthorized."},
        403: {"model": ErrorResponse, "description": "Forbidden (OWNER role required)."},
        404: {"model": ErrorResponse, "description": "No active GitHub connection found."},
        409: {"model": ErrorResponse, "description": "Active project assignment prevents disconnect."},
        503: {"model": ErrorResponse, "description": "Identity or Project Service unavailable."},
    },
)
async def disconnect_github_account(
    request: Request,
    auth_ctx: AuthContext = Depends(require_owner),
    session: AsyncSession = Depends(get_db_session),
    settings: Settings = Depends(get_settings),
    encryption_service: TokenEncryptionService = Depends(get_encryption_service),
    project_client: ProjectClient = Depends(get_project_client),
    github_client: GitHubHTTPClient = Depends(get_github_client),
) -> GitHubConnectionDisconnectResponse:
    service = GitHubOAuthService(
        session=session,
        settings=settings,
        encryption_service=encryption_service,
        project_client=project_client,
        github_client=github_client,
    )
    return await service.disconnect_account(
        auth_ctx=auth_ctx,
        request_id=get_request_id(request),
    )

