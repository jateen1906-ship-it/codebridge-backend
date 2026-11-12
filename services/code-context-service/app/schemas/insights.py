"""Pydantic schemas for deterministic repository insights and optional AI summaries."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field


class GenerateInsightsRequest(BaseModel):
    """Optional configuration for POST /insights/generate."""

    model_config = ConfigDict(extra="forbid")

    include_ai_summary: bool = True


class DeterministicInsightsReport(BaseModel):
    """Deterministic structural & hygiene report derived strictly from indexed repo facts."""

    repository_full_name: str
    branch: str
    index_version: int
    languages_detected: dict[str, int] = Field(default_factory=dict)
    total_files_discovered: int
    indexed_file_count: int
    skipped_file_count: int
    skipped_reasons: dict[str, int] = Field(default_factory=dict)
    total_indexed_bytes: int
    total_chunks: int
    has_readme: bool
    readme_paths: list[str] = Field(default_factory=list)
    has_tests: bool
    test_file_paths: list[str] = Field(default_factory=list)
    test_execution_note: str
    dependency_manifests: list[str] = Field(default_factory=list)
    container_and_ci_configs: list[str] = Field(default_factory=list)
    lint_and_format_configs: list[str] = Field(default_factory=list)
    large_files: list[dict[str, Any]] = Field(default_factory=list)
    last_indexed_at: datetime | None = None
    is_stale: bool = False
    warnings: list[str] = Field(default_factory=list)


class AIInsightSummary(BaseModel):
    """Explicitly labeled AI architectural summary constrained to indexed evidence."""

    architecture_overview: str
    likely_entry_points: list[str] = Field(default_factory=list)
    missing_setup_or_hygiene_signals: list[str] = Field(default_factory=list)
    high_risk_or_incomplete_areas: list[str] = Field(default_factory=list)
    evidence_files: list[str] = Field(default_factory=list)


class RepositoryInsightResponse(BaseModel):
    """Persisted repository insight payload returned by POST /insights/generate and GET /insights."""

    id: uuid.UUID
    project_id: uuid.UUID
    repository_index_id: uuid.UUID
    index_version: int
    ai_generated: bool
    llm_provider: str | None = None
    llm_model: str | None = None
    generated_at: datetime
    deterministic_report: DeterministicInsightsReport
    ai_summary: AIInsightSummary | None = None


class InsightsEnvelopeResponse(BaseModel):
    """Standardized Code Context insights response envelope."""

    success: Literal[True] = True
    data: RepositoryInsightResponse
    request_id: str

