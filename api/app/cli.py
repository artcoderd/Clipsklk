"""Owner bootstrap. Run once on the server:

    docker compose exec api python -m app.cli create-owner you@example.com

The owner is the single super-administrator; this command refuses to create a
second one.
"""
import argparse
import getpass
import sys

from sqlalchemy import select

from app.db import SessionLocal
from app.models import Role, User
from app.security import hash_password


def create_owner(email: str, password: str | None) -> int:
    email = email.lower()
    db = SessionLocal()
    try:
        if db.scalar(select(User).where(User.role == Role.owner)):
            print("An owner already exists; refusing to create another.", file=sys.stderr)
            return 1
        if db.scalar(select(User).where(User.email == email)):
            print("That email is already registered.", file=sys.stderr)
            return 1
        if password is None:
            password = getpass.getpass("Password (min 10 chars): ")
            if password != getpass.getpass("Repeat password: "):
                print("Passwords do not match.", file=sys.stderr)
                return 1
        if len(password) < 10:
            print("Password must be at least 10 characters.", file=sys.stderr)
            return 1
        db.add(User(email=email, name="Owner", password_hash=hash_password(password), role=Role.owner))
        db.commit()
        print(f"Owner {email} created. Log in and enable 2FA from the admin panel.")
        return 0
    finally:
        db.close()


def main() -> int:
    parser = argparse.ArgumentParser(prog="python -m app.cli")
    sub = parser.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("create-owner")
    p.add_argument("email")
    p.add_argument("--password", help="non-interactive (avoid: ends up in shell history)")
    args = parser.parse_args()
    if args.cmd == "create-owner":
        return create_owner(args.email, args.password)
    return 2


if __name__ == "__main__":
    sys.exit(main())
