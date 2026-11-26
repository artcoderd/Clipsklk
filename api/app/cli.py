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


def vapid_keys() -> int:
    """Prints a VAPID key pair for .env (keep the private one secret)."""
    import base64

    from cryptography.hazmat.primitives import serialization
    from py_vapid import Vapid

    v = Vapid()
    v.generate_keys()
    raw_pub = v.public_key.public_bytes(serialization.Encoding.X962, serialization.PublicFormat.UncompressedPoint)
    raw_priv = v.private_key.private_numbers().private_value.to_bytes(32, "big")
    b64 = lambda b: base64.urlsafe_b64encode(b).decode().rstrip("=")  # noqa: E731
    print(f"VAPID_PUBLIC_KEY={b64(raw_pub)}")
    print(f"VAPID_PRIVATE_KEY={b64(raw_priv)}")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(prog="python -m app.cli")
    sub = parser.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("create-owner")
    p.add_argument("email")
    p.add_argument("--password", help="non-interactive (avoid: ends up in shell history)")
    sub.add_parser("vapid-keys", help="generate keys for web push notifications")
    args = parser.parse_args()
    if args.cmd == "create-owner":
        return create_owner(args.email, args.password)
    if args.cmd == "vapid-keys":
        return vapid_keys()
    return 2


if __name__ == "__main__":
    sys.exit(main())
