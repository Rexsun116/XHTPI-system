#!/usr/bin/env python3
"""Create a fresh V2 DB from the isolated baseline; never targets V1 instance."""

import argparse
import os
from pathlib import Path
import sqlite3
import subprocess
import sys


ROOT = Path(__file__).resolve().parents[1]
REAL_V1 = (ROOT / "instance" / "database.db").resolve()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("database", type=Path)
    args = parser.parse_args()
    target = args.database.expanduser().resolve()
    print(f"V2 requested database path: {target}")
    if target == REAL_V1 or target.parent == REAL_V1.parent:
        raise SystemExit("Refusing V1 instance directory")
    if target.exists():
        raise SystemExit("Refusing to overwrite an existing database")
    target.parent.mkdir(parents=True, exist_ok=True)
    env = os.environ.copy()
    env["XHTPI_V2_DATABASE_URL"] = f"sqlite:///{target}"
    subprocess.run(
        [sys.executable, "-m", "alembic", "-c", "migrations_v2/alembic.ini", "upgrade", "head"],
        cwd=ROOT, env=env, check=True,
    )
    with sqlite3.connect(target) as conn:
        conn.execute("PRAGMA foreign_keys=ON")
        integrity = conn.execute("PRAGMA integrity_check").fetchone()[0]
        fk_errors = conn.execute("PRAGMA foreign_key_check").fetchall()
        revision = conn.execute("SELECT version_num FROM alembic_version").fetchone()[0]
    print(f"revision={revision} integrity={integrity} foreign_key_errors={len(fk_errors)}")
    if integrity != "ok" or fk_errors:
        raise SystemExit("V2 database verification failed")


if __name__ == "__main__":
    main()
