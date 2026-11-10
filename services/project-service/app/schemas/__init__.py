"""Pydantic request/response schemas for Project Service."""

from app.schemas.assignment import (
    AssignmentPublicSummary,
    AssignmentResponse,
    SelectFreelancerRequest,
)
from app.schemas.common import ErrorDetail, ErrorResponse, HealthResponse
from app.schemas.project import (
    ProjectCreateRequest,
    ProjectListResponse,
    ProjectResponse,
    ProjectUpdateRequest,
)
from app.schemas.requirements import ProjectRequirementResponse, normalize_skill_list

__all__ = [
    "AssignmentPublicSummary",
    "AssignmentResponse",
    "ErrorDetail",
    "ErrorResponse",
    "HealthResponse",
    "ProjectCreateRequest",
    "ProjectListResponse",
    "ProjectRequirementResponse",
    "ProjectResponse",
    "ProjectUpdateRequest",
    "SelectFreelancerRequest",
    "normalize_skill_list",
]
