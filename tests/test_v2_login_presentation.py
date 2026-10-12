"""Standalone V2 login presentation and frozen authentication-contract coverage."""

from pathlib import Path
import subprocess
import sys
import tempfile
from unittest import TestCase

from werkzeug.security import generate_password_hash

from v2.app import create_app
from v2.models import User, db


class V2LoginPresentationTest(TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="xhtpi-v2-login-")
        database = Path(self.tmp.name) / "login.db"
        root = Path(__file__).resolve().parents[1]
        subprocess.run([sys.executable, str(root / "scripts" / "init_v2_test_db.py"), str(database)],
                       cwd=root, check=True, capture_output=True, text=True)
        self.app = create_app(f"sqlite:///{database}", testing=True)
        self.ctx = self.app.app_context()
        self.ctx.push()
        db.session.add(User(username="login-user", password_hash=generate_password_hash("password")))
        db.session.commit()

    def tearDown(self):
        db.session.remove()
        self.ctx.pop()
        self.tmp.cleanup()

    def test_login_is_a_standalone_accessible_shared_design_system_shell(self):
        response = self.app.test_client().get("/login")
        self.assertEqual(response.status_code, 200)
        page = response.get_data(as_text=True)
        self.assertIn('class="v2-login"', page)
        self.assertIn("v2_design_system.css", page)
        self.assertIn("XHTPI", page)
        self.assertIn(">V2<", page)
        self.assertIn('class="v2-login-brand"', page)
        self.assertIn("西合钛贸易管理系统", page)
        self.assertIn("Trade Order &amp; Operations Management", page)
        self.assertIn("2026 西合钛贸易管理系统 Beta | Powered by RexSun", page)
        self.assertIn('class="v2-login-greeting" data-login-greeting>Welcome</p>', page)
        self.assertIn("Welcome to XHTPI", page)
        self.assertIn("Sign in to continue to your trade operations workspace.", page)
        self.assertIn('hour >= 5 && hour < 12', page)
        self.assertIn('"Good morning"', page)
        self.assertIn('"Good afternoon"', page)
        self.assertIn('"Good evening"', page)
        self.assertNotIn("Local Trial", page)
        self.assertNotIn('class="v2-shell"', page)
        self.assertNotIn('class="side"', page)
        self.assertIn('<form method="post" class="v2-login-form">', page)
        self.assertIn('name="csrf_token"', page)
        self.assertIn('for="login-username"', page)
        self.assertIn('name="username" autocomplete="username"', page)
        self.assertIn('for="login-password"', page)
        self.assertIn('type="password" name="password" autocomplete="current-password"', page)
        self.assertIn('class="v2-button v2-button--primary v2-login-submit"', page)
        self.assertNotIn("remember", page.lower())
        self.assertNotIn("logout", page.lower())

    def test_valid_credentials_still_redirect_to_the_v2_dashboard(self):
        response = self.app.test_client().post(
            "/login", data={"username": "login-user", "password": "password"}
        )
        self.assertEqual(response.status_code, 302)
        self.assertTrue(response.headers["Location"].endswith("/v2/"))

    def test_invalid_credentials_still_return_401_with_the_existing_visible_error(self):
        response = self.app.test_client().post(
            "/login", data={"username": "login-user", "password": "incorrect"}
        )
        self.assertEqual(response.status_code, 401)
        page = response.get_data(as_text=True)
        self.assertIn("Invalid credentials", page)
        self.assertIn('class="v2-login-error" role="alert"', page)
        self.assertIn('name="username" autocomplete="username"', page)
        self.assertIn('name="password" autocomplete="current-password"', page)

    def test_protected_v2_routes_still_redirect_to_login(self):
        response = self.app.test_client().get("/v2/")
        self.assertEqual(response.status_code, 302)
        self.assertIn("/login", response.headers["Location"])


class V2LoginDesignSystemSourceTest(TestCase):
    def test_login_css_is_scoped_and_responsive(self):
        root = Path(__file__).resolve().parents[1]
        css = (root / "v2" / "static" / "v2_design_system.css").read_text()
        self.assertIn("body.v2-login", css)
        self.assertIn(".v2-login-shell", css)
        self.assertIn(".v2-login-brand", css)
        self.assertIn(".v2-login-workspace", css)
        self.assertIn(".v2-login-card", css)
        self.assertIn("@media(max-width:800px)", css)
        self.assertIn("@media(max-width:575.98px){.v2-login-shell", css)
        login_css = css.split("/* Standalone authentication shell.", 1)[1].split(
            "/* Shared Shell 2.0", 1)[0]
        self.assertIn("var(--v2-color-primary)", login_css)
        self.assertIn("--v2-login-navy:#203b54", login_css)
        self.assertIn("#f7f6f2", login_css)
        self.assertNotIn("--v35-", login_css)
        self.assertNotIn("overflow-x:hidden", css)
