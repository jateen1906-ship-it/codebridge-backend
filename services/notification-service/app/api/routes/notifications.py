"""Notification endpoints mounted under /api/v1/notifications."""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, Query, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db_session
from app.core.dependencies import (
    AuthenticatedUser,
    get_current_user,
    require_internal_or_admin,
)
from app.schemas.notification import (
    NotificationEventRequest,
    NotificationListResponse,
    NotificationPreferencesResponse,
    NotificationPreferencesUpdateRequest,
    NotificationResponse,
    ReadAllResponse,
)
from app.services.notification_service import NotificationService

router = APIRouter(prefix="/notifications", tags=["Notifications"])


@router.post(
    "/events",
    response_model=NotificationResponse,
    status_code=status.HTTP_201_CREATED,
)
async def publish_notification_event(
    payload: NotificationEventRequest,
    _caller: str = Depends(require_internal_or_admin),
    session: AsyncSession = Depends(get_db_session),
) -> NotificationResponse:
    return await NotificationService(session).ingest_event(payload)


@router.get("", response_model=NotificationListResponse)
async def list_my_notifications(
    unread_only: bool = Query(default=False),
    limit: int = Query(default=50, ge=1, le=200),
    user: AuthenticatedUser = Depends(get_current_user),
    session: AsyncSession = Depends(get_db_session),
) -> NotificationListResponse:
    return await NotificationService(session).list_for_user(
        user_id=user.id,
        unread_only=unread_only,
        limit=limit,
    )


@router.post("/{notification_id}/read", response_model=NotificationResponse)
async def mark_notification_read(
    notification_id: uuid.UUID,
    user: AuthenticatedUser = Depends(get_current_user),
    session: AsyncSession = Depends(get_db_session),
) -> NotificationResponse:
    return await NotificationService(session).mark_read(
        notification_id=notification_id,
        user_id=user.id,
    )


@router.post("/read-all", response_model=ReadAllResponse)
async def mark_all_notifications_read(
    user: AuthenticatedUser = Depends(get_current_user),
    session: AsyncSession = Depends(get_db_session),
) -> ReadAllResponse:
    count = await NotificationService(session).mark_all_read(user_id=user.id)
    return ReadAllResponse(marked_count=count)


@router.get("/preferences", response_model=NotificationPreferencesResponse)
async def get_my_preferences(
    user: AuthenticatedUser = Depends(get_current_user),
    session: AsyncSession = Depends(get_db_session),
) -> NotificationPreferencesResponse:
    return await NotificationService(session).get_preferences(user_id=user.id)


@router.put("/preferences", response_model=NotificationPreferencesResponse)
async def update_my_preferences(
    payload: NotificationPreferencesUpdateRequest,
    user: AuthenticatedUser = Depends(get_current_user),
    session: AsyncSession = Depends(get_db_session),
) -> NotificationPreferencesResponse:
    return await NotificationService(session).update_preferences(
        user_id=user.id,
        payload=payload,
    )
