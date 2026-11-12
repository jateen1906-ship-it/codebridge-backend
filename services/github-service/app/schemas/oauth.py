"""Pydantic schemas for GitHub OAuth callback and connection disconnection responses."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field


class OAuthCallbackResponse(BaseModel):
    """Safe metadata returned when GitHub OAuth authorization completes successfully."""

    success: Literal[True] = True
    status: Literal["CONNECTED"] = "CONNECTED"
    project_id: uuid.UUID
    connection_id: uuid.UUID
    github_user_id: int
    github_login: str = Field(..., min_length=1, max_length=255)
    scopes: list[str]
    connected_at: datetime


class GitHubConnectionDisconnectResponse(BaseModel):
    """Response confirming owner GitHub OAuth connection & project links are disconnected."""

    success: Literal[True] = True
    status: Literal["DISCONNECTED"] = "DISCONNECTED"
    disconnected_projects_count: int = Field(..., ge=0)
    disconnected_at: datetime

