"""Startup safety against disposable, migration-built SQLite files only."""
from contextlib import closing
import hashlib
import os
from pathlib import Path
import shutil
import sqlite3
import subprocess
import sys
import tempfile
from unittest import TestCase
from unittest.mock import patch

from scripts import prepare_v2_local_start as safety


class LocalStartSafetyTest(TestCase):
    @classmethod
    def setUpClass(cls):
        cls.seed_dir = tempfile.TemporaryDirectory()
        cls.seed = Path(cls.seed_dir.name) / 'seed.db'
        subprocess.run([sys.executable, str(safety.ROOT / 'scripts/init_v2_test_db.py'), str(cls.seed)],
                       check=True, capture_output=True)

    @classmethod
    def tearDownClass(cls):
        cls.seed_dir.cleanup()

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.database = self.root / 'database.db'
        shutil.copyfile(self.seed, self.database)
        self.url = f'sqlite:///{self.database}'

    def execute(self, sql):
        with closing(sqlite3.connect(self.database)) as conn:
            conn.executescript(sql)
            conn.commit()

    def failed(self, url=None):
        with patch.dict(os.environ, {'XHTPI_V2_DATABASE_URL': url or self.url}):
            self.assertNotEqual(safety.main(), 0)
        directory = self.root / 'backups'
        self.assertFalse(directory.exists() and list(directory.iterdir()))

    def test_valid_backup_standalone_and_unique_without_source_changes(self):
        before = hashlib.sha256(self.database.read_bytes()).hexdigest()
        first = safety.prepare(self.url)
        second = safety.prepare(self.url)
        self.assertNotEqual(first, second)
        for backup in (first, second):
            with closing(sqlite3.connect(backup)) as conn:
                self.assertEqual(conn.execute('PRAGMA integrity_check').fetchall(), [('ok',)])
                self.assertEqual(conn.execute('PRAGMA foreign_key_check').fetchall(), [])
                self.assertTrue(safety.validate(conn))
        self.assertEqual(before, hashlib.sha256(self.database.read_bytes()).hexdigest())

    def test_wal_commits_included(self):
        with closing(sqlite3.connect(self.database)) as conn:
            conn.execute('PRAGMA journal_mode=WAL')
            conn.execute('CREATE TABLE backup_probe (value TEXT)')
            conn.execute("INSERT INTO backup_probe VALUES ('committed')")
            conn.commit()
            backup = safety.prepare(self.url)
            with closing(sqlite3.connect(backup)) as copy:
                self.assertEqual(copy.execute('SELECT value FROM backup_probe').fetchone(), ('committed',))

    def test_corrupt_source(self):
        self.database.write_bytes(b'not sqlite')
        self.failed()

    def test_foreign_key_violation(self):
        self.execute('CREATE TABLE bad (id INTEGER REFERENCES pi(id)); INSERT INTO bad VALUES (999);')
        self.failed()

    def test_missing_revision(self):
        self.execute('DROP TABLE alembic_version;')
        self.failed()

    def test_wrong_revision(self):
        self.execute("UPDATE alembic_version SET version_num='unknown';")
        self.failed()

    def test_missing_schema(self):
        self.execute('DROP TABLE task_activity;')
        self.failed()

    def test_missing_database(self):
        self.database.unlink()
        self.failed()
        self.assertFalse(self.database.exists())

    def test_directory(self):
        self.failed(f'sqlite:///{self.root}')

    def test_memory_and_ambiguous_urls(self):
        for raw in ('sqlite:///:memory:', 'sqlite://', 'sqlite:///relative.db',
                    'postgresql://name:secret@host/db', self.url + '?mode=rw'):
            with self.subTest(raw=raw):
                self.failed(raw)

    def test_v1_directory_and_symlink_rejected_before_open(self):
        # Fake repository root under temp: never inspect the real V1 database.
        fake = self.root / 'repo'
        (fake / 'instance/nested').mkdir(parents=True)
        for relative in ('database.db', 'other.db', 'nested/another.db'):
            target = fake / 'instance' / relative
            target.write_bytes(b'protected')
            with patch.object(safety, 'ROOT', fake), patch.object(safety.sqlite3, 'connect') as connect:
                self.failed(f'sqlite:///{target}')
                connect.assert_not_called()
        alias = self.root / 'alias.db'
        alias.symlink_to(fake / 'instance/database.db')
        with patch.object(safety, 'ROOT', fake):
            self.failed(f'sqlite:///{alias}')

    def test_unopenable_source(self):
        with patch.object(safety.sqlite3, 'connect', side_effect=sqlite3.OperationalError('private')):
            self.failed()

    def test_backup_directory_failure(self):
        (self.root / 'backups').write_text('occupied')
        with patch.dict(os.environ, {'XHTPI_V2_DATABASE_URL': self.url}):
            self.assertNotEqual(safety.main(), 0)
        self.assertEqual((self.root / 'backups').read_text(), 'occupied')

    def test_backup_validation_failure_cleans_partial(self):
        original = safety.validate
        calls = []
        def validate(conn):
            calls.append(True)
            if len(calls) == 2:
                raise safety.PreparationError('injected backup failure')
            return original(conn)
        with patch.object(safety, 'validate', side_effect=validate):
            self.failed()

    def test_existing_backup_cannot_be_overwritten(self):
        existing = self.root / 'preserve.db'
        existing.write_bytes(b'preserve')
        with patch.object(safety.os, 'link', side_effect=FileExistsError):
            self.failed()
        self.assertEqual(existing.read_bytes(), b'preserve')

    def test_launcher_gates_app_and_prefers_project_interpreter(self):
        fake = self.root / 'launcher'
        (fake / 'scripts').mkdir(parents=True)
        launcher = fake / 'scripts/run_v2_local.sh'
        shutil.copyfile(safety.ROOT / 'scripts/run_v2_local.sh', launcher)
        log = fake / 'calls'
        stub = '''#!/bin/sh
printf '%s\\n' "$*" >> "$CALL_LOG"
case "$1" in
  *prepare_v2_local_start.py) exit "$PREPARE_RESULT" ;;
  -m) test "$2" = v2.app ;;
  *) exit 99 ;;
esac
'''
        env = dict(os.environ, XHTPI_V2_DATABASE_URL=self.url, XHTPI_V2_SECRET_KEY='test-only',
                   CALL_LOG=str(log), PREPARE_RESULT='1')
        def run():
            return subprocess.run(['sh', str(launcher)], env=env, capture_output=True)
        self.assertNotEqual(run().returncode, 0)  # no unknown interpreter fallback
        for directory in ('venv', '.venv'):
            binary = fake / directory / 'bin/python'
            binary.parent.mkdir(parents=True)
            binary.write_text(stub)
            binary.chmod(0o700)
        self.assertNotEqual(run().returncode, 0)
        self.assertNotIn('-m v2.app', log.read_text())
        env['PREPARE_RESULT'] = '0'
        (fake / 'venv/bin/python').write_text('#!/bin/sh\nexit 98\n')
        self.assertEqual(run().returncode, 0)  # .venv preferred
        self.assertEqual(log.read_text().splitlines()[-1], '-m v2.app')
        self.assertIn('prepare_v2_local_start.py', log.read_text().splitlines()[-2])
        (fake / '.venv/bin/python').unlink()
        (fake / 'venv/bin/python').write_text(stub)
        self.assertEqual(run().returncode, 0)  # venv fallback
        for key in ('XHTPI_V2_DATABASE_URL', 'XHTPI_V2_SECRET_KEY'):
            saved = env.pop(key)
            self.assertNotEqual(run().returncode, 0)
            env[key] = saved
