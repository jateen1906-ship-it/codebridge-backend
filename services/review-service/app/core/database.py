"""Async SQLAlchemy 2.x engine and session factory for Review Service."""

from __future__ import annotations

from collections.abc import AsyncIterator

from fastapi import Request
from sqlalchemy import text
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.pool import NullPool

from app.core.config import Settings


def build_async_engine(settings: Settings) -> AsyncEngine:
    """Create an async SQLAlchemy engine suited to PostgreSQL or SQLite."""
    is_sqlite = settings.DATABASE_URL.startswith("sqlite+aiosqlite://")
    if is_sqlite:
        return create_async_engine(
            settings.DATABASE_URL,
            echo=settings.DB_ECHO,
            poolclass=NullPool,
            future=True,
        )
    return create_async_engine(
        settings.DATABASE_URL,
        echo=settings.DB_ECHO,
        pool_size=settings.DATABASE_POOL_SIZE,
        max_overflow=settings.DATABASE_MAX_OVERFLOW,
        pool_pre_ping=True,
        future=True,
    )


def build_session_factory(engine: AsyncEngine) -> async_sessionmaker[AsyncSession]:
    """Create async session factory bound to the given engine."""
    return async_sessionmaker(
        bind=engine,
        class_=AsyncSession,
        expire_on_commit=False,
        autoflush=False,
    )


async def get_db_session(request: Request) -> AsyncIterator[AsyncSession]:
    """Yield request-scoped AsyncSession."""
    session_factory: async_sessionmaker[AsyncSession] = request.app.state.session_factory
    async with session_factory() as session:
        yield session


async def check_database_health(engine: AsyncEngine) -> bool:
    """Execute lightweight connectivity check against Review Service DB."""
    try:
        async with engine.connect() as conn:
            await conn.execute(text("SELECT 1"))
        return True
    except Exception:
        return False

