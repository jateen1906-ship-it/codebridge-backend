"""Project requirement schemas and skill normalization helpers."""

from __future__ import annotations

import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict


def normalize_skill_list(raw_skills: list[str]) -> list[str]:
    """Trim whitespace, reject oversized tags, deduplicate case-insensitively, and enforce non-empty."""
    cleaned: list[str] = []
    seen: set[str] = set()
    for item in raw_skills:
        tag = " ".join(item.strip().split())
        if not tag:
            continue
        if len(tag) > 50:
            raise ValueError("Each skill must be at most 50 characters.")
        key = tag.lower()
        if key not in seen:
            seen.add(key)
            cleaned.append(tag)
    if not cleaned:
        raise ValueError("required_skills must contain at least one non-empty skill.")
    return cleaned


class ProjectRequirementResponse(BaseModel):
    """Structured technical requirement summary associated with a project."""

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    project_id: uuid.UUID
    expected_result: str
    required_skills: list[str]
    created_at: datetime
    updated_at: datetime

