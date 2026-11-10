"""Assignment request, summary, and detail schemas."""

from __future__ import annotations

import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field

from app.models.assignment import AssignmentStatus
from app.models.project import ProjectStatus


class SelectFreelancerRequest(BaseModel):
    """Owner request payload for selecting an Identity-verified freelancer."""

    model_config = ConfigDict(extra="forbid")

    freelancer_id: uuid.UUID = Field(
        ...,
        description="Identity Service User UUID of the selected freelancer.",
        examples=["550e8400-e29b-41d4-a716-446655440000"],
    )


class AssignmentPublicSummary(BaseModel):
    """Safe, non-sensitive active assignment summary exposed inside Project responses."""

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    freelancer_id: uuid.UUID
    status: AssignmentStatus
    assigned_at: datetime


class AssignmentResponse(BaseModel):
    """Response returned upon assigning a freelancer to a project."""

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    project_id: uuid.UUID
    freelancer_id: uuid.UUID
    assigned_by: uuid.UUID
    status: AssignmentStatus
    project_status: ProjectStatus = ProjectStatus.ASSIGNED
    assigned_at: datetime
    updated_at: datetime
    cancelled_at: datetime | None = None

