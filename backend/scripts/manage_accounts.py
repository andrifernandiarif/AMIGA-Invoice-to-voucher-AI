"""Explicit local bootstrap and idempotent demo provisioning; never runs at startup."""

import argparse
import asyncio
import getpass
import secrets
from sqlalchemy import select
from app.dependencies import AsyncSessionLocal
from app.models.user import User, UserRole
from app.models.client import Client
from app.repositories.user_repository import user_repository
from app.services.passwords import hash_password
from app.services.audit_service import log_event
from app.models.audit import AuditEventType
from app.services.voucher_identity import default_client_code
import uuid

DEMO_ROLES = {
    "demo_super_admin": "SUPER_ADMIN",
    "demo_system_admin": "SYSTEM_ADMIN",
    "demo_user_admin": "USER_ADMIN",
    "demo_service_lead": "SERVICE_LEAD",
    "demo_finance": "FINANCE_REVIEWER",
    "demo_client_pic": "CLIENT_PIC",
    "demo_intake": "INTAKE_SUPPORT",
    "demo_other_client": "CLIENT_PIC",
}


async def seed_demo(db):
    companies = []
    for name in ("AMIGA Demo Company A", "AMIGA Demo Company B"):
        client = await db.scalar(select(Client).where(Client.client_name == name))
        if not client:
            identifier = uuid.uuid4()
            client = Client(
                client_id=identifier,
                client_name=name,
                client_code=default_client_code(name, identifier),
                is_active=True,
            )
            db.add(client)
            await db.flush()
        companies.append(client)
    credentials = []
    for username, role in DEMO_ROLES.items():
        if await user_repository.find_by_username(db, username):
            continue
        password = secrets.token_urlsafe(18)
        user = await user_repository.create_user(db, username, UserRole(role))
        user.password_hash = hash_password(password)
        user.must_change_password = True
        user.full_name = username.replace("_", " ").title()
        user.job_title = role.replace("_", " ").title()
        company = companies[1 if username == "demo_other_client" else 0]
        await user_repository.add_assignment(db, user.user_id, company.client_id)
        await log_event(
            db,
            AuditEventType.USER_MODIFIED,
            {"action": "DEMO_ACCOUNT_CREATED", "user_id": str(user.user_id)},
            actor_id=user.user_id,
        )
        credentials.append((username, password, role, company.client_name))
    return credentials


async def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=("bootstrap", "seed-demo"))
    parser.add_argument("--username", default="super_admin")
    args = parser.parse_args()
    async with AsyncSessionLocal() as db:
        if args.command == "bootstrap":
            if await db.scalar(select(User).where(User.role == UserRole.SUPER_ADMIN, User.is_active.is_(True))):
                raise SystemExit("An active Super Admin already exists; use account management.")
            if await user_repository.find_by_username(db, args.username):
                raise SystemExit("Username exists. Choose a new bootstrap username.")
            password = getpass.getpass("Super Admin password (12–128 characters): ")
            if password != getpass.getpass("Confirm password: "):
                raise SystemExit("Passwords do not match.")
            encoded = hash_password(password)
            user = await user_repository.create_user(db, args.username, UserRole.SUPER_ADMIN)
            user.password_hash, user.full_name, user.job_title = encoded, "Super Administrator", "Administrator"
            await log_event(
                db, AuditEventType.USER_MODIFIED, {"action": "BOOTSTRAP_SUPER_ADMIN"}, actor_id=user.user_id
            )
            await db.commit()
            print(f"Created {args.username}. Sign in with your chosen password.")
        else:
            rows = await seed_demo(db)
            await db.commit()
            for username, password, role, company in rows:
                print(f"{username}\t{password}\t{role}\t{company}")
            print(
                f"Created {len(rows)} accounts. Existing accounts were left unchanged. Change temporary passwords at first login."
            )


if __name__ == "__main__":
    asyncio.run(main())
