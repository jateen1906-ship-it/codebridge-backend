"""SQLAlchemy async DeclarativeBase and UTC timestamp helper."""

from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy.orm import DeclarativeBase


def utc_now() -> datetime:
    """Return timezone-aware current UTC timestamp."""
    return datetime.now(timezone.utc)


class Base(DeclarativeBase):
    """Base declarative class for Workspace Service ORM models."""

