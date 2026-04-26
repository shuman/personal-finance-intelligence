#!/usr/bin/env python3
"""CLI utility to create users and update passwords."""

import argparse
import asyncio
import sys
import uuid
from datetime import datetime

from sqlalchemy import select

from app.database import AsyncSessionLocal
from app.models import User
from app.utils.auth import get_password_hash
from app.utils.encryption import hash_value


def _validate_password(password: str) -> None:
    if len(password) < 8:
        raise ValueError("Password must be at least 8 characters long")


async def create_user(email: str, password: str, full_name: str | None, is_admin: bool) -> int:
    _validate_password(password)

    async with AsyncSessionLocal() as db:
        result = await db.execute(select(User).where(User.email_hash == hash_value(email.lower())))
        existing = result.scalar_one_or_none()
        if existing:
            raise ValueError(f"User already exists: {email}")

        user = User(
            uuid=str(uuid.uuid4()),
            email=email,
            email_hash=hash_value(email.lower()),
            hashed_password=get_password_hash(password),
            full_name=full_name,
            is_active=True,
            is_admin=is_admin,
            created_at=datetime.utcnow(),
            updated_at=datetime.utcnow(),
        )
        db.add(user)
        await db.commit()
        await db.refresh(user)
        return user.id


async def set_password(email: str, password: str) -> None:
    _validate_password(password)

    async with AsyncSessionLocal() as db:
        result = await db.execute(select(User).where(User.email_hash == hash_value(email.lower())))
        user = result.scalar_one_or_none()
        if not user:
            raise ValueError(f"User not found: {email}")

        user.hashed_password = get_password_hash(password)
        user.updated_at = datetime.utcnow()
        await db.commit()


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="User management CLI")
    subparsers = parser.add_subparsers(dest="command", required=True)

    create_parser = subparsers.add_parser("create-user", help="Create a new user")
    create_parser.add_argument("--email", required=True, help="User email")
    create_parser.add_argument("--password", required=True, help="User password (min 8 chars)")
    create_parser.add_argument("--name", dest="full_name", help="Full name")
    create_parser.add_argument("--admin", action="store_true", help="Create as admin user")

    password_parser = subparsers.add_parser("set-password", help="Set a user's password")
    password_parser.add_argument("--email", required=True, help="User email")
    password_parser.add_argument("--password", required=True, help="New password (min 8 chars)")

    return parser


async def _run(args: argparse.Namespace) -> int:
    if args.command == "create-user":
        user_id = await create_user(
            email=args.email,
            password=args.password,
            full_name=args.full_name,
            is_admin=args.admin,
        )
        print(f"Created user {args.email} (id={user_id})")
        return 0

    if args.command == "set-password":
        await set_password(email=args.email, password=args.password)
        print(f"Updated password for {args.email}")
        return 0

    print(f"Unknown command: {args.command}")
    return 1


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()
    try:
        return asyncio.run(_run(args))
    except ValueError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 2
    except Exception as exc:  # pragma: no cover
        print(f"Unexpected error: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
