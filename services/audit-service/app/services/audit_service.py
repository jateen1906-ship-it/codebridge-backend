"""Append-only sanitized audit trail service."""

from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.clients.service_clients import ProjectClient
from app.core.dependencies import AuthenticatedUser
from app.core.exceptions import ForbiddenError
from app.core.logging import scrub_metadata
from app.models import AuditLog
from app.schemas.audit import AuditEventCreateRequest, AuditLogListResponse, AuditLogResponse


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _to_response(entry: AuditLog) -> AuditLogResponse:
    try:
        meta = json.loads(entry.metadata_json)
        if not isinstance(meta, dict):
            meta = {}
    except ValueError:
        meta = {}
    return AuditLogResponse(
        id=entry.id,
        service_name=entry.service_name,
        actor_id=entry.actor_id,
        actor_role=entry.actor_role,
        action=entry.action,
        resource_type=entry.resource_type,
        resource_id=entry.resource_id,
        project_id=entry.project_id,
        outcome=entry.outcome,
        ip_address=entry.ip_address,
        request_id=entry.request_id,
        metadata=meta,
        created_at=entry.created_at,
    )


class AuditService:
    def __init__(self, *, session: AsyncSession, project_client: ProjectClient) -> None:
        self.session = session
        self.project_client = project_client

    async def record_event(
        self,
        *,
        payload: AuditEventCreateRequest,
        fallback_request_id: str | None,
    ) -> AuditLogResponse:
        sanitized_meta = scrub_metadata(payload.metadata)
        entry = AuditLog(
            service_name=payload.service_name,
            actor_id=payload.actor_id,
            actor_role=payload.actor_role,
            action=payload.action,
            resource_type=payload.resource_type,
            resource_id=payload.resource_id,
            project_id=payload.project_id,
            outcome=payload.outcome,
            ip_address=payload.ip_address,
            request_id=payload.request_id or fallback_request_id,
            metadata_json=json.dumps(sanitized_meta, default=str),
            created_at=_utcnow(),
        )
        self.session.add(entry)
        await self.session.commit()
        await self.session.refresh(entry)
        return _to_response(entry)

    async def query_events(
        self,
        *,
        user: AuthenticatedUser,
        service_name: str | None = None,
        actor_id: uuid.UUID | None = None,
        project_id: uuid.UUID | None = None,
        action: str | None = None,
        outcome: str | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> AuditLogListResponse:
        stmt = select(AuditLog)
        count_stmt = select(func.count()).select_from(AuditLog)

        if user.role != "ADMIN":
            stmt = stmt.where(AuditLog.actor_id == user.id)
            count_stmt = count_stmt.where(AuditLog.actor_id == user.id)
        elif actor_id is not None:
            stmt = stmt.where(AuditLog.actor_id == actor_id)
            count_stmt = count_stmt.where(AuditLog.actor_id == actor_id)

        if service_name:
            stmt = stmt.where(AuditLog.service_name == service_name)
            count_stmt = count_stmt.where(AuditLog.service_name == service_name)
        if project_id is not None:
            stmt = stmt.where(AuditLog.project_id == project_id)
            count_stmt = count_stmt.where(AuditLog.project_id == project_id)
        if action:
            stmt = stmt.where(AuditLog.action == action)
            count_stmt = count_stmt.where(AuditLog.action == action)
        if outcome:
            stmt = stmt.where(AuditLog.outcome == outcome)
            count_stmt = count_stmt.where(AuditLog.outcome == outcome)

        total = int((await self.session.scalar(count_stmt)) or 0)
        rows = list(
            (
                await self.session.execute(
                    stmt.order_by(AuditLog.created_at.desc()).offset(offset).limit(limit)
                )
            )
            .scalars()
            .all()
        )
        return AuditLogListResponse(total=total, items=[_to_response(r) for r in rows])

    async def query_project_events(
        self,
        *,
        project_id: uuid.UUID,
        user: AuthenticatedUser,
        limit: int = 50,
        request_id: str | None = None,
    ) -> AuditLogListResponse:
        if user.role != "ADMIN":
            proj = await self.project_client.get_project_access(
                project_id=project_id,
                access_token=user.access_token,
                request_id=request_id,
            )
            if proj.owner_id != user.id and proj.assigned_freelancer_id != user.id:
                raise ForbiddenError("Only project members or admins may inspect project audit logs.")

        stmt = (
            select(AuditLog)
            .where(AuditLog.project_id == project_id)
            .order_by(AuditLog.created_at.desc())
            .limit(limit)
        )
        rows = list((await self.session.execute(stmt)).scalars().all())
        total = int(
            await self.session.scalar(
                select(func.count()).select_from(AuditLog).where(AuditLog.project_id == project_id)
            )
            or 0
        )
        return AuditLogListResponse(total=total, items=[_to_response(r) for r in rows])
