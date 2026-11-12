"""Project CRUD, discovery, publishing, and cancellation endpoints under /api/v1/projects."""

from __future__ import annotations

import uuid
from datetime import date
from typing import Literal

from fastapi import APIRouter, Depends, Query, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings
from app.core.database import get_db_session
from app.core.dependencies import (
    get_settings,
    require_any_authenticated,
    require_owner,
    require_owner_or_freelancer,
)
from app.core.security import AuthenticatedUser
from app.models.project import ProjectStatus
from app.schemas.common import ErrorResponse
from app.schemas.project import (
    ProjectCreateRequest,
    ProjectListResponse,
    ProjectResponse,
    ProjectUpdateRequest,
    WorkflowTransitionRequest,
)
from app.services.project_service import ProjectService

router = APIRouter(prefix="/projects", tags=["Projects"])


@router.post(
    "",
    response_model=ProjectResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Create a new project (OWNER only)",
    description=(
        "Creates a new OPEN project and its 1-to-1 ProjectRequirement record in a single "
        "transaction. Derive owner_id strictly from authenticated Identity context. "
        "Authentication: Bearer access JWT. Role required: OWNER."
    ),
    responses={
        401: {"model": ErrorResponse, "description": "Missing/invalid/expired credentials."},
        403: {"model": ErrorResponse, "description": "Forbidden (OWNER role required)."},
        422: {"model": ErrorResponse, "description": "Validation error."},
        503: {"model": ErrorResponse, "description": "Identity Service unavailable."},
    },
)
async def create_project(
    payload: ProjectCreateRequest,
    current_owner: AuthenticatedUser = Depends(require_owner),
    session: AsyncSession = Depends(get_db_session),
    settings: Settings = Depends(get_settings),
) -> ProjectResponse:
    service = ProjectService(session, settings)
    return await service.create_project(owner=current_owner, payload=payload)


@router.get(
    "",
    response_model=ProjectListResponse,
    status_code=status.HTTP_200_OK,
    summary="List projects scoped to caller's role",
    description=(
        "OWNER views own projects; FREELANCER discovers OPEN projects and projects assigned "
        "to them; ADMIN views all projects. Supports pagination, search, skill, status, "
        "deadline range, and sorting. Authentication: Bearer access JWT."
    ),
    responses={
        401: {"model": ErrorResponse, "description": "Unauthorized."},
        403: {"model": ErrorResponse, "description": "Account inactive or forbidden."},
        422: {"model": ErrorResponse, "description": "Invalid query filters."},
        503: {"model": ErrorResponse, "description": "Identity Service unavailable."},
    },
)
async def list_projects(
    page: int = Query(1, ge=1, description="Page number (1-indexed)."),
    page_size: int = Query(20, ge=1, le=100, description="Items per page (max 100)."),
    search: str | None = Query(None, max_length=100, description="Search title & description."),
    project_status: ProjectStatus | None = Query(
        None,
        alias="status",
        description="Filter by project status.",
    ),
    skill: str | None = Query(None, max_length=50, description="Filter by exact required skill."),
    deadline_before: date | None = Query(None, description="Maximum deadline (inclusive)."),
    deadline_after: date | None = Query(None, description="Minimum deadline (inclusive)."),
    sort_by: Literal["created_at", "deadline"] = Query("created_at", description="Sort column."),
    sort_order: Literal["asc", "desc"] = Query("desc", description="Sort direction."),
    current_user: AuthenticatedUser = Depends(require_any_authenticated),
    session: AsyncSession = Depends(get_db_session),
    settings: Settings = Depends(get_settings),
) -> ProjectListResponse:
    service = ProjectService(session, settings)
    return await service.list_projects(
        viewer=current_user,
        page=page,
        page_size=page_size,
        search=search,
        status=project_status,
        skill=skill,
        deadline_before=deadline_before,
        deadline_after=deadline_after,
        sort_by=sort_by,
        sort_order=sort_order,
    )


@router.get(
    "/{project_id}",
    response_model=ProjectResponse,
    status_code=status.HTTP_200_OK,
    summary="Retrieve project details",
    description=(
        "OWNER sees own project; FREELANCER sees OPEN projects or projects assigned to them; "
        "ADMIN sees any project. Authentication: Bearer access JWT."
    ),
    responses={
        401: {"model": ErrorResponse, "description": "Unauthorized."},
        403: {"model": ErrorResponse, "description": "Forbidden."},
        404: {"model": ErrorResponse, "description": "Project not found."},
        503: {"model": ErrorResponse, "description": "Identity Service unavailable."},
    },
)
async def get_project(
    project_id: uuid.UUID,
    current_user: AuthenticatedUser = Depends(require_any_authenticated),
    session: AsyncSession = Depends(get_db_session),
    settings: Settings = Depends(get_settings),
) -> ProjectResponse:
    service = ProjectService(session, settings)
    return await service.get_project(project_id=project_id, viewer=current_user)


@router.patch(
    "/{project_id}",
    response_model=ProjectResponse,
    status_code=status.HTTP_200_OK,
    summary="Update permitted fields on an OPEN project (OWNER only)",
    description=(
        "Allows project owner to update mutable project and requirement attributes while the "
        "project remains unassigned and OPEN. Strictly rejects id, owner_id, and status mutations. "
        "Authentication: Bearer access JWT. Role required: OWNER (must own project)."
    ),
    responses={
        401: {"model": ErrorResponse, "description": "Unauthorized."},
        403: {"model": ErrorResponse, "description": "Forbidden."},
        404: {"model": ErrorResponse, "description": "Project not found."},
        409: {"model": ErrorResponse, "description": "Project status does not allow editing."},
        422: {"model": ErrorResponse, "description": "Validation or forbidden field error."},
        503: {"model": ErrorResponse, "description": "Identity Service unavailable."},
    },
)
async def update_project(
    project_id: uuid.UUID,
    payload: ProjectUpdateRequest,
    current_owner: AuthenticatedUser = Depends(require_owner),
    session: AsyncSession = Depends(get_db_session),
    settings: Settings = Depends(get_settings),
) -> ProjectResponse:
    service = ProjectService(session, settings)
    return await service.update_project(
        project_id=project_id,
        owner=current_owner,
        payload=payload,
    )


@router.post(
    "/{project_id}/publish",
    response_model=ProjectResponse,
    status_code=status.HTTP_200_OK,
    summary="Publish an eligible OPEN project (OWNER only)",
    description=(
        "Validates ownership and project completeness, sets status OPEN and records published_at. "
        "Rejects duplicate publish attempts or non-OPEN projects. "
        "Authentication: Bearer access JWT. Role required: OWNER (must own project)."
    ),
    responses={
        401: {"model": ErrorResponse, "description": "Unauthorized."},
        403: {"model": ErrorResponse, "description": "Forbidden."},
        404: {"model": ErrorResponse, "description": "Project not found."},
        409: {"model": ErrorResponse, "description": "Project already published or invalid state."},
        503: {"model": ErrorResponse, "description": "Identity Service unavailable."},
    },
)
async def publish_project(
    project_id: uuid.UUID,
    current_owner: AuthenticatedUser = Depends(require_owner),
    session: AsyncSession = Depends(get_db_session),
    settings: Settings = Depends(get_settings),
) -> ProjectResponse:
    service = ProjectService(session, settings)
    return await service.publish_project(project_id=project_id, owner=current_owner)


@router.post(
    "/{project_id}/cancel",
    response_model=ProjectResponse,
    status_code=status.HTTP_200_OK,
    summary="Cancel an unassigned OPEN project (OWNER only)",
    description=(
        "Transitions an eligible unassigned OPEN project to CANCELLED and records closed_at. "
        "Rejects assigned or closed projects. "
        "Authentication: Bearer access JWT. Role required: OWNER (must own project)."
    ),
    responses={
        401: {"model": ErrorResponse, "description": "Unauthorized."},
        403: {"model": ErrorResponse, "description": "Forbidden."},
        404: {"model": ErrorResponse, "description": "Project not found."},
        409: {"model": ErrorResponse, "description": "Project cannot be cancelled in current state."},
        503: {"model": ErrorResponse, "description": "Identity Service unavailable."},
    },
)
async def cancel_project(
    project_id: uuid.UUID,
    current_owner: AuthenticatedUser = Depends(require_owner),
    session: AsyncSession = Depends(get_db_session),
    settings: Settings = Depends(get_settings),
) -> ProjectResponse:
    service = ProjectService(session, settings)
    return await service.cancel_project(project_id=project_id, owner=current_owner)


@router.post(
    "/{project_id}/workflow/transition",
    response_model=ProjectResponse,
    status_code=status.HTTP_200_OK,
    include_in_schema=False,
    summary="Internal workflow hook for Workspace/Review state coordination",
)
async def transition_project_workflow(
    project_id: uuid.UUID,
    payload: WorkflowTransitionRequest,
    actor: AuthenticatedUser = Depends(require_owner_or_freelancer),
    session: AsyncSession = Depends(get_db_session),
    settings: Settings = Depends(get_settings),
) -> ProjectResponse:
    service = ProjectService(session, settings)
    if payload.target_status == "IN_PROGRESS":
        return await service.start_work(project_id=project_id, actor=actor)
    if payload.target_status == "CHANGES_REQUESTED":
        return await service.request_changes(project_id=project_id, owner=actor)
    return await service.complete_project(
        project_id=project_id,
        owner=actor,
        approval_verified=payload.approval_verified,
    )

