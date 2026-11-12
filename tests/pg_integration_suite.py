"""Optional Live PostgreSQL Integration & Alembic Migration Validation Suite.

Requires a running PostgreSQL 16+ instance with credentials passed via:
  CODEBRIDGE_PG_TEST_URL=postgresql+asyncpg://user:pass@localhost:5432/postgres

Creates isolated per-service temporary databases, executes `alembic upgrade head`
across all 12 database-backed microservices, inspects `pg_tables` and `pg_indexes`,
verifies unique constraint enforcement, transaction rollbacks, and connection cleanup.
"""

from __future__ import annotations

import os
import subprocess
import uuid
from pathlib import Path
from urllib.parse import urlparse, urlunparse

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent

DB_SERVICES = [
    "identity-service",
    "project-service",
    "github-service",
    "code-context-service",
    "workspace-service",
    "review-service",
    "verification-service",
    "payment-service",
    "notification-service",
    "admin-service",
    "audit-service",
    "observability-service",
]


def _replace_db_name(admin_url: str, db_name: str) -> str:
    parsed = urlparse(admin_url)
    return urlunparse(parsed._replace(path=f"/{db_name}"))


@pytest.mark.asyncio
async def test_live_postgresql_migrations_constraints_and_isolation() -> None:
    pg_admin_url = os.environ.get("CODEBRIDGE_PG_TEST_URL", "").strip()
    if not pg_admin_url:
        pytest.skip("CODEBRIDGE_PG_TEST_URL not set; PostgreSQL instance/credentials required.")

    from sqlalchemy import text
    from sqlalchemy.exc import IntegrityError
    from sqlalchemy.ext.asyncio import create_async_engine

    suffix = uuid.uuid4().hex[:8]
    created_dbs: list[str] = []
    admin_engine = create_async_engine(pg_admin_url, isolation_level="AUTOCOMMIT")

    try:
        async with admin_engine.connect() as conn:
            ver = (await conn.execute(text("SELECT version()"))).scalar_one()
            assert "PostgreSQL" in ver

        for svc in DB_SERVICES:
            db_name = f"cb_test_{svc.replace('-', '_')}_{suffix}"
            async with admin_engine.connect() as conn:
                await conn.execute(text(f'CREATE DATABASE "{db_name}"'))
            created_dbs.append(db_name)

            svc_dir = REPO_ROOT / "services" / svc
            alembic_exe = svc_dir / ".venv" / "Scripts" / "alembic.exe"
            svc_db_url = _replace_db_name(pg_admin_url, db_name)
            env = os.environ.copy()
            env["DATABASE_URL"] = svc_db_url
            subprocess.run(
                [str(alembic_exe), "upgrade", "head"],
                cwd=str(svc_dir),
                env=env,
                check=True,
                capture_output=True,
                text=True,
            )

            svc_engine = create_async_engine(svc_db_url)
            try:
                async with svc_engine.connect() as conn:
                    tables = (
                        await conn.execute(
                            text("SELECT tablename FROM pg_tables WHERE schemaname = 'public'")
                        )
                    ).scalars().all()
                    indexes = (
                        await conn.execute(
                            text("SELECT indexname FROM pg_indexes WHERE schemaname = 'public'")
                        )
                    ).scalars().all()
                    assert "alembic_version" in tables
                    assert len(tables) >= 2
                    assert len(indexes) >= 1

                # Verify real transaction rollback & unique constraint enforcement on identity-service
                if svc == "identity-service":
                    uid = str(uuid.uuid4())
                    async with svc_engine.begin() as conn:
                        await conn.execute(
                            text(
                                "INSERT INTO users (id, full_name, email, password_hash, role, is_active, is_verified, created_at, updated_at) "
                                "VALUES (:id, 'Test PG', 'pg@example.com', 'hash', 'OWNER', true, true, NOW(), NOW())"
                            ),
                            {"id": uid},
                        )
                    # Duplicate email must fail with IntegrityError
                    with pytest.raises(IntegrityError):
                        async with svc_engine.begin() as conn:
                            await conn.execute(
                                text(
                                    "INSERT INTO users (id, full_name, email, password_hash, role, is_active, is_verified, created_at, updated_at) "
                                    "VALUES (:id, 'Dup PG', 'pg@example.com', 'hash', 'OWNER', true, true, NOW(), NOW())"
                                ),
                                {"id": str(uuid.uuid4())},
                            )
            finally:
                await svc_engine.dispose()
    finally:
        for db_name in created_dbs:
            async with admin_engine.connect() as conn:
                await conn.execute(
                    text(
                        "SELECT pg_terminate_backend(pid) FROM pg_stat_activity "
                        "WHERE datname = :db AND pid <> pg_backend_pid()"
                    ),
                    {"db": db_name},
                )
                await conn.execute(text(f'DROP DATABASE IF EXISTS "{db_name}"'))
        await admin_engine.dispose()

