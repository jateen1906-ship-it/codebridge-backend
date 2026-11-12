"""Admin domain service coordinating moderation, user suspension, and cross-service inspection."""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.clients.service_clients import IdentityAdminClient, PlatformInspectionClient
from app.core.dependencies import AuthenticatedUser
from app.core.exceptions import ConflictError, NotFoundError
from app.models import AdminActionLog, ModerationReport
from app.schemas.admin import (
    AdminActionResponse,
    CreateReportRequest,
    ModerationReportResponse,
    ResolveReportRequest,
    UserModerationRequest,
    UserModerationResponse,
)


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class AdminService:
    def __init__(
        self,
        *,
        session: AsyncSession,
        identity_client: IdentityAdminClient,
        inspection_client: PlatformInspectionClient,
    ) -> None:
        self.session = session
        self.identity_client = identity_client
        self.inspection_client = inspection_client

    async def list_users(
        self,
        *,
        admin: AuthenticatedUser,
        page: int,
        page_size: int,
        role: str | None,
        request_id: str | None,
    ) -> dict[str, Any]:
        return await self.identity_client.list_users(
            access_token=admin.access_token,
            page=page,
            page_size=page_size,
            role=role,
            request_id=request_id,
        )

    async def suspend_user(
        self,
        *,
        target_user_id: uuid.UUID,
        payload: UserModerationRequest,
        admin: AuthenticatedUser,
        request_id: str | None,
    ) -> UserModerationResponse:
        if target_user_id == admin.id:
            raise ConflictError(
                "An administrator cannot suspend their own account.",
                error_code="CANNOT_SUSPEND_SELF",
            )

        updated_user = await self.identity_client.set_user_active_status(
            user_id=target_user_id,
            is_active=False,
            access_token=admin.access_token,
            request_id=request_id,
        )
        action = AdminActionLog(
            admin_id=admin.id,
            action_type="USER_SUSPENDED",
            target_type="USER",
            target_id=str(target_user_id),
            reason=payload.reason,
            outcome="SUCCESS",
            request_id=request_id,
            created_at=_utcnow(),
        )
        self.session.add(action)
        await self.session.commit()
        await self.session.refresh(action)

        await self.inspection_client.emit_audit(
            actor_id=admin.id,
            action="ADMIN_USER_SUSPENDED",
            resource_type="user",
            resource_id=str(target_user_id),
            metadata={"reason": payload.reason},
            request_id=request_id,
        )
        return UserModerationResponse(
            user_id=target_user_id,
            is_active=bool(updated_user.get("is_active", False)),
            action_recorded_id=action.id,
            updated_user=updated_user,
        )

    async def restore_user(
        self,
        *,
        target_user_id: uuid.UUID,
        payload: UserModerationRequest,
        admin: AuthenticatedUser,
        request_id: str | None,
    ) -> UserModerationResponse:
        updated_user = await self.identity_client.set_user_active_status(
            user_id=target_user_id,
            is_active=True,
            access_token=admin.access_token,
            request_id=request_id,
        )
        action = AdminActionLog(
            admin_id=admin.id,
            action_type="USER_RESTORED",
            target_type="USER",
            target_id=str(target_user_id),
            reason=payload.reason,
            outcome="SUCCESS",
            request_id=request_id,
            created_at=_utcnow(),
        )
        self.session.add(action)
        await self.session.commit()
        await self.session.refresh(action)

        await self.inspection_client.emit_audit(
            actor_id=admin.id,
            action="ADMIN_USER_RESTORED",
            resource_type="user",
            resource_id=str(target_user_id),
            metadata={"reason": payload.reason},
            request_id=request_id,
        )
        return UserModerationResponse(
            user_id=target_user_id,
            is_active=bool(updated_user.get("is_active", True)),
            action_recorded_id=action.id,
            updated_user=updated_user,
        )

    async def inspect_projects(
        self,
        *,
        admin: AuthenticatedUser,
        page: int,
        page_size: int,
        status_filter: str | None,
        request_id: str | None,
    ) -> dict[str, Any]:
        return await self.inspection_client.list_projects(
            access_token=admin.access_token,
            page=page,
            page_size=page_size,
            status_filter=status_filter,
            request_id=request_id,
        )

    async def inspect_audit_logs(
        self,
        *,
        admin: AuthenticatedUser,
        service_name: str | None,
        project_id: uuid.UUID | None,
        limit: int,
        request_id: str | None,
    ) -> dict[str, Any]:
        return await self.inspection_client.list_audit_logs(
            access_token=admin.access_token,
            service_name=service_name,
            project_id=project_id,
            limit=limit,
            request_id=request_id,
        )

    async def create_report(
        self,
        *,
        payload: CreateReportRequest,
        admin: AuthenticatedUser,
        request_id: str | None,
    ) -> ModerationReportResponse:
        now = _utcnow()
        report = ModerationReport(
            reporter_id=admin.id,
            target_type=payload.target_type,
            target_id=payload.target_id,
            reason=payload.reason,
            severity=payload.severity,
            status="OPEN",
            created_at=now,
            updated_at=now,
        )
        self.session.add(report)
        self.session.add(
            AdminActionLog(
                admin_id=admin.id,
                action_type="REPORT_CREATED",
                target_type=payload.target_type,
                target_id=str(payload.target_id),
                reason=payload.reason,
                outcome="SUCCESS",
                request_id=request_id,
                created_at=now,
            )
        )
        await self.session.commit()
        await self.session.refresh(report)
        return ModerationReportResponse.model_validate(report)

    async def list_reports(self, *, status_filter: str | None = None) -> list[ModerationReportResponse]:
        stmt = select(ModerationReport).order_by(ModerationReport.created_at.desc())
        if status_filter:
            stmt = stmt.where(ModerationReport.status == status_filter)
        rows = list((await self.session.execute(stmt)).scalars().all())
        return [ModerationReportResponse.model_validate(r) for r in rows]

    async def resolve_report(
        self,
        *,
        report_id: uuid.UUID,
        payload: ResolveReportRequest,
        admin: AuthenticatedUser,
        request_id: str | None,
    ) -> ModerationReportResponse:
        report = (
            await self.session.execute(select(ModerationReport).where(ModerationReport.id == report_id))
        ).scalar_one_or_none()
        if report is None:
            raise NotFoundError("Moderation report not found.", error_code="REPORT_NOT_FOUND")
        if report.status in {"RESOLVED", "DISMISSED"}:
            raise ConflictError("Moderation report is already closed.", error_code="REPORT_ALREADY_CLOSED")
        now = _utcnow()
        report.status = payload.status
        report.resolution_notes = payload.resolution_notes
        report.resolved_by = admin.id
        report.resolved_at = now
        report.updated_at = now
        self.session.add(
            AdminActionLog(
                admin_id=admin.id,
                action_type=f"REPORT_{payload.status}",
                target_type=report.target_type,
                target_id=str(report.target_id),
                reason=payload.resolution_notes,
                outcome="SUCCESS",
                request_id=request_id,
                created_at=now,
            )
        )
        await self.session.commit()
        await self.session.refresh(report)
        return ModerationReportResponse.model_validate(report)

    async def list_actions(self, *, limit: int = 100) -> list[AdminActionResponse]:
        stmt = select(AdminActionLog).order_by(AdminActionLog.created_at.desc()).limit(limit)
        rows = list((await self.session.execute(stmt)).scalars().all())
        return [AdminActionResponse.model_validate(r) for r in rows]
