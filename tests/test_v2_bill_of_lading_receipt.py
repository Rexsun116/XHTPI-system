"""Schema coverage for the B/L business receipt date."""
import os
from pathlib import Path
import sqlite3
import subprocess
import sys
import tempfile
from unittest import TestCase


ROOT = Path(__file__).resolve().parents[1]


class BillOfLadingReceiptMigrationTest(TestCase):
    def command(self, path, *args):
        env = os.environ | {"XHTPI_V2_DATABASE_URL": f"sqlite:///{path}"}
        return subprocess.run(
            [sys.executable, "-m", "alembic", "-c", "migrations_v2/alembic.ini", *args],
            cwd=ROOT, env=env, check=True, capture_output=True, text=True,
        )

    def test_v2_0009_adds_only_nullable_bl_receipt_date_and_downgrades_cleanly(self):
        with tempfile.TemporaryDirectory(prefix="xhtpi-v2-0009-") as directory:
            path = Path(directory) / "migration.db"
            self.command(path, "upgrade", "v2_0008")
            with sqlite3.connect(path) as connection:
                before = {row[1] for row in connection.execute("PRAGMA table_info(pi)")}
                self.assertNotIn("bill_of_lading_received_on", before)

            self.command(path, "upgrade", "v2_0009")
            with sqlite3.connect(path) as connection:
                columns = {row[1]: row for row in connection.execute("PRAGMA table_info(pi)")}
                self.assertEqual(connection.execute("SELECT version_num FROM alembic_version").fetchone()[0], "v2_0009")
                self.assertEqual((columns["bill_of_lading_received_on"][2], columns["bill_of_lading_received_on"][3]), ("DATE", 0))
                self.assertEqual(set(columns), before | {"bill_of_lading_received_on"})

            self.command(path, "downgrade", "v2_0008")
            with sqlite3.connect(path) as connection:
                self.assertNotIn("bill_of_lading_received_on", {row[1] for row in connection.execute("PRAGMA table_info(pi)")})
                self.assertEqual(connection.execute("PRAGMA integrity_check").fetchone()[0], "ok")
                self.assertEqual(connection.execute("PRAGMA foreign_key_check").fetchall(), [])
