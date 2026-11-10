"""Freelancer selection and transactional project assignment service."""

from __future__ import annotations

import uuid

from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.clients.identity_client import IdentityClient
from app.core.exceptions import (
    ConcurrentAssignmentConflictError,
    ForbiddenError,
    InvalidFreelancerError,
    ProjectNotFoundError,
)
from app.core.logging import get_logger
from app.core.security import AuthenticatedUser, UserRole
from app.models.base import utc_now
from app.models.project import ProjectStatus
from app.repositories.assignment_repository import AssignmentRepository
from app.repositories.project_repository import ProjectRepository
from app.schemas.assignment import AssignmentResponse
from app.services.project_state_service import ProjectStateService

logger = get_logger("services.assignment")


class AssignmentService:
    """Handles verified freelancer assignment with transaction and DB-level concurrency safety."""

    def __init__(self, session: AsyncSession, identity_client: IdentityClient) -> None:
        self.session = session
        self.identity_client = identity_client
        self.project_repo = ProjectRepository(session)
        self.assignment_repo = AssignmentRepository(session)
        self.state_service = ProjectStateService()

    async def select_freelancer(
        self,
        *,
        project_id: uuid.UUID,
        owner: AuthenticatedUser,
        freelancer_id: uuid.UUID,
        request_id: str,
    ) -> AssignmentResponse:
        """Select and assign a verified Identity freelancer to an OPEN project transactionally."""
        if owner.role != UserRole.OWNER:
            raise ForbiddenError("Only OWNER accounts may assign freelancers.")

        if freelancer_id == owner.id:
            raise InvalidFreelancerError("Project owner cannot assign themselves as freelancer.")

        # Pre-flight validation before external HTTP call (avoids holding DB row locks across I/O).
        pre_project = await self.project_repo.get_by_id(project_id, include_relations=False)
        if pre_project is None:
            raise ProjectNotFoundError()
        if pre_project.owner_id != owner.id:
            raise ForbiddenError("Only the project owner may select a freelancer.")

        pre_active = await self.assignment_repo.get_active_by_project_id(project_id)
        self.state_service.validate_assignment(
            pre_project,
            has_active_assignment=pre_active is not None,
        )
        pre_owner_id = pre_project.owner_id
        # Release the read-only snapshot before external HTTP I/O to avoid idle transactions.
        await self.session.rollback()

        # Verify target freelancer through Identity Service over HTTPX.
        verified = await self.identity_client.verify_freelancer(
            freelancer_id,
            request_id=request_id,
        )
        if verified.user_id == owner.id or verified.user_id == pre_owner_id:
            raise InvalidFreelancerError("Project owner cannot assign themselves as freelancer.")

        # Transactional write section with row locking (on PostgreSQL) and partial unique index safety.
        project = await self.project_repo.get_by_id(
            project_id,
            include_relations=False,
            for_update=True,
        )
        if project is None:
            raise ProjectNotFoundError()
        if project.owner_id != owner.id:
            raise ForbiddenError("Only the project owner may select a freelancer.")

        active_assignment = await self.assignment_repo.get_active_by_project_id(
            project_id,
            for_update=True,
        )
        if active_assignment is not None or project.status == ProjectStatus.ASSIGNED:
            raise ConcurrentAssignmentConflictError(
                "Project was already assigned concurrently."
            )
        self.state_service.validate_assignment(project, has_active_assignment=False)

        now = utc_now()
        try:
            assignment = await self.assignment_repo.create_assignment(
                project_id=project.id,
                freelancer_id=verified.user_id,
                assigned_by=owner.id,
                assigned_at=now,
            )
            project.status = ProjectStatus.ASSIGNED
            project.updated_at = now
            await self.session.commit()
        except IntegrityError as exc:
            await self.session.rollback()
            logger.warning(
                "Concurrent assignment database constraint triggered",
                extra={
                    "event": "concurrent_assignment_conflict",
                    "project_id": str(project_id),
                    "user_id": str(owner.id),
                    "error_code": "CONCURRENT_ASSIGNMENT_CONFLICT",
                },
            )
            raise ConcurrentAssignmentConflictError() from exc

        logger.info(
            "Freelancer assigned to project",
            extra={
                "event": "freelancer_assigned",
                "user_id": str(owner.id),
                "role": owner.role.value,
                "project_id": str(project.id),
                "assignment_id": str(assignment.id),
                "freelancer_id": str(verified.user_id),
            },
        )
        return AssignmentResponse(
            id=assignment.id,
            project_id=assignment.project_id,
            freelancer_id=assignment.freelancer_id,
            assigned_by=assignment.assigned_by,
            status=assignment.status,
            project_status=project.status,
            assigned_at=assignment.assigned_at,
            updated_at=assignment.updated_at,
            cancelled_at=assignment.cancelled_at,
        )
