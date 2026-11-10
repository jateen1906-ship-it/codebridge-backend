"""Project creation, update, detail, and paginated listing schemas."""

from __future__ import annotations

import uuid
from datetime import date, datetime, timezone
from decimal import Decimal
from typing import Annotated

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    PlainSerializer,
    field_validator,
    model_validator,
)

from app.models.project import ProjectStatus
from app.schemas.assignment import AssignmentPublicSummary
from app.schemas.requirements import normalize_skill_list

MoneyDecimal = Annotated[
    Decimal,
    PlainSerializer(lambda value: f"{value:.2f}", return_type=str, when_used="json"),
]

_MAX_BUDGET = Decimal("9999999999.99")


def _clean_text(value: str, *, field_name: str, min_length: int) -> str:
    cleaned = " ".join(value.strip().split()) if field_name == "title" else value.strip()
    if len(cleaned) < min_length:
        raise ValueError(f"{field_name} must be at least {min_length} characters long.")
    return cleaned


def _validate_budget_decimal(value: object) -> Decimal:
    if isinstance(value, bool):
        raise ValueError("budget must be a positive decimal amount.")
    if isinstance(value, Decimal):
        dec = value
    elif isinstance(value, (int, float, str)):
        try:
            dec = Decimal(str(value).strip())
        except Exception as exc:
            raise ValueError("budget must be a valid decimal number.") from exc
    else:
        raise ValueError("budget must be a valid decimal number.")

    if not dec.is_finite() or dec <= Decimal("0"):
        raise ValueError("budget must be greater than zero.")
    if dec > _MAX_BUDGET:
        raise ValueError("budget exceeds maximum supported precision NUMERIC(12,2).")

    exponent = dec.as_tuple().exponent
    if isinstance(exponent, int) and exponent < -2:
        raise ValueError("budget may have at most 2 decimal places.")

    return dec.quantize(Decimal("0.01"))


def _validate_currency_code(value: str) -> str:
    cleaned = value.strip().upper()
    if len(cleaned) != 3 or not cleaned.isascii() or not cleaned.isalpha():
        raise ValueError("currency must be a 3-letter ISO currency code (e.g., INR).")
    return cleaned


def _validate_future_or_today_deadline(value: date) -> date:
    today_utc = datetime.now(timezone.utc).date()
    if value < today_utc:
        raise ValueError("deadline cannot be in the past.")
    return value


class ProjectCreateRequest(BaseModel):
    """OWNER-only project creation request payload."""

    model_config = ConfigDict(extra="forbid")

    title: str = Field(..., min_length=3, max_length=200, examples=["Fix payment callback"])
    description: str = Field(
        ...,
        min_length=5,
        max_length=10000,
        examples=["Payment succeeds but the callback is not saving the final status."],
    )
    expected_result: str = Field(
        ...,
        min_length=5,
        max_length=5000,
        examples=["Payment status is stored correctly after callback."],
    )
    budget: MoneyDecimal = Field(..., examples=["5000.00"])
    currency: str | None = Field(default=None, min_length=3, max_length=3, examples=["INR"])
    deadline: date = Field(..., examples=["2026-12-20"])
    required_skills: list[str] = Field(
        ...,
        min_length=1,
        max_length=30,
        examples=[["Python", "React", "API integration"]],
    )

    @field_validator("title")
    @classmethod
    def _validate_title(cls, value: str) -> str:
        return _clean_text(value, field_name="title", min_length=3)

    @field_validator("description")
    @classmethod
    def _validate_description(cls, value: str) -> str:
        return _clean_text(value, field_name="description", min_length=5)

    @field_validator("expected_result")
    @classmethod
    def _validate_expected_result(cls, value: str) -> str:
        return _clean_text(value, field_name="expected_result", min_length=5)

    @field_validator("budget", mode="before")
    @classmethod
    def _validate_budget(cls, value: object) -> Decimal:
        return _validate_budget_decimal(value)

    @field_validator("currency")
    @classmethod
    def _validate_currency(cls, value: str | None) -> str | None:
        if value is None:
            return None
        return _validate_currency_code(value)

    @field_validator("deadline")
    @classmethod
    def _validate_deadline(cls, value: date) -> date:
        return _validate_future_or_today_deadline(value)

    @field_validator("required_skills")
    @classmethod
    def _validate_skills(cls, value: list[str]) -> list[str]:
        return normalize_skill_list(value)


class ProjectUpdateRequest(BaseModel):
    """Strict partial update payload for OPEN owner projects (forbids id, owner_id, status)."""

    model_config = ConfigDict(extra="forbid")

    title: str | None = Field(default=None, min_length=3, max_length=200)
    description: str | None = Field(default=None, min_length=5, max_length=10000)
    expected_result: str | None = Field(default=None, min_length=5, max_length=5000)
    budget: MoneyDecimal | None = Field(default=None)
    currency: str | None = Field(default=None, min_length=3, max_length=3)
    deadline: date | None = Field(default=None)
    required_skills: list[str] | None = Field(default=None, min_length=1, max_length=30)

    @field_validator("title")
    @classmethod
    def _validate_title(cls, value: str | None) -> str | None:
        return _clean_text(value, field_name="title", min_length=3) if value is not None else None

    @field_validator("description")
    @classmethod
    def _validate_description(cls, value: str | None) -> str | None:
        return (
            _clean_text(value, field_name="description", min_length=5)
            if value is not None
            else None
        )

    @field_validator("expected_result")
    @classmethod
    def _validate_expected_result(cls, value: str | None) -> str | None:
        return (
            _clean_text(value, field_name="expected_result", min_length=5)
            if value is not None
            else None
        )

    @field_validator("budget", mode="before")
    @classmethod
    def _validate_budget(cls, value: object) -> Decimal | None:
        if value is None:
            return None
        return _validate_budget_decimal(value)

    @field_validator("currency")
    @classmethod
    def _validate_currency(cls, value: str | None) -> str | None:
        return _validate_currency_code(value) if value is not None else None

    @field_validator("deadline")
    @classmethod
    def _validate_deadline(cls, value: date | None) -> date | None:
        return _validate_future_or_today_deadline(value) if value is not None else None

    @field_validator("required_skills")
    @classmethod
    def _validate_skills(cls, value: list[str] | None) -> list[str] | None:
        return normalize_skill_list(value) if value is not None else None

    @model_validator(mode="after")
    def _ensure_non_empty_valid_patch(self) -> "ProjectUpdateRequest":
        if not self.model_fields_set:
            raise ValueError("At least one updatable field must be provided.")
        for field_name in self.model_fields_set:
            if getattr(self, field_name) is None:
                raise ValueError(f"{field_name} cannot be null.")
        return self


class ProjectResponse(BaseModel):
    """Standard Project detail and listing item response."""

    id: uuid.UUID
    owner_id: uuid.UUID
    title: str
    description: str
    expected_result: str
    required_skills: list[str]
    budget: MoneyDecimal
    currency: str
    deadline: date
    status: ProjectStatus
    created_at: datetime
    updated_at: datetime
    published_at: datetime | None = None
    closed_at: datetime | None = None
    active_assignment: AssignmentPublicSummary | None = None


class ProjectListResponse(BaseModel):
    """Paginated list of projects scoped to the caller's role."""

    items: list[ProjectResponse]
    total: int
    page: int
    page_size: int
    total_pages: int

