"""Declarative ORM base class and UTC timestamp helpers for Project Service."""

from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy.ext.asyncio import AsyncAttrs
from sqlalchemy.orm import DeclarativeBase


def utc_now() -> datetime:
    """Return the current UTC timezone-aware datetime."""
    return datetime.now(timezone.utc)


class Base(AsyncAttrs, DeclarativeBase):
    """Base declarative class for all Project Service ORM models."""

