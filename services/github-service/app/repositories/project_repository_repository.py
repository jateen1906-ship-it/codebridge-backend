"""Repository helpers for project-to-GitHub-repository associations."""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import ConflictError
from app.models.project_repository import ProjectRepository, ProjectRepositoryStatus


class ProjectRepositoryRepository:
    """Data-access layer for project-scoped GitHub repository links."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def get_active_by_project_id(self, project_id: uuid.UUID) -> ProjectRepository | None:
        """Return the single ACTIVE repository associated with a CodeBridge project, if any."""
        stmt = select(ProjectRepository).where(
            ProjectRepository.project_id == project_id,
            ProjectRepository.connection_status == ProjectRepositoryStatus.ACTIVE,
        )
        result = await self._session.execute(stmt)
        return result.scalar_one_or_none()

    async def list_active_by_connection_id(
        self,
        connection_id: uuid.UUID,
    ) -> list[ProjectRepository]:
        """List all ACTIVE project repository links associated with an owner GitHub connection."""
        stmt = (
            select(ProjectRepository)
            .where(
                ProjectRepository.github_connection_id == connection_id,
                ProjectRepository.connection_status == ProjectRepositoryStatus.ACTIVE,
            )
            .order_by(ProjectRepository.connected_at.desc())
        )
        result = await self._session.execute(stmt)
        return list(result.scalars().all())

    async def create(
        self,
        *,
        project_id: uuid.UUID,
        owner_id: uuid.UUID,
        github_connection_id: uuid.UUID,
        github_repository_id: int,
        full_name: str,
        repository_name: str,
        owner_login: str,
        is_private: bool,
        default_branch: str,
        html_url: str,
        description: str | None,
        is_archived: bool,
        is_fork: bool,
        now: datetime,
    ) -> ProjectRepository:
        """Persist a new ACTIVE project-to-repository association."""
        record = ProjectRepository(
            project_id=project_id,
            owner_id=owner_id,
            github_connection_id=github_connection_id,
            github_repository_id=github_repository_id,
            full_name=full_name,
            repository_name=repository_name,
            owner_login=owner_login,
            is_private=is_private,
            default_branch=default_branch,
            html_url=html_url,
            description=description,
            is_archived=is_archived,
            is_fork=is_fork,
            connection_status=ProjectRepositoryStatus.ACTIVE,
            connected_at=now,
            updated_at=now,
        )
        self._session.add(record)
        try:
            await self._session.flush()
        except IntegrityError as exc:
            await self._session.rollback()
            raise ConflictError(
                "This project already has an active connected repository.",
                error_code="PROJECT_ALREADY_CONNECTED",
            ) from exc
        return record

    async def disconnect_project(
        self,
        record: ProjectRepository,
        *,
        now: datetime,
    ) -> ProjectRepository:
        """Mark a project repository link DISCONNECTED while keeping audit history."""
        record.connection_status = ProjectRepositoryStatus.DISCONNECTED
        record.disconnected_at = now
        record.updated_at = now
        await self._session.flush()
        return record

    async def disconnect_all_for_connection(
        self,
        connection_id: uuid.UUID,
        *,
        now: datetime,
    ) -> int:
        """Disconnect every ACTIVE project repository link tied to a GitHub connection."""
        stmt = (
            update(ProjectRepository)
            .where(
                ProjectRepository.github_connection_id == connection_id,
                ProjectRepository.connection_status == ProjectRepositoryStatus.ACTIVE,
            )
            .values(
                connection_status=ProjectRepositoryStatus.DISCONNECTED,
                disconnected_at=now,
                updated_at=now,
            )
            .execution_options(synchronize_session=False)
        )
        result = await self._session.execute(stmt)
        return int(result.rowcount or 0)
