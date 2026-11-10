"""Async SQLAlchemy repository for Project and ProjectRequirement entities."""

from __future__ import annotations

import uuid
from datetime import date, datetime
from decimal import Decimal
from typing import Literal

from sqlalchemy import exists, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.core.security import AuthenticatedUser, UserRole
from app.models.assignment import Assignment, AssignmentStatus
from app.models.project import Project, ProjectStatus
from app.models.project_requirement import ProjectRequirement


class ProjectRepository:
    """Data access layer enforcing project role-scoping, filtering, and pagination."""

    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def create_project_with_requirement(
        self,
        *,
        owner_id: uuid.UUID,
        title: str,
        description: str,
        expected_result: str,
        required_skills: list[str],
        budget: Decimal,
        currency: str,
        deadline: date,
        now: datetime,
    ) -> Project:
        project = Project(
            owner_id=owner_id,
            title=title,
            description=description,
            status=ProjectStatus.OPEN,
            budget=budget,
            currency=currency,
            deadline=deadline,
            created_at=now,
            updated_at=now,
        )
        requirement = ProjectRequirement(
            project=project,
            expected_result=expected_result,
            required_skills=required_skills,
            created_at=now,
            updated_at=now,
        )
        self.session.add(project)
        self.session.add(requirement)
        await self.session.flush()
        return project

    async def get_by_id(
        self,
        project_id: uuid.UUID,
        *,
        include_relations: bool = True,
        for_update: bool = False,
    ) -> Project | None:
        stmt = select(Project).where(Project.id == project_id)
        if include_relations:
            stmt = stmt.options(
                selectinload(Project.requirement),
                selectinload(Project.assignments),
            )
        if for_update:
            bind = self.session.get_bind()
            if bind is not None and bind.dialect.name != "sqlite":
                stmt = stmt.with_for_update()
        result = await self.session.execute(stmt)
        return result.scalar_one_or_none()

    async def list_projects(
        self,
        *,
        viewer: AuthenticatedUser,
        page: int,
        page_size: int,
        search: str | None = None,
        status: ProjectStatus | None = None,
        skill: str | None = None,
        deadline_before: date | None = None,
        deadline_after: date | None = None,
        sort_by: Literal["created_at", "deadline"] = "created_at",
        sort_order: Literal["asc", "desc"] = "desc",
    ) -> tuple[list[Project], int]:
        conditions = []

        if viewer.role == UserRole.OWNER:
            conditions.append(Project.owner_id == viewer.id)
        elif viewer.role == UserRole.FREELANCER:
            assigned_to_viewer = exists(
                select(Assignment.id).where(
                    Assignment.project_id == Project.id,
                    Assignment.freelancer_id == viewer.id,
                    Assignment.status.in_([AssignmentStatus.ACTIVE, AssignmentStatus.COMPLETED]),
                )
            )
            conditions.append(or_(Project.status == ProjectStatus.OPEN, assigned_to_viewer))
        # ADMIN is permitted to list across all projects.

        if search and search.strip():
            term = f"%{search.strip()}%"
            conditions.append(or_(Project.title.ilike(term), Project.description.ilike(term)))

        if status is not None:
            conditions.append(Project.status == status)

        if deadline_after is not None:
            conditions.append(Project.deadline >= deadline_after)

        if deadline_before is not None:
            conditions.append(Project.deadline <= deadline_before)

        sort_column = Project.deadline if sort_by == "deadline" else Project.created_at
        primary_order = sort_column.asc() if sort_order == "asc" else sort_column.desc()

        base_stmt = (
            select(Project)
            .options(
                selectinload(Project.requirement),
                selectinload(Project.assignments),
            )
            .where(*conditions)
            .order_by(primary_order, Project.id.asc())
        )

        if skill and skill.strip():
            # Case-insensitive exact skill tag matching compatible across PostgreSQL and SQLite.
            target_skill = skill.strip().lower()
            rows = list((await self.session.execute(base_stmt)).scalars().all())
            filtered = [
                project
                for project in rows
                if project.requirement is not None
                and any(
                    str(tag).strip().lower() == target_skill
                    for tag in (project.requirement.required_skills or [])
                )
            ]
            total = len(filtered)
            start = (page - 1) * page_size
            return filtered[start : start + page_size], total

        count_stmt = select(func.count()).select_from(Project).where(*conditions)
        total = int((await self.session.execute(count_stmt)).scalar_one())

        paged_stmt = base_stmt.offset((page - 1) * page_size).limit(page_size)
        rows = list((await self.session.execute(paged_stmt)).scalars().all())
        return rows, total

