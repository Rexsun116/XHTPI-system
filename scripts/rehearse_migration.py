#!/usr/bin/env python3
"""Run an Alembic upgrade/downgrade against one explicit disposable database.

The command refuses the project's real instance directory unless the caller adds
``--allow-production``.  Phase rehearsals must never use that override.
"""

import argparse
import os
from pathlib import Path
import sqlite3
import sys


PROJECT_ROOT = Path(__file__).resolve().parents[1]
INSTANCE_DIR = (PROJECT_ROOT / "instance").resolve()
REAL_DATABASE = (INSTANCE_DIR / "database.db").resolve()


def parse_args():
    parser = argparse.ArgumentParser(
        description="Run a migration only after verifying the actual SQLite target path."
    )
    parser.add_argument("action", choices=("upgrade", "downgrade"))
    parser.add_argument("database", type=Path)
    parser.add_argument("revision", help="Explicit Alembic target revision")
    parser.add_argument(
        "--allow-production",
        action="store_true",
        help="Explicitly allow a target in the real instance directory.",
    )
    return parser.parse_args()


def validate_target(target, allow_production):
    target = target.expanduser().resolve()
    print(f"requested_database={target}")
    if not target.exists():
        raise SystemExit("Refusing migration: target database does not exist.")
    if target.is_dir():
        raise SystemExit("Refusing migration: target is a directory.")
    if target == REAL_DATABASE or target.is_relative_to(INSTANCE_DIR):
        if not allow_production:
            raise SystemExit(
                "Refusing migration: target is inside the real instance directory. "
                "Use --allow-production only after an explicit production upgrade approval."
            )
    return target


def current_revision(target):
    try:
        with sqlite3.connect(f"file:{target}?mode=ro", uri=True) as connection:
            row = connection.execute("SELECT version_num FROM alembic_version").fetchone()
    except sqlite3.OperationalError:
        return None
    return row[0] if row else None


def run(action, target, revision):
    os.environ["XHTPI_DATABASE_URI"] = f"sqlite:///{target}"
    os.environ["XHTPI_ENABLE_SQLITE_FOREIGN_KEYS"] = "1"
    sys.path.insert(0, str(PROJECT_ROOT))

    from alembic.autogenerate import compare_metadata
    from alembic.migration import MigrationContext
    from app import app, db
    from flask_migrate import downgrade, upgrade

    with app.app_context():
        configured = Path(db.engine.url.database).expanduser().resolve()
        print(f"configured_database={configured}")
        if configured != target:
            raise SystemExit(
                f"Refusing migration: configured database {configured} does not match {target}."
            )
        print(f"before_revision={current_revision(target)}")
        command = upgrade if action == "upgrade" else downgrade
        command(directory=str(PROJECT_ROOT / "migrations"), revision=revision)
        print(f"after_revision={current_revision(target)}")
        with db.engine.connect() as connection:
            differences = compare_metadata(
                MigrationContext.configure(connection), db.metadata
            )
        print(f"metadata_diff_count={len(differences)}")
        if differences:
            print(f"metadata_diff={differences}")
    with sqlite3.connect(f"file:{target}?mode=ro", uri=True) as connection:
        print(f"integrity_check={connection.execute('PRAGMA integrity_check').fetchone()[0]}")


if __name__ == "__main__":
    args = parse_args()
    database = validate_target(args.database, args.allow_production)
    run(args.action, database, args.revision)
