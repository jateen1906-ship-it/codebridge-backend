"""Controlled Workspace lifecycle state machine."""

from __future__ import annotations

from app.core.exceptions import ConflictError
from app.models.workspace import WorkspaceStatus

ALLOWED_WORKSPACE_TRANSITIONS: dict[WorkspaceStatus, frozenset[WorkspaceStatus]] = {
    WorkspaceStatus.PROVISIONING: frozenset(
        {WorkspaceStatus.READY, WorkspaceStatus.FAILED, WorkspaceStatus.DESTROYED}
    ),
    WorkspaceStatus.READY: frozenset(
        {
            WorkspaceStatus.RUNNING,
            WorkspaceStatus.STOPPED,
            WorkspaceStatus.EXPIRED,
            WorkspaceStatus.DESTROYED,
        }
    ),
    WorkspaceStatus.RUNNING: frozenset(
        {
            WorkspaceStatus.STOPPED,
            WorkspaceStatus.EXPIRED,
            WorkspaceStatus.DESTROYED,
        }
    ),
    WorkspaceStatus.STOPPED: frozenset(
        {
            WorkspaceStatus.RUNNING,
            WorkspaceStatus.EXPIRED,
            WorkspaceStatus.DESTROYED,
        }
    ),
    WorkspaceStatus.FAILED: frozenset({WorkspaceStatus.DESTROYED}),
    WorkspaceStatus.EXPIRED: frozenset({WorkspaceStatus.DESTROYED}),
    WorkspaceStatus.DESTROYED: frozenset(),
}


class WorkspaceStateService:
    """Enforces allowed Workspace lifecycle state transitions."""

    @staticmethod
    def assert_transition(current: WorkspaceStatus, target: WorkspaceStatus) -> None:
        allowed = ALLOWED_WORKSPACE_TRANSITIONS.get(current, frozenset())
        if target not in allowed:
            raise ConflictError(
                f"Cannot transition workspace from {current.value} to {target.value}.",
                error_code="INVALID_WORKSPACE_STATE",
            )

