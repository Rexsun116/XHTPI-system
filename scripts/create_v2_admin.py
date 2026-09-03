#!/usr/bin/env python3
"""Explicit initial V2 admin creation; password is prompted, never stored."""

import argparse
import getpass
import os
from pathlib import Path
import sys

from werkzeug.security import generate_password_hash

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from v2.app import create_configured_app  # noqa: E402
from v2.models import User, db  # noqa: E402


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("username")
    args = parser.parse_args()
    password = getpass.getpass("New V2 admin password: ")
    confirm = getpass.getpass("Confirm password: ")
    if len(password) < 10 or password != confirm:
        raise SystemExit("Passwords must match and contain at least 10 characters")
    app = create_configured_app()
    with app.app_context():
        if db.session.scalar(db.select(User).where(User.username == args.username)):
            raise SystemExit("Username already exists")
        db.session.add(User(username=args.username, password_hash=generate_password_hash(password)))
        db.session.commit()
    print("V2 admin created")


if __name__ == "__main__":
    main()
