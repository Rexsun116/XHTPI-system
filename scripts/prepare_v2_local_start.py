#!/usr/bin/env python3
"""Read-only V2 preflight and verified SQLite online backup; never migrates."""
from contextlib import closing
from datetime import datetime, timezone
import hashlib
import os
from pathlib import Path
import sqlite3
import sys
import tempfile

from alembic.config import Config
from alembic.script import ScriptDirectory
from sqlalchemy.engine import make_url

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


class PreparationError(Exception):
    pass


def database_path(raw):
    try:
        url = make_url(raw or "")
    except Exception:
        raise PreparationError("Set an explicit absolute SQLite file URL.") from None
    if (url.drivername != "sqlite" or url.query or url.host or url.username
            or not url.database or url.database == ":memory:"
            or not Path(url.database).is_absolute()):
        raise PreparationError("Only sqlite:////absolute/path file URLs without options are accepted.")
    target = Path(url.database).resolve()
    reject_v1(target)
    if not target.is_file():
        raise PreparationError("Database must be an existing regular file.")
    return target


def reject_v1(target):
    v1 = (ROOT / "instance").resolve()
    if target == v1 or v1 in target.parents:
        raise PreparationError("Refusing the entire V1 instance directory.")


def validate(connection):
    if connection.execute("PRAGMA integrity_check").fetchall() != [("ok",)]:
        raise PreparationError("SQLite integrity_check failed.")
    if connection.execute("PRAGMA foreign_key_check").fetchall():
        raise PreparationError("SQLite foreign_key_check failed.")
    from v2.models import db
    # Read metadata only: no app creation, engine connection or schema mutation.
    for table in db.metadata.sorted_tables:
        columns = {row[1] for row in connection.execute(f'PRAGMA table_info("{table.name}")')}
        if not set(table.columns.keys()) <= columns:
            raise PreparationError("Required V2 tables/columns are missing.")
    revisions = {row[0] for row in connection.execute("SELECT version_num FROM alembic_version")}
    config = Config()
    config.set_main_option("script_location", str(ROOT / "migrations_v2"))
    if revisions != set(ScriptDirectory.from_config(config).get_heads()):
        raise PreparationError("V2 revision does not match the current migration head; no migration performed.")
    return ", ".join(sorted(revisions))


def prepare(raw):
    target = database_path(raw)
    print(f"V2 database: {target}", flush=True)
    partial = None
    try:
        with closing(sqlite3.connect(target.as_uri() + "?mode=ro", uri=True)) as source:
            source.execute("BEGIN")
            revision = validate(source)
            print(f"V2 revision: {revision}; integrity=ok; foreign_key_errors=0", flush=True)
            directory = (target.parent / "backups").resolve()
            reject_v1(directory)
            directory.mkdir(exist_ok=True)
            stamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S-%f")
            fd, name = tempfile.mkstemp(prefix=f"database-v2-{stamp}-", suffix=".partial", dir=directory)
            os.close(fd)
            partial = Path(name)
            with closing(sqlite3.connect(partial)) as destination:
                source.backup(destination)
            with closing(sqlite3.connect(partial.as_uri() + "?mode=ro", uri=True)) as backup:
                validate(backup)
            digest = hashlib.sha256(partial.read_bytes()).hexdigest()
            final = partial.with_suffix(".db")
            # Atomic publication without replacing any existing backup.
            os.link(partial, final)
            partial.unlink()
            partial = None
        print(f"V2 backup: {final}")
        print(f"Backup SHA-256: {digest}")
        return final
    finally:
        if partial is not None:
            partial.unlink(missing_ok=True)


def main():
    try:
        prepare(os.environ.get("XHTPI_V2_DATABASE_URL"))
    except PreparationError as exc:
        print(f"V2 startup refused: {exc}", file=sys.stderr)
        return 1
    except Exception:
        # Driver/OS exceptions can contain configuration values; do not print credentials.
        print("V2 startup refused: database/schema check or verified backup failed.", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
