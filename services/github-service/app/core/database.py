"""Async SQLAlchemy 2.x engine, session factory, and FastAPI dependency helpers."""

from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Any

from fastapi import Request
from sqlalchemy import event
from sqlalchemy.pool import NullPool
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from app.core.config import Settings


def build_async_engine(settings: Settings) -> AsyncEngine:
    """Create an async SQLAlchemy engine configured for PostgreSQL or isolated SQLite tests."""
    is_sqlite = settings.DATABASE_URL.startswith("sqlite")
    engine_kwargs: dict[str, Any] = {
        "echo": settings.DB_ECHO,
        "future": True,
    }
    if is_sqlite and ":memory:" not in settings.DATABASE_URL:
        engine_kwargs["poolclass"] = NullPool

    if not is_sqlite:
        engine_kwargs["pool_pre_ping"] = True
        engine_kwargs["pool_size"] = settings.DATABASE_POOL_SIZE
        engine_kwargs["max_overflow"] = settings.DATABASE_MAX_OVERFLOW

    engine = create_async_engine(settings.DATABASE_URL, **engine_kwargs)

    if is_sqlite:

        @event.listens_for(engine.sync_engine, "connect")
        def _set_sqlite_pragma(dbapi_connection: Any, _connection_record: Any) -> None:
            cursor = dbapi_connection.cursor()
            cursor.execute("PRAGMA foreign_keys=ON")
            cursor.close()

    return engine


def build_session_factory(engine: AsyncEngine) -> async_sessionmaker[AsyncSession]:
    """Create an async session factory bound to the configured engine."""
    return async_sessionmaker(
        bind=engine,
        class_=AsyncSession,
        expire_on_commit=False,
        autoflush=False,
    )


async def get_db_session(request: Request) -> AsyncIterator[AsyncSession]:
    """Yield a scoped AsyncSession and roll back cleanly on unhandled exceptions."""
    session_factory: async_sessionmaker[AsyncSession] = request.app.state.session_factory
    async with session_factory() as session:
        try:
            yield session
        except Exception:
            await session.rollback()
            raise
