"""Notification domain service with deduplication, preferences, read tracking, and mock email delivery."""

from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone

from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import ForbiddenError, NotFoundError
from app.core.ws import NotificationWebSocketManager
from app.models import EmailDeliveryLog, Notification, NotificationPreference
from app.schemas.notification import (
    NotificationEventRequest,
    NotificationListResponse,
    NotificationPreferencesResponse,
    NotificationPreferencesUpdateRequest,
    NotificationResponse,
)


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class NotificationService:
    def __init__(
        self,
        session: AsyncSession,
        ws_manager: NotificationWebSocketManager | None = None,
    ) -> None:
        self.session = session
        self.ws_manager = ws_manager

    async def _get_or_default_preferences(self, user_id: uuid.UUID) -> tuple[bool, bool, list[str], datetime | None]:
        pref = (
            await self.session.execute(
                select(NotificationPreference).where(NotificationPreference.user_id == user_id)
            )
        ).scalar_one_or_none()
        if pref is None:
            return True, True, [], None
        try:
            muted = json.loads(pref.muted_event_types_json)
            if not isinstance(muted, list):
                muted = []
        except ValueError:
            muted = []
        return pref.in_app_enabled, pref.email_enabled, [str(x) for x in muted], pref.updated_at

    async def ingest_event(self, payload: NotificationEventRequest) -> NotificationResponse:
        if payload.idempotency_key:
            existing = (
                await self.session.execute(
                    select(Notification).where(
                        Notification.user_id == payload.user_id,
                        Notification.idempotency_key == payload.idempotency_key,
                    )
                )
            ).scalar_one_or_none()
            if existing is not None:
                return NotificationResponse.model_validate(existing)

        in_app_enabled, email_enabled, muted, _ = await self._get_or_default_preferences(payload.user_id)
        is_muted = payload.event_type in muted
        now = _utcnow()

        notification = Notification(
            user_id=payload.user_id,
            event_type=payload.event_type,
            title=payload.title,
            message=payload.message,
            resource_type=payload.resource_type,
            resource_id=payload.resource_id,
            idempotency_key=payload.idempotency_key,
            is_read=not in_app_enabled or is_muted,
            read_at=now if (not in_app_enabled or is_muted) else None,
            created_at=now,
        )
        self.session.add(notification)
        await self.session.flush()

        if payload.send_email:
            delivery_status = (
                "SENT_MOCK" if (email_enabled and not is_muted) else "SKIPPED_PREFERENCE"
            )
            self.session.add(
                EmailDeliveryLog(
                    notification_id=notification.id,
                    recipient_user_id=payload.user_id,
                    subject=payload.title,
                    body_preview=payload.message[:300],
                    adapter_mode="mock",
                    delivery_status=delivery_status,
                    provider_message_id=(
                        f"mock_msg_{uuid.uuid4().hex[:12]}"
                        if delivery_status == "SENT_MOCK"
                        else None
                    ),
                    created_at=now,
                )
            )
        await self.session.commit()
        await self.session.refresh(notification)
        response = NotificationResponse.model_validate(notification)
        if self.ws_manager is not None and not notification.is_read:
            await self.ws_manager.broadcast_to_user(
                payload.user_id,
                {
                    "event": "notification.created",
                    "data": response.model_dump(mode="json"),
                },
            )
        return response

    async def list_for_user(
        self,
        *,
        user_id: uuid.UUID,
        unread_only: bool = False,
        limit: int = 50,
    ) -> NotificationListResponse:
        stmt = select(Notification).where(Notification.user_id == user_id)
        if unread_only:
            stmt = stmt.where(Notification.is_read.is_(False))
        stmt = stmt.order_by(Notification.created_at.desc()).limit(limit)
        items = list((await self.session.execute(stmt)).scalars().all())
        unread_count = int(
            await self.session.scalar(
                select(func.count())
                .select_from(Notification)
                .where(Notification.user_id == user_id, Notification.is_read.is_(False))
            )
            or 0
        )
        return NotificationListResponse(
            total=len(items),
            unread_count=unread_count,
            items=[NotificationResponse.model_validate(i) for i in items],
        )

    async def mark_read(self, *, notification_id: uuid.UUID, user_id: uuid.UUID) -> NotificationResponse:
        notif = (
            await self.session.execute(select(Notification).where(Notification.id == notification_id))
        ).scalar_one_or_none()
        if notif is None:
            raise NotFoundError()
        if notif.user_id != user_id:
            raise ForbiddenError("You can only mark your own notifications as read.")
        if not notif.is_read:
            notif.is_read = True
            notif.read_at = _utcnow()
            await self.session.commit()
            await self.session.refresh(notif)
        return NotificationResponse.model_validate(notif)

    async def mark_all_read(self, *, user_id: uuid.UUID) -> int:
        now = _utcnow()
        res = await self.session.execute(
            update(Notification)
            .where(Notification.user_id == user_id, Notification.is_read.is_(False))
            .values(is_read=True, read_at=now)
        )
        await self.session.commit()
        return int(getattr(res, "rowcount", 0) or 0)

    async def get_preferences(self, *, user_id: uuid.UUID) -> NotificationPreferencesResponse:
        in_app, email, muted, updated = await self._get_or_default_preferences(user_id)
        return NotificationPreferencesResponse(
            user_id=user_id,
            in_app_enabled=in_app,
            email_enabled=email,
            muted_event_types=muted,
            updated_at=updated,
        )

    async def update_preferences(
        self,
        *,
        user_id: uuid.UUID,
        payload: NotificationPreferencesUpdateRequest,
    ) -> NotificationPreferencesResponse:
        pref = (
            await self.session.execute(
                select(NotificationPreference).where(NotificationPreference.user_id == user_id)
            )
        ).scalar_one_or_none()
        now = _utcnow()
        muted_clean = sorted({item.strip() for item in payload.muted_event_types if item.strip()})
        if pref is None:
            pref = NotificationPreference(
                user_id=user_id,
                in_app_enabled=payload.in_app_enabled,
                email_enabled=payload.email_enabled,
                muted_event_types_json=json.dumps(muted_clean),
                updated_at=now,
            )
            self.session.add(pref)
        else:
            pref.in_app_enabled = payload.in_app_enabled
            pref.email_enabled = payload.email_enabled
            pref.muted_event_types_json = json.dumps(muted_clean)
            pref.updated_at = now
        await self.session.commit()
        return NotificationPreferencesResponse(
            user_id=user_id,
            in_app_enabled=pref.in_app_enabled,
            email_enabled=pref.email_enabled,
            muted_event_types=muted_clean,
            updated_at=pref.updated_at,
        )

