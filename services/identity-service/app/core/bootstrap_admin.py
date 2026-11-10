"""Out-of-band CLI utility to provision an initial ADMIN account securely."""

from __future__ import annotations

import argparse
import asyncio
import getpass

from app.core.config import load_settings
from app.core.database import build_async_engine, build_session_factory
from app.models.user import UserRole
from app.schemas.user import AdminUserCreateRequest
from app.services.user_service import UserService


async def _create_admin(full_name: str, email: str, password: str) -> None:
    settings = load_settings()
    engine = build_async_engine(settings)
    session_factory = build_session_factory(engine)
    try:
        async with session_factory() as session:
            service = UserService(session)
            payload = AdminUserCreateRequest(
                full_name=full_name,
                email=email,
                password=password,
                role=UserRole.ADMIN,
                is_verified=True,
            )
            admin_user = await service.provision_user_by_admin(payload)
            print(f"Provisioned ADMIN user id={admin_user.id} email={admin_user.email}")
    finally:
        await engine.dispose()


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Provision an initial ADMIN user for CodeBridge Identity Service."
    )
    parser.add_argument("--full-name", required=True, help="Full name of the administrator.")
    parser.add_argument("--email", required=True, help="Email address for the administrator.")
    args = parser.parse_args()

    password = getpass.getpass("Admin password (8+ chars, upper/lower/digit/special): ")
    asyncio.run(_create_admin(args.full_name, args.email, password))


if __name__ == "__main__":
    main()

