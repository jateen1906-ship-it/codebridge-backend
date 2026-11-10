"""Central project lifecycle state machine validator and future workflow transition hooks."""

from __future__ import annotations

from datetime import datetime, timezone

from app.core.exceptions import (
    InvalidProjectStatusError,
    ProjectAlreadyAssignedError,
    ValidationError,
)
from app.models.base import utc_now
from app.models.project import Project, ProjectStatus

ALLOWED_PROJECT_TRANSITIONS: dict[ProjectStatus, frozenset[ProjectStatus]] = {
    ProjectStatus.OPEN: frozenset({ProjectStatus.ASSIGNED, ProjectStatus.CANCELLED}),
    ProjectStatus.ASSIGNED: frozenset({ProjectStatus.IN_PROGRESS}),
    ProjectStatus.IN_PROGRESS: frozenset(
        {ProjectStatus.CHANGES_REQUESTED, ProjectStatus.COMPLETED}
    ),
    ProjectStatus.CHANGES_REQUESTED: frozenset({ProjectStatus.IN_PROGRESS}),
    ProjectStatus.COMPLETED: frozenset(),
    ProjectStatus.CANCELLED: frozenset(),
}


class ProjectStateService:
    """Enforces controlled project status transitions and publish/cancel eligibility."""

    @staticmethod
    def validate_transition(
        current_status: ProjectStatus,
        target_status: ProjectStatus,
    ) -> None:
        """Verify that transitioning from current_status to target_status is permitted."""
        allowed_targets = ALLOWED_PROJECT_TRANSITIONS.get(current_status, frozenset())
        if target_status not in allowed_targets:
            raise InvalidProjectStatusError(
                f"Cannot transition project from {current_status.value} to {target_status.value}."
            )

    @staticmethod
    def validate_publish(project: Project) -> None:
        """Verify project completeness and ensure it has not already been published or closed."""
        if project.status != ProjectStatus.OPEN:
            raise InvalidProjectStatusError(
                f"Cannot publish a project with status {project.status.value}."
            )
        if project.published_at is not None:
            raise InvalidProjectStatusError("Project has already been published.")

        if not project.title or len(project.title.strip()) < 3:
            raise ValidationError("Project title must be complete before publishing.")
        if not project.description or len(project.description.strip()) < 5:
            raise ValidationError("Project description must be complete before publishing.")
        if project.budget is None or project.budget <= 0:
            raise ValidationError("Project budget must be positive before publishing.")
        if project.deadline < datetime.now(timezone.utc).date():
            raise ValidationError("Cannot publish a project with a past deadline.")

        req = project.requirement
        if req is None or not req.expected_result or not req.expected_result.strip():
            raise ValidationError("Project expected_result is required before publishing.")
        if not req.required_skills:
            raise ValidationError("At least one required skill is needed before publishing.")

    @classmethod
    def validate_cancel(
        cls,
        project: Project,
        *,
        has_active_assignment: bool,
    ) -> None:
        """Allow cancellation only on unassigned OPEN projects in Phase 3."""
        if has_active_assignment:
            raise InvalidProjectStatusError(
                "Assigned projects cannot be cancelled in this workflow phase."
            )
        cls.validate_transition(project.status, ProjectStatus.CANCELLED)

    @classmethod
    def validate_assignment(
        cls,
        project: Project,
        *,
        has_active_assignment: bool,
    ) -> None:
        """Allow freelancer assignment only when project is OPEN without active assignments."""
        if project.status == ProjectStatus.ASSIGNED or has_active_assignment:
            raise ProjectAlreadyAssignedError("Project already has an active assignment.")
        cls.validate_transition(project.status, ProjectStatus.ASSIGNED)

    @classmethod
    def advance_to_in_progress(
        cls,
        project: Project,
        *,
        now: datetime | None = None,
    ) -> Project:
        """Service hook for Workspace Service integration: ASSIGNED/CHANGES_REQUESTED -> IN_PROGRESS."""
        cls.validate_transition(project.status, ProjectStatus.IN_PROGRESS)
        ts = now or utc_now()
        project.status = ProjectStatus.IN_PROGRESS
        project.updated_at = ts
        return project

    @classmethod
    def request_changes(
        cls,
        project: Project,
        *,
        now: datetime | None = None,
    ) -> Project:
        """Service hook for Review Service integration: IN_PROGRESS -> CHANGES_REQUESTED."""
        cls.validate_transition(project.status, ProjectStatus.CHANGES_REQUESTED)
        ts = now or utc_now()
        project.status = ProjectStatus.CHANGES_REQUESTED
        project.updated_at = ts
        return project

    @classmethod
    def complete_project(
        cls,
        project: Project,
        *,
        approval_verified: bool = False,
        now: datetime | None = None,
    ) -> Project:
        """Service hook for Review/Verification workflow completion: IN_PROGRESS -> COMPLETED."""
        if not approval_verified:
            raise InvalidProjectStatusError(
                "Project cannot be marked COMPLETED without verified review approval."
            )
        cls.validate_transition(project.status, ProjectStatus.COMPLETED)
        ts = now or utc_now()
        project.status = ProjectStatus.COMPLETED
        project.closed_at = ts
        project.updated_at = ts
        return project

