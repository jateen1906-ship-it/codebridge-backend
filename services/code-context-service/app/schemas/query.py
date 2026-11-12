"""Pydantic schemas for grounded repository semantic retrieval and RAG query responses."""

from __future__ import annotations

import uuid
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator


class QueryRepositoryRequest(BaseModel):
    """Validated semantic question/query payload for POST /query."""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    query: str = Field(..., min_length=2, max_length=2000)
    top_k: int | None = Field(default=None, ge=1, le=50)

    @field_validator("query")
    @classmethod
    def _validate_query(cls, value: str) -> str:
        cleaned = " ".join(value.strip().split())
        if len(cleaned) < 2:
            raise ValueError("query must contain at least 2 non-whitespace characters.")
        return cleaned


class RetrievedSource(BaseModel):
    """Exact cited source chunk returned from ChromaDB vector retrieval."""

    file_path: str
    start_line: int = Field(..., ge=1)
    end_line: int = Field(..., ge=1)
    language: str
    relevance_score: float = Field(..., ge=0.0, le=1.0)
    snippet: str
    chunk_identifier: str


class QueryResultData(BaseModel):
    """Grounded RAG answer and exact retrieved repository citations."""

    project_id: uuid.UUID
    index_version: int
    branch: str
    answer: str
    sources: list[RetrievedSource]
    retrieval_count: int = Field(..., ge=0)
    ai_generated: bool
    fallback_used: bool
    grounded: bool


class QueryRepositoryResponse(BaseModel):
    """Standardized Code Context query response envelope."""

    success: Literal[True] = True
    data: QueryResultData
    request_id: str

