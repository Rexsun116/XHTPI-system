"""Isolated production-runtime and authentication safety coverage for V2."""

import os
from pathlib import Path
import subprocess
import sys
import sqlite3
from tempfile import TemporaryDirectory
from unittest import TestCase
from unittest.mock import patch

from werkzeug.middleware.proxy_fix import ProxyFix
from werkzeug.security import generate_password_hash

from v2.app import create_app
from v2.models import User, db


ROOT = Path(__file__).resolve().parents[1]


class ProductionRuntimeTest(TestCase):
    def setUp(self):
        self.temp = TemporaryDirectory(prefix="xhtpi-v2-runtime-")
        self.addCleanup(self.temp.cleanup)
        self.database = Path(self.temp.name) / "runtime.db"
        subprocess.run(
            [sys.executable, str(ROOT / "scripts" / "init_v2_test_db.py"), str(self.database)],
            cwd=ROOT, check=True, capture_output=True, text=True,
        )

    @property
    def url(self):
        return f"sqlite:///{self.database}"

    def test_production_config_uses_secure_cookie_and_one_proxy_hop(self):
        app = create_app(self.url, testing=False, secret_key="runtime-secret", production=True)
        self.assertFalse(app.debug)
        self.assertTrue(app.config["SESSION_COOKIE_SECURE"])
        self.assertTrue(app.config["SESSION_COOKIE_HTTPONLY"])
        self.assertEqual(app.config["SESSION_COOKIE_SAMESITE"], "Lax")
        self.assertEqual(app.config["PREFERRED_URL_SCHEME"], "https")
        self.assertIsInstance(app.wsgi_app, ProxyFix)
        self.assertEqual((app.wsgi_app.x_for, app.wsgi_app.x_proto), (1, 1))
        self.assertEqual((app.wsgi_app.x_host, app.wsgi_app.x_port, app.wsgi_app.x_prefix), (0, 0, 0))

    def test_non_testing_runtime_refuses_missing_secret(self):
        with patch.dict(os.environ, {"XHTPI_V2_ENV": "production"}, clear=True):
            with self.assertRaisesRegex(RuntimeError, "XHTPI_V2_SECRET_KEY is required"):
                create_app(self.url, testing=False, production=True)

    def test_wsgi_entrypoint_imports_with_explicit_production_environment(self):
        command = "from v2.wsgi import app; print(app.config['SESSION_COOKIE_SECURE'])"
        env = dict(os.environ, XHTPI_V2_DATABASE_URL=self.url,
                   XHTPI_V2_SECRET_KEY="runtime-secret", XHTPI_V2_ENV="production")
        result = subprocess.run([sys.executable, "-c", command], cwd=ROOT, env=env,
                                check=True, capture_output=True, text=True)
        self.assertEqual(result.stdout.strip(), "True")

    def test_module_entrypoint_refuses_flask_development_server_in_production(self):
        env = dict(os.environ, XHTPI_V2_DATABASE_URL=self.url,
                   XHTPI_V2_SECRET_KEY="runtime-secret", XHTPI_V2_ENV="production")
        result = subprocess.run([sys.executable, "-m", "v2.app"], cwd=ROOT, env=env,
                                capture_output=True, text=True)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("Production runtime must use scripts/run_v2_production.sh and Gunicorn", result.stderr)

    def test_inactive_user_is_rejected_and_active_user_still_logs_in(self):
        app = create_app(self.url, testing=True)
        with app.app_context():
            active = User(username="active", password_hash=generate_password_hash("password"), active=True)
            inactive = User(username="inactive", password_hash=generate_password_hash("password"), active=False)
            db.session.add_all((active, inactive))
            db.session.commit()
            inactive_id = inactive.id
        client = app.test_client()
        rejected = client.post("/login", data={"username": "inactive", "password": "password"})
        self.assertEqual(rejected.status_code, 401)
        accepted = client.post("/login", data={"username": "active", "password": "password"})
        self.assertEqual(accepted.status_code, 302)
        self.assertTrue(accepted.headers["Location"].endswith("/v2/"))

        with client.session_transaction() as session:
            session["_user_id"] = str(inactive_id)
            session["_fresh"] = True
        self.assertEqual(client.get("/v2/").status_code, 302)

    def test_logout_requires_csrf_and_clears_authenticated_session(self):
        app = create_app(self.url, testing=False, secret_key="runtime-secret", production=True)
        with app.app_context():
            user = User(username="logout-user", password_hash=generate_password_hash("password"))
            db.session.add(user); db.session.commit()
            user_id = user.id
        client = app.test_client()
        with client.session_transaction() as session:
            session["_user_id"] = str(user_id)
            session["_fresh"] = True
        page = client.get("/v2/", base_url="https://localhost")
        html = page.get_data(as_text=True)
        self.assertEqual(page.status_code, 200)
        self.assertIn('method="post" action="/logout"', html)
        self.assertIn('class="v2-shell-logout"', html)
        self.assertIn(">Sign out</span>", html)
        self.assertEqual(
            client.post("/logout", base_url="https://localhost", headers={"Referer": "https://localhost/v2/"}).status_code,
            400,
        )
        csrf_token = html.split('name="csrf_token" value="', 1)[1].split('"', 1)[0]
        response = client.post(
            "/logout", data={"csrf_token": csrf_token}, base_url="https://localhost",
            headers={"Referer": "https://localhost/v2/"},
        )
        self.assertEqual(response.status_code, 302)
        self.assertTrue(response.headers["Location"].endswith("/login"))
        self.assertEqual(client.get("/v2/").status_code, 302)

    def test_production_launcher_refuses_bad_database_before_gunicorn(self):
        missing = Path(self.temp.name) / "missing.db"
        env = dict(os.environ, XHTPI_V2_DATABASE_URL=f"sqlite:///{missing}",
                   XHTPI_V2_SECRET_KEY="runtime-secret", XHTPI_V2_ENV="production")
        result = subprocess.run(["sh", str(ROOT / "scripts" / "run_v2_production.sh")],
                                cwd=ROOT, env=env, capture_output=True, text=True)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("V2 startup refused", result.stderr)
        self.assertFalse(missing.exists())

    def test_production_launcher_refuses_existing_database_with_missing_schema(self):
        invalid = Path(self.temp.name) / "invalid-schema.db"
        sqlite3.connect(invalid).close()
        env = dict(os.environ, XHTPI_V2_DATABASE_URL=f"sqlite:///{invalid}",
                   XHTPI_V2_SECRET_KEY="runtime-secret", XHTPI_V2_ENV="production")
        result = subprocess.run(["sh", str(ROOT / "scripts" / "run_v2_production.sh")],
                                cwd=ROOT, env=env, capture_output=True, text=True)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("V2 startup refused", result.stderr)
        self.assertFalse((invalid.parent / "backups").exists())


class ProductionRuntimeSourceContractTest(TestCase):
    def test_production_launcher_preflights_before_launching_gunicorn(self):
        source = (ROOT / "scripts" / "run_v2_production.sh").read_text()
        self.assertLess(source.index("prepare_v2_local_start.py"), source.index("-m gunicorn"))
        self.assertIn('XHTPI_V2_GUNICORN_BIND:-127.0.0.1:5056', source)
        self.assertIn('XHTPI_V2_GUNICORN_WORKERS:-1', source)
        self.assertIn('v2.wsgi:app', source)

    def test_wsgi_module_exposes_only_the_configured_application(self):
        source = (ROOT / "v2" / "wsgi.py").read_text()
        self.assertIn("from .app import create_configured_app", source)
        self.assertIn("app = create_configured_app()", source)
        self.assertNotIn(".run(", source)
