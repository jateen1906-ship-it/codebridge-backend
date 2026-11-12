"""Declarative base and UTC timestamp utilities for GitHub Service ORM models."""

from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy.orm import DeclarativeBase


def utc_now() -> datetime:
    """Return a timezone-aware UTC timestamp."""
    return datetime.now(timezone.utc)


class Base(DeclarativeBase):
    """Shared SQLAlchemy 2.x DeclarativeBase for codebridge_github models."""

