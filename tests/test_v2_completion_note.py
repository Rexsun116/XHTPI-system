"""Schema and completion-flow coverage for the final completion note."""
import os
from pathlib import Path
import sqlite3
import subprocess
import sys
import tempfile
from unittest import TestCase


ROOT = Path(__file__).resolve().parents[1]


class CompletionNoteMigrationTest(TestCase):
    def command(self, path, *args):
        env = os.environ | {"XHTPI_V2_DATABASE_URL": f"sqlite:///{path}"}
        return subprocess.run(
            [sys.executable, "-m", "alembic", "-c", "migrations_v2/alembic.ini", *args],
            cwd=ROOT, env=env, check=True, capture_output=True, text=True,
        )

    def test_v2_0008_adds_only_nullable_completion_note_and_downgrades_cleanly(self):
        with tempfile.TemporaryDirectory(prefix="xhtpi-v2-0008-") as directory:
            path = Path(directory) / "migration.db"
            self.command(path, "upgrade", "v2_0007")
            with sqlite3.connect(path) as connection:
                before = {row[1] for row in connection.execute("PRAGMA table_info(pi)")}
                self.assertNotIn("completion_note", before)

            self.command(path, "upgrade", "v2_0008")
            with sqlite3.connect(path) as connection:
                columns = {row[1]: row for row in connection.execute("PRAGMA table_info(pi)")}
                self.assertEqual(connection.execute("SELECT version_num FROM alembic_version").fetchone()[0], "v2_0008")
                self.assertEqual((columns["completion_note"][2], columns["completion_note"][3]), ("TEXT", 0))
                self.assertEqual(set(columns), before | {"completion_note"})

            self.command(path, "downgrade", "v2_0007")
            with sqlite3.connect(path) as connection:
                columns = {row[1] for row in connection.execute("PRAGMA table_info(pi)")}
                self.assertNotIn("completion_note", columns)
                self.assertEqual(connection.execute("PRAGMA integrity_check").fetchone()[0], "ok")
                self.assertEqual(connection.execute("PRAGMA foreign_key_check").fetchall(), [])
