"""Project creation, discovery, retrieval, updates, publishing, and cancellation business logic."""

from __future__ import annotations

import math
import uuid
from datetime import date
from typing import Literal

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings
from app.core.exceptions import (
    ForbiddenError,
    InvalidProjectStatusError,
    ProjectNotFoundError,
    ValidationError,
)
from app.core.logging import get_logger
from app.core.security import AuthenticatedUser, UserRole
from app.models.assignment import Assignment, AssignmentStatus
from app.models.base import utc_now
from app.models.project import Project, ProjectStatus
from app.repositories.assignment_repository import AssignmentRepository
from app.repositories.project_repository import ProjectRepository
from app.schemas.assignment import AssignmentPublicSummary
from app.schemas.project import (
    ProjectCreateRequest,
    ProjectListResponse,
    ProjectResponse,
    ProjectUpdateRequest,
)
from app.services.project_state_service import ProjectStateService

logger = get_logger("services.project")


class ProjectService:
    """Orchestrates project lifecycle operations with strict ownership and RBAC enforcement."""

    def __init__(self, session: AsyncSession, settings: Settings) -> None:
        self.session = session
        self.settings = settings
        self.project_repo = ProjectRepository(session)
        self.assignment_repo = AssignmentRepository(session)
        self.state_service = ProjectStateService()

    async def create_project(
        self,
        *,
        owner: AuthenticatedUser,
        payload: ProjectCreateRequest,
    ) -> ProjectResponse:
        """Create a new OPEN project and its 1-to-1 ProjectRequirement transactionally."""
        if owner.role != UserRole.OWNER:
            raise ForbiddenError("Only OWNER accounts may create projects.")

        now = utc_now()
        currency = payload.currency or self.settings.DEFAULT_CURRENCY
        project = await self.project_repo.create_project_with_requirement(
            owner_id=owner.id,
            title=payload.title,
            description=payload.description,
            expected_result=payload.expected_result,
            required_skills=payload.required_skills,
            budget=payload.budget,
            currency=currency,
            deadline=payload.deadline,
            now=now,
        )
        await self.session.commit()
        created = await self.project_repo.get_by_id(project.id, include_relations=True)
        assert created is not None

        logger.info(
            "Project created",
            extra={
                "event": "project_created",
                "user_id": str(owner.id),
                "role": owner.role.value,
                "project_id": str(created.id),
            },
        )
        return self._to_project_response(created, viewer=owner)

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
    ) -> ProjectListResponse:
        """List projects filtered by caller's role visibility and query criteria."""
        if deadline_after and deadline_before and deadline_after > deadline_before:
            raise ValidationError("deadline_after cannot be later than deadline_before.")

        projects, total = await self.project_repo.list_projects(
            viewer=viewer,
            page=page,
            page_size=page_size,
            search=search,
            status=status,
            skill=skill,
            deadline_before=deadline_before,
            deadline_after=deadline_after,
            sort_by=sort_by,
            sort_order=sort_order,
        )
        total_pages = math.ceil(total / page_size) if total > 0 else 0
        items = [self._to_project_response(p, viewer=viewer) for p in projects]
        return ProjectListResponse(
            items=items,
            total=total,
            page=page,
            page_size=page_size,
            total_pages=total_pages,
        )

    async def get_project(
        self,
        *,
        project_id: uuid.UUID,
        viewer: AuthenticatedUser,
    ) -> ProjectResponse:
        """Retrieve a single project after enforcing role and ownership visibility checks."""
        project = await self.project_repo.get_by_id(project_id, include_relations=True)
        if project is None:
            raise ProjectNotFoundError()

        self._enforce_read_access(project=project, viewer=viewer)
        return self._to_project_response(project, viewer=viewer)

    async def update_project(
        self,
        *,
        project_id: uuid.UUID,
        owner: AuthenticatedUser,
        payload: ProjectUpdateRequest,
    ) -> ProjectResponse:
        """Update permitted mutable fields on an unassigned OPEN project owned by caller."""
        if owner.role != UserRole.OWNER:
            raise ForbiddenError("Only OWNER accounts may update projects.")

        project = await self.project_repo.get_by_id(project_id, include_relations=True)
        if project is None:
            raise ProjectNotFoundError()

        if project.owner_id != owner.id:
            raise ForbiddenError("Only the project owner may update this project.")

        active_assignment = self._find_active_assignment(project)
        if project.status != ProjectStatus.OPEN or active_assignment is not None:
            raise InvalidProjectStatusError(
                f"Cannot modify a project in {project.status.value} status."
            )

        now = utc_now()
        if payload.title is not None:
            project.title = payload.title
        if payload.description is not None:
            project.description = payload.description
        if payload.budget is not None:
            project.budget = payload.budget
        if payload.currency is not None:
            project.currency = payload.currency
        if payload.deadline is not None:
            project.deadline = payload.deadline
        project.updated_at = now

        if project.requirement is not None:
            if payload.expected_result is not None:
                project.requirement.expected_result = payload.expected_result
            if payload.required_skills is not None:
                project.requirement.required_skills = payload.required_skills
            project.requirement.updated_at = now

        await self.session.commit()
        updated = await self.project_repo.get_by_id(project_id, include_relations=True)
        assert updated is not None
        return self._to_project_response(updated, viewer=owner)

    async def publish_project(
        self,
        *,
        project_id: uuid.UUID,
        owner: AuthenticatedUser,
    ) -> ProjectResponse:
        """Publish an eligible OPEN project owned by the caller."""
        if owner.role != UserRole.OWNER:
            raise ForbiddenError("Only OWNER accounts may publish projects.")

        project = await self.project_repo.get_by_id(project_id, include_relations=True)
        if project is None:
            raise ProjectNotFoundError()

        if project.owner_id != owner.id:
            raise ForbiddenError("Only the project owner may publish this project.")

        self.state_service.validate_publish(project)

        now = utc_now()
        project.status = ProjectStatus.OPEN
        project.published_at = now
        project.updated_at = now
        await self.session.commit()

        published = await self.project_repo.get_by_id(project_id, include_relations=True)
        assert published is not None
        logger.info(
            "Project published",
            extra={
                "event": "project_published",
                "user_id": str(owner.id),
                "role": owner.role.value,
                "project_id": str(published.id),
            },
        )
        return self._to_project_response(published, viewer=owner)

    async def cancel_project(
        self,
        *,
        project_id: uuid.UUID,
        owner: AuthenticatedUser,
    ) -> ProjectResponse:
        """Cancel an eligible unassigned OPEN project owned by the caller."""
        if owner.role != UserRole.OWNER:
            raise ForbiddenError("Only OWNER accounts may cancel projects.")

        project = await self.project_repo.get_by_id(project_id, include_relations=True)
        if project is None:
            raise ProjectNotFoundError()

        if project.owner_id != owner.id:
            raise ForbiddenError("Only the project owner may cancel this project.")

        active_assignment = await self.assignment_repo.get_active_by_project_id(project.id)
        self.state_service.validate_cancel(
            project,
            has_active_assignment=active_assignment is not None,
        )

        now = utc_now()
        project.status = ProjectStatus.CANCELLED
        project.closed_at = now
        project.updated_at = now
        await self.session.commit()

        cancelled = await self.project_repo.get_by_id(project_id, include_relations=True)
        assert cancelled is not None
        logger.info(
            "Project cancelled",
            extra={
                "event": "project_cancelled",
                "user_id": str(owner.id),
                "role": owner.role.value,
                "project_id": str(cancelled.id),
            },
        )
        return self._to_project_response(cancelled, viewer=owner)

    async def start_work(
        self,
        *,
        project_id: uuid.UUID,
        actor: AuthenticatedUser,
    ) -> ProjectResponse:
        """Transition ASSIGNED or CHANGES_REQUESTED project to IN_PROGRESS."""
        project = await self.project_repo.get_by_id(project_id, include_relations=True)
        if project is None:
            raise ProjectNotFoundError()

        active_assignment = self._find_active_assignment(project)
        is_owner = actor.role == UserRole.OWNER and project.owner_id == actor.id
        is_assigned = (
            actor.role == UserRole.FREELANCER
            and active_assignment is not None
            and active_assignment.freelancer_id == actor.id
        )
        if not (is_owner or is_assigned):
            raise ForbiddenError(
                "Only the project owner or actively assigned freelancer may start work."
            )

        self.state_service.advance_to_in_progress(project)
        await self.session.commit()
        updated = await self.project_repo.get_by_id(project_id, include_relations=True)
        assert updated is not None
        logger.info(
            "Project moved to IN_PROGRESS",
            extra={
                "event": "project_started",
                "user_id": str(actor.id),
                "role": actor.role.value,
                "project_id": str(updated.id),
            },
        )
        return self._to_project_response(updated, viewer=actor)

    async def request_changes(
        self,
        *,
        project_id: uuid.UUID,
        owner: AuthenticatedUser,
    ) -> ProjectResponse:
        """Owner transitions IN_PROGRESS project to CHANGES_REQUESTED."""
        if owner.role != UserRole.OWNER:
            raise ForbiddenError("Only OWNER accounts may request project changes.")
        project = await self.project_repo.get_by_id(project_id, include_relations=True)
        if project is None:
            raise ProjectNotFoundError()
        if project.owner_id != owner.id:
            raise ForbiddenError("Only the project owner may request project changes.")

        self.state_service.request_changes(project)
        await self.session.commit()
        updated = await self.project_repo.get_by_id(project_id, include_relations=True)
        assert updated is not None
        return self._to_project_response(updated, viewer=owner)

    async def complete_project(
        self,
        *,
        project_id: uuid.UUID,
        owner: AuthenticatedUser,
        approval_verified: bool,
    ) -> ProjectResponse:
        """Owner completes an IN_PROGRESS project after verified review approval."""
        if owner.role != UserRole.OWNER:
            raise ForbiddenError("Only OWNER accounts may complete projects.")
        project = await self.project_repo.get_by_id(project_id, include_relations=True)
        if project is None:
            raise ProjectNotFoundError()
        if project.owner_id != owner.id:
            raise ForbiddenError("Only the project owner may complete this project.")

        now = utc_now()
        self.state_service.complete_project(project, approval_verified=approval_verified, now=now)
        active_assignment = self._find_active_assignment(project)
        if active_assignment is not None:
            active_assignment.status = AssignmentStatus.COMPLETED
            active_assignment.updated_at = now
        await self.session.commit()
        updated = await self.project_repo.get_by_id(project_id, include_relations=True)
        assert updated is not None
        logger.info(
            "Project marked COMPLETED",
            extra={
                "event": "project_completed",
                "user_id": str(owner.id),
                "role": owner.role.value,
                "project_id": str(updated.id),
            },
        )
        return self._to_project_response(updated, viewer=owner)

    @classmethod
    def _enforce_read_access(cls, *, project: Project, viewer: AuthenticatedUser) -> None:
        if viewer.role == UserRole.ADMIN:
            return
        if viewer.role == UserRole.OWNER:
            if project.owner_id != viewer.id:
                raise ForbiddenError("You do not have permission to view this owner's project.")
            return
        if viewer.role == UserRole.FREELANCER:
            if project.status == ProjectStatus.OPEN:
                return
            if cls._is_freelancer_assigned(project=project, freelancer_id=viewer.id):
                return
            raise ForbiddenError("You do not have permission to view this project.")
        raise ForbiddenError()

    @staticmethod
    def _find_active_assignment(project: Project) -> Assignment | None:
        for assignment in project.assignments or []:
            if assignment.status == AssignmentStatus.ACTIVE:
                return assignment
        return None

    @staticmethod
    def _is_freelancer_assigned(*, project: Project, freelancer_id: uuid.UUID) -> bool:
        for assignment in project.assignments or []:
            if (
                assignment.freelancer_id == freelancer_id
                and assignment.status in {AssignmentStatus.ACTIVE, AssignmentStatus.COMPLETED}
            ):
                return True
        return False

    @classmethod
    def _to_project_response(
        cls,
        project: Project,
        *,
        viewer: AuthenticatedUser,
    ) -> ProjectResponse:
        req = project.requirement
        active = cls._find_active_assignment(project)
        assignment_summary: AssignmentPublicSummary | None = None
        if active is not None and (
            viewer.role == UserRole.ADMIN
            or project.owner_id == viewer.id
            or active.freelancer_id == viewer.id
        ):
            assignment_summary = AssignmentPublicSummary.model_validate(active)

        return ProjectResponse(
            id=project.id,
            owner_id=project.owner_id,
            title=project.title,
            description=project.description,
            expected_result=req.expected_result if req is not None else "",
            required_skills=list(req.required_skills or []) if req is not None else [],
            budget=project.budget,
            currency=project.currency,
            deadline=project.deadline,
            status=project.status,
            created_at=project.created_at,
            updated_at=project.updated_at,
            published_at=project.published_at,
            closed_at=project.closed_at,
            active_assignment=assignment_summary,
        )

