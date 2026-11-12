"""Async SQLAlchemy engine and session management for Observability Service."""

from __future__ import annotations

from collections.abc import AsyncIterator

from fastapi import Request
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker, create_async_engine

from app.core.config import Settings


def build_async_engine(settings: Settings) -> AsyncEngine:
    kwargs: dict[str, object] = {"echo": settings.DB_ECHO}
    if settings.DATABASE_URL.startswith("postgresql+asyncpg://"):
        kwargs["pool_size"] = settings.DATABASE_POOL_SIZE
        kwargs["max_overflow"] = settings.DATABASE_MAX_OVERFLOW
        kwargs["pool_pre_ping"] = True
    else:
        kwargs["connect_args"] = {"check_same_thread": False}
    return create_async_engine(settings.DATABASE_URL, **kwargs)


def build_session_factory(engine: AsyncEngine) -> async_sessionmaker[AsyncSession]:
    return async_sessionmaker(bind=engine, class_=AsyncSession, expire_on_commit=False)


async def get_db_session(request: Request) -> AsyncIterator[AsyncSession]:
    factory: async_sessionmaker[AsyncSession] = request.app.state.session_factory
    async with factory() as session:
        yield session


async def check_database_connectivity(session: AsyncSession) -> bool:
    await session.execute(text("SELECT 1"))
    return True
