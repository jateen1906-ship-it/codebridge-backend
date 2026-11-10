"""SQLAlchemy declarative base and shared UTC timestamp helpers."""

from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy.orm import DeclarativeBase


def utc_now() -> datetime:
    """Return current UTC datetime for SQLAlchemy column defaults."""
    return datetime.now(timezone.utc)


class Base(DeclarativeBase):
    """Declarative base for all Identity Service ORM models."""

