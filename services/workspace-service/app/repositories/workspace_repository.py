"""Async SQLAlchemy repository for Workspace and WorkspaceEvent persistence."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.models.workspace import TERMINAL_WORKSPACE_STATUSES, Workspace, WorkspaceStatus
from app.models.workspace_event import WorkspaceEvent


class WorkspaceRepository:
    """Data access layer for Workspace entities and lifecycle events."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def get_by_id(
        self,
        workspace_id: uuid.UUID,
        *,
        include_events: bool = False,
    ) -> Workspace | None:
        stmt = select(Workspace).where(Workspace.id == workspace_id)
        if include_events:
            stmt = stmt.options(selectinload(Workspace.events))
        result = await self._session.execute(stmt)
        return result.scalar_one_or_none()

    async def get_active_by_project_id(self, project_id: uuid.UUID) -> Workspace | None:
        stmt = select(Workspace).where(
            Workspace.project_id == project_id,
            Workspace.status.notin_(tuple(TERMINAL_WORKSPACE_STATUSES)),
        )
        result = await self._session.execute(stmt)
        return result.scalar_one_or_none()

    async def count_active_by_user(self, user_id: uuid.UUID) -> int:
        stmt = select(func.count(Workspace.id)).where(
            Workspace.created_by == user_id,
            Workspace.status.notin_(tuple(TERMINAL_WORKSPACE_STATUSES)),
        )
        result = await self._session.execute(stmt)
        return int(result.scalar_one() or 0)

    async def list_by_project_id(self, project_id: uuid.UUID) -> list[Workspace]:
        stmt = (
            select(Workspace)
            .where(Workspace.project_id == project_id)
            .order_by(Workspace.created_at.desc())
        )
        return list((await self._session.execute(stmt)).scalars().all())

    async def list_expired_active(self, *, now: datetime) -> list[Workspace]:
        stmt = (
            select(Workspace)
            .where(
                Workspace.expires_at <= now,
                Workspace.status.notin_(tuple(TERMINAL_WORKSPACE_STATUSES)),
            )
            .order_by(Workspace.expires_at.asc())
        )
        return list((await self._session.execute(stmt)).scalars().all())

    async def create_workspace(
        self,
        *,
        project_id: uuid.UUID,
        owner_id: uuid.UUID,
        assigned_freelancer_id: uuid.UUID | None,
        created_by: uuid.UUID,
        repository_full_name: str,
        repository_ref: str,
        container_name: str,
        workspace_path: str,
        runtime_image: str,
        cpu_limit: str,
        memory_limit_mb: int,
        pids_limit: int,
        network_disabled: bool,
        expires_at: datetime,
        now: datetime,
    ) -> Workspace:
        ws = Workspace(
            project_id=project_id,
            owner_id=owner_id,
            assigned_freelancer_id=assigned_freelancer_id,
            created_by=created_by,
            repository_full_name=repository_full_name,
            repository_ref=repository_ref,
            status=WorkspaceStatus.PROVISIONING,
            container_name=container_name,
            workspace_path=workspace_path,
            runtime_image=runtime_image,
            cpu_limit=cpu_limit,
            memory_limit_mb=memory_limit_mb,
            pids_limit=pids_limit,
            network_disabled=network_disabled,
            expires_at=expires_at,
            created_at=now,
            updated_at=now,
        )
        self._session.add(ws)
        await self._session.flush()
        return ws

    async def add_event(
        self,
        *,
        workspace_id: uuid.UUID,
        actor_id: uuid.UUID,
        event_type: str,
        previous_status: str | None,
        new_status: str,
        details: dict[str, Any] | None = None,
        now: datetime,
    ) -> WorkspaceEvent:
        evt = WorkspaceEvent(
            workspace_id=workspace_id,
            actor_id=actor_id,
            event_type=event_type,
            previous_status=previous_status,
            new_status=new_status,
            details=details or {},
            created_at=now,
        )
        self._session.add(evt)
        await self._session.flush()
        return evt

