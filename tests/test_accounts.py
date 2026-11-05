import io
from html.parser import HTMLParser
import os
from pathlib import Path
import re
import secrets
import sqlite3
import tempfile
import unittest
from unittest.mock import patch

from flask import Flask
from werkzeug.security import check_password_hash

from app import app, get_db, seed_demo_data
from adapters.cheapshark import GameSearchResult
from migrations import upgrade_accounts
from security import admin_required, configure_security, init_security, login_required
from services.price_sync import sync_prices
from adapters.demo_price_source import DemoPriceSource


# Dummy credential for isolated in-memory tests, never a real user password.
PASSWORD = "Offline-Test-Password!"


class FormParser(HTMLParser):
    def __init__(self, html):
        super().__init__()
        self.forms = []
        self.current = None
        self.feed(html.decode())

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if tag == "form":
            self.current = {"method": attrs.get("method", "get").lower(), "inputs": {}}
            self.forms.append(self.current)
        if tag == "input" and self.current is not None:
            self.current["inputs"][attrs.get("name")] = attrs.get("value", "")

    def handle_endtag(self, tag):
        if tag == "form":
            self.current = None


class AccountsTest(unittest.TestCase):
    def setUp(self):
        self.previous_config = dict(app.config)
        app.config.update(TESTING=True, DATABASE=":memory:")
        self.ctx = app.app_context()
        self.ctx.push()
        self.runner = app.test_cli_runner()
        self.assertEqual(self.runner.invoke(args=["init-db"]).exit_code, 0)
        self.client = app.test_client()  # Raw client: no authentication/CSRF shortcut.

    def tearDown(self):
        self.ctx.pop()
        app.config.update(self.previous_config)

    def token(self, path="/login"):
        page = self.client.get(path)
        forms = FormParser(page.data).forms
        return next(form["inputs"]["csrf_token"] for form in forms if "csrf_token" in form["inputs"])

    def post(self, path, data=None, **kwargs):
        token = self.token("/register" if path == "/register" else "/login")
        return self.client.post(path, data={**(data or {}), "csrf_token": token}, **kwargs)

    def register(self, username="Alice", **extra):
        return self.post("/register", {"username": username, "password": PASSWORD, **extra})

    def login(self, username="Alice", password=PASSWORD, **extra):
        return self.post("/login", {"username": username, "password": password, **extra})

    def test_registration_hash_role_created_time_and_case_insensitive_duplicate(self):
        result = self.register(role="admin", user_id="1", id="1")
        self.assertEqual(result.status_code, 303)
        row = get_db().execute("SELECT * FROM users WHERE username_normalized='alice'").fetchone()
        self.assertEqual(row["role"], "user")
        self.assertEqual(row["username"], "Alice")
        self.assertNotEqual(row["id"], 1)
        self.assertTrue(row["created_at"])
        self.assertNotEqual(row["password_hash"], PASSWORD)
        self.assertNotIn(PASSWORD, row["password_hash"])
        self.assertTrue(check_password_hash(row["password_hash"], PASSWORD))
        duplicate = self.register("aLiCe")
        self.assertIn("已被使用".encode(), duplicate.data)
        self.assertNotIn(PASSWORD.encode(), duplicate.data)
        self.assertEqual(get_db().execute("SELECT COUNT(*) FROM users WHERE username_normalized='alice'").fetchone()[0], 1)
        with self.assertRaises(sqlite3.IntegrityError):
            with get_db() as db:
                db.execute("INSERT INTO users(display_name,username_normalized) VALUES ('Fake','ALICE')")

    def test_username_and_password_validation_and_reserved_demo(self):
        for username in ["ab", "9abc", "a-b", "用户名", "a" * 25]:
            with self.subTest(username=username):
                self.assertIn("用户名须为".encode(), self.register(username).data)
        for password in ["short", "a" * 7, " " * 8, "a" * 129]:
            self.assertIn("密码须为".encode(), self.register(password=password).data)
        self.assertIn("已被使用".encode(), self.register("DEMO").data)
        demo = get_db().execute("SELECT * FROM users WHERE id=1").fetchone()
        self.assertIsNone(demo["password_hash"])
        self.assertEqual(demo["role"], "user")
        self.assertIn("用户名或密码错误".encode(), self.login("demo").data)

    def test_eight_character_password_registers_and_logs_in(self):
        password = "Test123!"
        self.assertEqual(self.register(password=password).status_code, 303)
        self.assertEqual(self.login(password=password).status_code, 303)
        with self.client.session_transaction() as state:
            self.assertIsInstance(state.get("user_id"), int)

    def test_login_wrong_password_loads_identity_and_logout_clears_session(self):
        self.register()
        self.assertIn("用户名或密码错误".encode(), self.login(password="Wrong-Password!").data)
        before_token = self.token()
        with self.client.session_transaction() as state:
            state["last_game_search"] = "old search"
        result = self.client.post("/login", data={"username": "ALICE", "password": PASSWORD, "csrf_token": before_token})
        self.assertEqual(result.status_code, 303)
        with self.client.session_transaction() as state:
            self.assertIn("user_id", state)
            self.assertNotIn("last_game_search", state)
            self.assertNotEqual(state["_csrf_token"], before_token)
            self.assertNotIn("password", state)
        page = self.client.get("/games/1")
        self.assertIn(b"Alice", page.data)
        self.assertIn("退出".encode(), page.data)
        result = self.post("/logout")
        self.assertEqual(result.status_code, 303)
        with self.client.session_transaction() as state:
            self.assertEqual(dict(state), {})
        self.assertEqual(self.client.get("/logout").status_code, 405)

    def test_csrf_rejects_registration_login_logout_and_every_business_post(self):
        before = get_db().execute("SELECT COUNT(*) FROM users").fetchone()[0]
        self.assertEqual(self.client.post("/register", data={"username": "Mallory", "password": PASSWORD}).status_code, 400)
        self.assertEqual(get_db().execute("SELECT COUNT(*) FROM users").fetchone()[0], before)
        self.register()
        self.assertEqual(self.client.post("/login", data={"username": "Alice", "password": PASSWORD}).status_code, 400)
        self.login()
        before_price_target = get_db().execute("SELECT target_cents FROM price_targets WHERE game_id=1").fetchone()[0]
        with patch("adapters.cheapshark.urlopen", side_effect=AssertionError("CSRF must block API calls")):
            for path, data in [
                ("/games/1/target", {"target_price": "1.00"}),
                ("/games/track", {"cheapshark_game_id": "555"}),
                ("/games/untrack", {"steam_app_id": "413150"}),
                ("/logout", {}),
            ]:
                with self.subTest(path=path):
                    self.assertEqual(self.client.post(path, data={**data, "csrf_token": "invalid"}).status_code, 400)
        self.assertEqual(get_db().execute("SELECT target_cents FROM price_targets WHERE game_id=1").fetchone()[0], before_price_target)
        self.assertEqual(get_db().execute("SELECT is_tracked FROM games WHERE id=101").fetchone()[0], 1)
        with self.client.session_transaction() as state:
            self.assertIn("user_id", state)

    def test_all_rendered_post_forms_have_csrf_and_cookie_flags(self):
        response = self.client.get("/register")
        cookie = response.headers.get("Set-Cookie", "")
        self.assertIn("HttpOnly", cookie)
        self.assertIn("SameSite=Lax", cookie)
        self.assertNotIn("; Secure", cookie)
        for path in ["/register", "/login", "/games/1", "/games/101"]:
            for form in FormParser(self.client.get(path).data).forms:
                if form["method"] == "post":
                    self.assertTrue(form["inputs"].get("csrf_token"))
        self.register()
        self.login()
        for path in ["/games/1", "/games/101"]:
            for form in FormParser(self.client.get(path).data).forms:
                if form["method"] == "post":
                    self.assertTrue(form["inputs"].get("csrf_token"))
        with patch("app.CheapSharkSource.search_by_title", return_value=[
            GameSearchResult("150036", "Tracked", "413150", 1499),
            GameSearchResult("555", "New Game", "9999", 999),
        ]):
            forms = FormParser(self.client.get("/games/search?q=Game").data).forms
        post_forms = [form for form in forms if form["method"] == "post"]
        self.assertEqual(len(post_forms), 3)  # logout, cancel, add
        self.assertTrue(all(form["inputs"].get("csrf_token") for form in post_forms))

    def test_unsafe_next_is_ignored_and_safe_local_next_works(self):
        self.register()
        for destination in ["https://evil.invalid", "//evil.invalid", "/\\evil.invalid", "/%2fevil.invalid",
                            "/%255cevil.invalid", "/\n/evil.invalid", "javascript:alert(1)"]:
            with self.subTest(destination=destination):
                result = self.login(next=destination)
                self.assertEqual(result.status_code, 303)
                self.assertEqual(result.location, "/")
                self.post("/logout")
        self.assertEqual(self.login(next="/games/101").location, "/games/101")

    def test_login_required_and_admin_required_use_database_role(self):
        self.register()
        db = get_db()
        user_id = db.execute("SELECT id FROM users WHERE username_normalized='alice'").fetchone()[0]
        probe = Flask("access-control-test")
        probe.config.update(TESTING=True, SECRET_KEY=app.secret_key)
        probe.add_url_rule("/login", "login", lambda: "login")
        init_security(probe, lambda: db)
        probe.add_url_rule("/private", "private", login_required(lambda: "private"))
        probe.add_url_rule("/admin", "admin", admin_required(lambda: "admin"))
        client = probe.test_client()
        self.assertEqual(client.get("/private").status_code, 302)
        self.assertTrue(client.get("/admin").location.startswith("/login?next="))
        with client.session_transaction() as state:
            state["user_id"] = user_id
            state["role"] = "admin"  # Never trust a browser's role claim.
        self.assertEqual(client.get("/private").status_code, 200)
        self.assertEqual(client.get("/admin").status_code, 403)
        db.execute("UPDATE users SET role='admin' WHERE id=?", (user_id,))
        db.commit()
        self.assertEqual(client.get("/admin").status_code, 200)

    def test_anonymous_valid_csrf_write_redirects_to_login_without_mutation(self):
        token = self.token()
        result = self.client.post("/games/1/target", data={"target_price": "1.00", "csrf_token": token})
        self.assertEqual(result.status_code, 302)
        self.assertTrue(result.location.startswith("/login?next="))
        self.assertEqual(get_db().execute("SELECT target_cents FROM price_targets WHERE game_id=1").fetchone()[0], 10000)

    def test_admin_cli_hidden_password_create_promote_and_no_password_option(self):
        self.register()
        for username in ["root_admin", "ALICE"]:
            result = self.runner.invoke(args=["create-admin", "--username", username], input=PASSWORD + "\n" + PASSWORD + "\n")
            self.assertEqual(result.exit_code, 0, repr(result.exception))
            self.assertNotIn(PASSWORD, result.output)
            row = get_db().execute("SELECT * FROM users WHERE username_normalized=?", (username.lower(),)).fetchone()
            self.assertEqual(row["role"], "admin")
            self.assertTrue(check_password_hash(row["password_hash"], PASSWORD))
            self.assertTrue(row["created_at"])
        result = self.runner.invoke(args=["create-admin", "--username", "bad_admin"], input="short\nshort\n")
        self.assertNotEqual(result.exit_code, 0)
        self.assertIsNone(get_db().execute("SELECT id FROM users WHERE username_normalized='bad_admin'").fetchone())
        self.assertNotIn("--password", self.runner.invoke(args=["create-admin", "--help"]).output)


class AccountMigrationAndConfigTest(unittest.TestCase):
    def test_legacy_user_and_business_data_are_preserved_on_repeated_upgrade(self):
        root = Path(__file__).resolve().parents[1]
        schema = (root / "schema.sql").read_text(encoding="utf-8")
        schema = re.sub(r"CREATE TABLE users \(.*?\);", "CREATE TABLE users (id INTEGER PRIMARY KEY, display_name TEXT NOT NULL);", schema, flags=re.S)
        db = sqlite3.connect(":memory:")
        db.row_factory = sqlite3.Row
        db.execute("PRAGMA foreign_keys=ON")
        try:
            db.executescript(schema)
            db.executescript((root / "notifications.sql").read_text(encoding="utf-8"))
            seed_demo_data(db)
            db.commit()
            sync_prices(db, DemoPriceSource(), scenario="sequence")
            tables = ["games", "price_observations", "price_targets", "notifications", "sync_status"]
            before = {table: [tuple(row) for row in db.execute(f"SELECT * FROM {table} ORDER BY 1")] for table in tables}
            upgrade_accounts(db)
            first = [tuple(row) for row in db.execute("SELECT * FROM users ORDER BY id")]
            upgrade_accounts(db)
            self.assertEqual(first, [tuple(row) for row in db.execute("SELECT * FROM users ORDER BY id")])
            self.assertEqual(before, {table: [tuple(row) for row in db.execute(f"SELECT * FROM {table} ORDER BY 1")] for table in tables})
            demo = db.execute("SELECT * FROM users WHERE id=1").fetchone()
            self.assertEqual(demo["display_name"], "演示用户")
            self.assertEqual(demo["username_normalized"], "demo")
            self.assertIsNone(demo["password_hash"])
            self.assertTrue(demo["created_at"])
            with self.assertRaises(sqlite3.IntegrityError):
                with db:
                    db.execute("UPDATE users SET role='owner' WHERE id=1")
            self.assertEqual(db.execute("PRAGMA foreign_key_check").fetchall(), [])
        finally:
            db.close()

    def test_development_secret_is_persistent_and_production_requires_env_secret(self):
        root = Path(__file__).resolve().parents[1] / "instance"
        with tempfile.TemporaryDirectory(dir=root) as directory, patch.dict(os.environ, {}, clear=True):
            first = Flask("security-config-test", instance_path=directory)
            configure_security(first)
            second = Flask("security-config-test", instance_path=directory)
            configure_security(second)
            self.assertEqual(first.secret_key, second.secret_key)
            self.assertGreaterEqual(len(first.secret_key), 32)
            self.assertFalse(first.config["SESSION_COOKIE_SECURE"])
            os.environ["APP_ENV"] = "production"
            with self.assertRaises(RuntimeError):
                configure_security(second)
            os.environ["SECRET_KEY"] = secrets.token_hex(32)
            configure_security(second)
            self.assertEqual(second.secret_key, os.environ["SECRET_KEY"])
            self.assertTrue(second.config["SESSION_COOKIE_SECURE"])
            self.assertTrue(second.config["SESSION_COOKIE_HTTPONLY"])
            self.assertEqual(second.config["SESSION_COOKIE_SAMESITE"], "Lax")
