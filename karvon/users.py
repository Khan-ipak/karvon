"""Manage users from the command line.

    python -m karvon.users create admin --role admin
    python -m karvon.users list
    python -m karvon.users password admin

Uses KARVON_DATABASE_URL, like the app.
"""

from __future__ import annotations

import argparse
import getpass
import sys

from sqlalchemy import select

from karvon import auth
from karvon.config import Settings
from karvon.db import make_engine, make_sessionmaker
from karvon.migrate import upgrade
from karvon.models import Role, User
from karvon.services import DomainError


def _password(prompt: str = "Password: ") -> str:
    first = getpass.getpass(prompt)
    if getpass.getpass("Repeat: ") != first:
        raise DomainError("mismatch", "Passwords do not match")
    return first


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m karvon.users")
    sub = parser.add_subparsers(dest="command", required=True)
    create = sub.add_parser("create", help="add a user")
    create.add_argument("username")
    create.add_argument("--role", choices=[r.value for r in Role], required=True)
    create.add_argument("--name", help="full name shown in the UI")
    sub.add_parser("list", help="list users")
    pw = sub.add_parser("password", help="set a new password")
    pw.add_argument("username")
    args = parser.parse_args(argv)

    engine = make_engine(Settings().database_url)
    upgrade(engine)
    try:
        with make_sessionmaker(engine)() as session, session.begin():
            if args.command == "create":
                auth.create_user(session, args.username, _password(), Role(args.role), args.name)
                print(f"Created {args.username} ({args.role})")
            elif args.command == "list":
                for u in session.scalars(select(User).order_by(User.username)):
                    state = "" if u.active else " (inactive)"
                    print(f"{u.username:<20} {u.role.value:<12} {u.full_name}{state}")
            else:
                user = session.scalar(select(User).where(User.username == args.username.lower()))
                if user is None:
                    raise DomainError("not_found", f"No user {args.username}")
                auth.set_password(session, user, _password("New password: "))
                print(f"Password changed for {user.username}")
    except DomainError as e:
        print(e.message, file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
