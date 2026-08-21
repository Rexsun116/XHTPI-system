#!/usr/bin/env python3
"""Create an isolated empty SQLite database for model or migration rehearsal."""

import argparse
import os
from pathlib import Path
import sqlite3
import sys


PROJECT_ROOT = Path(__file__).resolve().parents[1]
REAL_DATABASE = (PROJECT_ROOT / "instance" / "database.db").resolve()


def parse_args():
    parser = argparse.ArgumentParser(
        description="Initialize a new, empty XHTPI test database. Existing files are never overwritten."
    )
    parser.add_argument("path", type=Path, help="Path for a new temporary SQLite database")
    parser.add_argument(
        "--method",
        choices=("models", "migrations"),
        default="models",
        help="Create from current model metadata or rehearse the Alembic migration chain",
    )
    return parser.parse_args()


def validate_target(target):
    if target == REAL_DATABASE:
        raise SystemExit("Refusing to use the real business database.")
    if target.is_relative_to(PROJECT_ROOT):
        raise SystemExit("Refusing to create a test database inside the Git workspace.")
    if target.exists():
        raise SystemExit(f"Refusing to overwrite existing file: {target}")
    if not target.parent.is_dir():
        raise SystemExit(f"Target directory does not exist: {target.parent}")


def initialize_from_models(target):
    os.environ["XHTPI_DATABASE_URI"] = f"sqlite:///{target}"
    os.environ["XHTPI_ENABLE_SQLITE_FOREIGN_KEYS"] = "1"
    sys.path.insert(0, str(PROJECT_ROOT))

    from app import app, db

    with app.app_context():
        db.create_all()


def initialize_from_migrations(target):
    print(f"requested_database={target}")
    sys.path.insert(0, str(PROJECT_ROOT / "scripts"))
    from rehearse_migration import run

    run("upgrade", target, "head")


def verify_database(target, method):
    uri = f"file:{target}?mode=ro"
    with sqlite3.connect(uri, uri=True) as connection:
        connection.execute("PRAGMA foreign_keys=ON")
        integrity = connection.execute("PRAGMA integrity_check").fetchone()[0]
        foreign_keys = connection.execute("PRAGMA foreign_keys").fetchone()[0]
        tables = {
            row[0]
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'"
            )
        }
    required = {"pi", "pi_item", "customer", "exporter", "factory", "user"}
    missing = sorted(required - tables)
    if integrity != "ok" or foreign_keys != 1 or missing:
        raise SystemExit(
            f"Verification failed: integrity={integrity}, foreign_keys={foreign_keys}, missing={missing}"
        )
    print(f"created={target}")
    print(f"method={method}")
    print(f"integrity={integrity}")
    print(f"foreign_keys={foreign_keys}")
    print(f"tables={len(tables)}")


if __name__ == "__main__":
    args = parse_args()
    target_path = args.path.expanduser().resolve()
    validate_target(target_path)
    if args.method == "models":
        initialize_from_models(target_path)
    else:
        initialize_from_migrations(target_path)
    verify_database(target_path, args.method)
