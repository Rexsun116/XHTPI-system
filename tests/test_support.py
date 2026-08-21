"""One isolated migrated SQLite database shared by the unittest process."""

import atexit
import os
from pathlib import Path
import subprocess
import sys
import tempfile


TEST_ROOT = tempfile.TemporaryDirectory(prefix="xhtpi-tests-")
atexit.register(TEST_ROOT.cleanup)
TEST_DATABASE = Path(TEST_ROOT.name) / "tests.db"
PROJECT_ROOT = Path(__file__).resolve().parents[1]

subprocess.run(
    [
        sys.executable,
        str(PROJECT_ROOT / "scripts/init_test_db.py"),
        str(TEST_DATABASE),
        "--method",
        "migrations",
    ],
    cwd=PROJECT_ROOT,
    check=True,
    capture_output=True,
    text=True,
)
os.environ["XHTPI_DATABASE_URI"] = f"sqlite:///{TEST_DATABASE}"
os.environ["XHTPI_ENABLE_SQLITE_FOREIGN_KEYS"] = "1"
