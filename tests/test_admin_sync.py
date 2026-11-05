"""Offline administrator actions, parameter parity, audit and concurrent replay."""
import ast
from concurrent.futures import ThreadPoolExecutor
import io
import json
from pathlib import Path
import sqlite3
import tempfile
from threading import Barrier
import unittest
from unittest.mock import patch
from urllib.error import HTTPError

from flask import render_template

from app import app, get_db
from adapters.demo_price_source import validate_scenario
from services.sync_inputs import prepare_demo_sync
from tests.helpers import TEST_HASH


class Response(io.BytesIO):
    def __init__(self, payload):
        super().__init__(json.dumps(payload).encode())
        self.headers = {}


class AdminSyncTest(unittest.TestCase):
    def setUp(self):
        self.previous_config = dict(app.config)
        self.temp = tempfile.TemporaryDirectory(dir=Path(app.instance_path))
        self.database = Path(self.temp.name) / "admin-test.sqlite3"
        app.config.update(TESTING=True, DATABASE=self.database)
        self.ctx = app.app_context()
        self.ctx.push()
        self.runner = app.test_cli_runner()
        self.assertEqual(self.runner.invoke(args=["init-db"]).exit_code, 0)
        with get_db() as db:
            self.admin_id = db.execute("""INSERT INTO users(display_name,username,username_normalized,password_hash,role)
                VALUES ('AdminTest','AdminTest','admintest',?,'admin')""", (TEST_HASH,)).lastrowid
            self.user_id = db.execute("""INSERT INTO users(display_name,username,username_normalized,password_hash,role)
                VALUES ('NormalUser','NormalUser','normaluser',?,'user')""", (TEST_HASH,)).lastrowid
            db.execute("INSERT INTO user_game_tracking(user_id,game_id) VALUES (?,101)", (self.user_id,))
            db.execute("INSERT INTO price_targets(user_id,game_id,target_cents) VALUES (?,101,10000)", (self.user_id,))
        self.admin = self.client(self.admin_id)
        self.normal = self.client(self.user_id)
        self.network = patch("adapters.cheapshark.urlopen", side_effect=AssertionError("Offline tests only"))
        self.network.start()
        self.addCleanup(self.network.stop)

    def tearDown(self):
        self.ctx.pop()
        app.config.update(self.previous_config)
        self.temp.cleanup()

    def client(self, user_id=None):
        client = app.test_client()
        if user_id is not None:
            with client.session_transaction() as state:
                state["user_id"] = user_id
        return client

    def token(self, client):
        client.get("/login")
        with client.session_transaction() as state:
            return state["_csrf_token"]

    def post(self, path, data=None, client=None, **kwargs):
        client = client or self.admin
        if path == "/admin/sync/demo":
            data = {"game_id": "1", **(data or {})}
        return client.post(path, data={**(data or {}), "csrf_token": self.token(client)}, **kwargs)

    def demo(self, scenario="price-90", event_id="admin-demo", **kwargs):
        return self.post("/admin/sync/demo", {"scenario": scenario, "event_id": event_id}, **kwargs)

    def quote(self, price="128.00", changed=100):
        return [{"storeID": "1", "steamAppID": "413150", "salePrice": price, "lastChange": changed}]

    def cheap(self, price="128.00", changed=100):
        with patch("adapters.cheapshark.urlopen", side_effect=lambda *args, **kwargs: Response(self.quote(price, changed))) as opener:
            response = self.post("/admin/sync/cheapshark")
        return response, opener

    def logs(self):
        return get_db().execute("SELECT * FROM admin_action_log ORDER BY id").fetchall()

    def business_records(self):
        return {table: [tuple(row) for row in get_db().execute(f"SELECT * FROM {table} ORDER BY 1,2")]
                for table in ["users", "games", "user_game_tracking", "price_observations", "price_targets",
                              "notifications", "notification_retries"]}

    def test_access_controls_and_navigation_use_database_role(self):
        anonymous = self.client()
        response = anonymous.get("/admin/sync")
        self.assertEqual(response.status_code, 302)
        self.assertIn("next=/admin/sync", response.location)
        self.assertEqual(self.normal.get("/admin/sync").status_code, 403)
        for path in ["/admin/sync/demo", "/admin/sync/cheapshark"]:
            self.assertEqual(self.post(path, client=anonymous).status_code, 302)
            self.assertEqual(self.post(path, client=self.normal).status_code, 403)
            self.assertEqual(self.admin.get(path).status_code, 405)
        with self.normal.session_transaction() as state:
            state["role"] = "admin"
        self.assertEqual(self.normal.get("/admin/sync").status_code, 403)
        self.assertIn('href="/admin/sync"', self.admin.get("/games/1").get_data(as_text=True))
        self.assertNotIn('href="/admin/sync"', self.normal.get("/games/1").get_data(as_text=True))
        self.assertEqual(self.logs(), [])

    def test_overview_shows_both_global_sources_counts_and_no_cache(self):
        self.cheap()
        self.demo()
        with patch("admin_sync.render_template", wraps=render_template) as renderer:
            response = self.admin.get("/admin/sync")
        self.assertEqual(response.status_code, 200)
        page = response.get_data(as_text=True)
        for text in ["最近尝试", "最近成功", "最近错误摘要", "已追踪游戏（去重）", "历史记录",
                     "模拟数据 / USD", "CheapShark / Steam / USD", "最近 20 次", "AdminTest"]:
            self.assertIn(text, page)
        self.assertIn("no-store", response.headers["Cache-Control"])
        self.assertIn('name="csrf_token"', page)
        self.assertIn('name="event_id"', page)
        self.assertNotIn('name="command"', page)
        self.assertNotIn("init-db", page)
        sources = renderer.call_args.kwargs["sources"]
        self.assertEqual([(item["tracked_games"], item["history_count"]) for item in sources], [(4, 21), (1, 1)])

    def test_admin_demo_success_replay_and_post_redirect_get(self):
        first = self.demo()
        self.assertEqual(first.status_code, 303)
        self.assertTrue(first.location.endswith("/admin/sync"))
        after = self.business_records()
        for _ in range(2):
            self.assertEqual(self.admin.get(first.location).status_code, 200)
        self.assertEqual(len(self.logs()), 1)
        self.assertEqual(self.demo().status_code, 303)
        self.assertEqual(after, self.business_records())
        self.assertEqual(len(self.logs()), 2)
        self.assertIn("新增历史 0 条", self.logs()[-1]["summary"])
        for row in self.logs():
            self.assertEqual((row["user_id"], row["action_type"], row["source"], row["outcome"]),
                             (self.admin_id, "sync_demo", "demo", "success"))
            self.assertTrue(row["created_at"])
            self.assertTrue(row["finished_at"])

    def test_conflicting_event_price_is_rejected_and_audited(self):
        self.demo("price-90", "same-event")
        before = self.business_records()
        status = tuple(get_db().execute("SELECT * FROM sync_status WHERE source_name='demo-steam-usd'").fetchone())
        self.demo("price-80", "same-event")
        self.assertEqual(before, self.business_records())
        self.assertEqual(status, tuple(get_db().execute("SELECT * FROM sync_status WHERE source_name='demo-steam-usd'").fetchone()))
        self.assertEqual(self.logs()[-1]["outcome"], "failure")

    def test_web_only_accepts_shared_integer_scenario_and_event_id_rules(self):
        invalid = ["price--1", "price-1.5", "price-1e2", "price-2147483648", "price-999999999999999",
                   "price-", "price-１２", "price-1;whoami", "price-drop", "late", "failure", "sequence", ""]
        before = self.business_records()
        for scenario in invalid:
            with self.subTest(scenario=scenario):
                self.assertEqual(self.demo(scenario, "valid-id").status_code, 303)
                self.assertEqual(self.logs()[-1]["outcome"], "failure")
                self.assertEqual(before, self.business_records())
        for event_id in ["", "a" * 81, "x y", "x;y", "中文", "x\ny"]:
            with self.subTest(event_id=event_id):
                self.demo("price-90", event_id)
                self.assertEqual(self.logs()[-1]["outcome"], "failure")
                self.assertEqual(before, self.business_records())
        self.post("/admin/sync/demo", {"scenario": "price-90"})
        self.assertEqual(self.logs()[-1]["outcome"], "failure")
        self.assertEqual(before, self.business_records())

    def test_zero_and_int_max_are_integer_cents(self):
        for price in ["0", "2147483647"]:
            self.demo("price-" + price, "boundary-" + price)
            row = get_db().execute("SELECT price_cents,typeof(price_cents) FROM price_observations WHERE source_event_id=?", ("demo-manual-boundary-" + price,)).fetchone()
            self.assertEqual(tuple(row), (int(price) * 100, "integer"))
            self.assertEqual(self.logs()[-1]["outcome"], "success")

    def test_cli_and_web_share_validation_and_event_identity(self):
        with patch("services.sync_inputs.validate_scenario", wraps=validate_scenario) as validator:
            self.demo("price-90", "shared-path")
            result = self.runner.invoke(args=["sync-prices", "--scenario", "price-90", "--event-id", "shared-path"])
        self.assertEqual(result.exit_code, 0, result.output)
        self.assertGreaterEqual(validator.call_count, 2)
        self.assertIn("新增 0 条", result.output)
        self.assertEqual(get_db().execute("SELECT COUNT(*) FROM price_observations WHERE source_event_id='demo-manual-shared-path'").fetchone()[0], 1)

    def test_cheapshark_calls_adapter_service_and_notifies_other_user(self):
        first, opener = self.cheap()
        self.assertEqual(first.status_code, 303)
        self.assertEqual(opener.call_count, 1)
        self.assertEqual(opener.call_args.kwargs["timeout"], 10)
        self.assertIn("GamePriceTracker", opener.call_args.args[0].headers["User-agent"])
        self.cheap("90.00", 101)
        self.assertEqual(get_db().execute("SELECT COUNT(*) FROM notifications WHERE user_id=? AND game_id=101", (self.user_id,)).fetchone()[0], 1)
        self.assertEqual(get_db().execute("SELECT COUNT(*) FROM notifications WHERE user_id=?", (self.admin_id,)).fetchone()[0], 0)
        before = self.business_records()
        self.cheap("90.00", 101)
        self.assertEqual(before, self.business_records())
        self.assertIn("新增历史 0 条", self.logs()[-1]["summary"])

    def test_api_failure_preserves_prices_success_time_and_public_pages(self):
        self.cheap()
        before = self.business_records()
        success_at = get_db().execute("SELECT last_success_at FROM sync_status WHERE source_name='cheapshark-steam-usd'").fetchone()[0]
        limited = HTTPError("https://example.invalid", 429, "limited", {"Retry-After": "30"}, None)
        try:
            with patch("adapters.cheapshark.urlopen", side_effect=limited):
                response = self.post("/admin/sync/cheapshark", follow_redirects=True)
        finally:
            limited.close()
        self.assertIn("429", response.get_data(as_text=True))
        self.assertIn("旧价格和上次成功同步时间已保留", response.get_data(as_text=True))
        self.assertEqual(before, self.business_records())
        self.assertEqual(success_at, get_db().execute("SELECT last_success_at FROM sync_status WHERE source_name='cheapshark-steam-usd'").fetchone()[0])
        self.assertEqual(self.logs()[-1]["outcome"], "failure")
        self.assertEqual(self.normal.get("/games/101").status_code, 200)
        self.assertEqual(self.client().get("/games/101").status_code, 200)

    def test_timeout_and_malformed_response_are_friendly_audited_failures(self):
        for failure in [TimeoutError("not-public"), Response({"bad": "SECRET_RESPONSE"})]:
            with self.subTest(failure=type(failure).__name__):
                if isinstance(failure, Exception):
                    mock = patch("adapters.cheapshark.urlopen", side_effect=failure)
                else:
                    mock = patch("adapters.cheapshark.urlopen", return_value=failure)
                with mock:
                    response = self.post("/admin/sync/cheapshark", follow_redirects=True)
                self.assertEqual(response.status_code, 200)
                self.assertNotIn("SECRET_RESPONSE", response.get_data(as_text=True))
                self.assertNotIn("not-public", response.get_data(as_text=True))
                self.assertEqual(self.logs()[-1]["outcome"], "failure")

    def test_csrf_failure_blocks_all_actions_before_service_or_audit(self):
        before = self.business_records()
        with patch("admin_sync.sync_prices", side_effect=AssertionError("Must not execute")):
            for path in ["/admin/sync/demo", "/admin/sync/cheapshark"]:
                self.assertEqual(self.admin.post(path, data={"csrf_token": "bad", "scenario": "price-90", "event_id": "csrf"}).status_code, 400)
        self.assertEqual(before, self.business_records())
        self.assertEqual(self.logs(), [])

    def test_no_eligible_games_does_not_request_api(self):
        with get_db() as db:
            db.execute("DELETE FROM user_game_tracking WHERE game_id=101")
        with patch("adapters.cheapshark.urlopen") as opener:
            response = self.post("/admin/sync/cheapshark", follow_redirects=True)
        opener.assert_not_called()
        self.assertIn("没有可同步", response.get_data(as_text=True))
        self.assertEqual(self.logs()[-1]["outcome"], "success")

    def test_audit_never_records_raw_form_credentials_or_exception_text(self):
        secret = "NOT_FOR_AUDIT_RESPONSE_SECRET"
        with self.admin.session_transaction() as state:
            state["test_private"] = secret
        self.post("/admin/sync/demo", {"scenario": "price-90", "event_id": "safe-event", "password": secret,
                                      "session": secret, "command": secret, "user_id": str(self.user_id), "source": "init-db"})
        self.assertEqual(self.logs()[-1]["user_id"], self.admin_id)
        with patch("admin_sync.sync_prices", side_effect=RuntimeError(secret)):
            self.demo(event_id="unexpected-error")
        audit = str([dict(row) for row in self.logs()])
        self.assertNotIn(secret, audit)
        self.assertNotIn(TEST_HASH, audit)
        self.assertNotIn(secret, self.admin.get("/admin/sync").get_data(as_text=True))
        self.assertEqual(self.logs()[-1]["outcome"], "failure")

    def test_database_failure_rolls_back_prices_and_marks_audit_failed(self):
        get_db().execute("""CREATE TRIGGER fail_price BEFORE INSERT ON price_observations
            BEGIN SELECT RAISE(ABORT,'database test'); END""")
        before = self.business_records()
        self.demo()
        self.assertEqual(before, self.business_records())
        self.assertEqual(self.logs()[-1]["outcome"], "failure")

    def test_missing_audit_storage_prevents_sync(self):
        get_db().execute("""CREATE TRIGGER fail_audit BEFORE INSERT ON admin_action_log
            BEGIN SELECT RAISE(ABORT,'audit test'); END""")
        before = self.business_records()
        with patch("admin_sync.sync_prices") as sync:
            response = self.demo(follow_redirects=True)
        sync.assert_not_called()
        self.assertIn("本次未启动同步", response.get_data(as_text=True))
        self.assertEqual(before, self.business_records())
        self.assertEqual(self.logs(), [])

    def test_failed_audit_completion_keeps_intent_and_does_not_claim_price_rollback(self):
        get_db().execute("""CREATE TRIGGER fail_audit_update BEFORE UPDATE ON admin_action_log
            BEGIN SELECT RAISE(ABORT,'audit test'); END""")
        response = self.demo(follow_redirects=True)
        self.assertIn("审计结果未能保存", response.get_data(as_text=True))
        self.assertEqual(self.logs()[0]["outcome"], "running")
        self.assertEqual(get_db().execute("SELECT COUNT(*) FROM price_observations WHERE source_event_id='demo-manual-admin-demo'").fetchone()[0], 1)

    def test_notification_partial_failure_is_explicit_and_retained_for_retry(self):
        get_db().execute("""CREATE TRIGGER fail_notice BEFORE INSERT ON notifications
            BEGIN SELECT RAISE(ABORT,'recipient test'); END""")
        response = self.demo(follow_redirects=True)
        self.assertIn("通知失败 1 次，待重试 1 条", response.get_data(as_text=True))
        self.assertEqual(self.logs()[-1]["outcome"], "success")
        self.assertEqual(get_db().execute("SELECT COUNT(*) FROM notification_retries").fetchone()[0], 1)

    def test_concurrent_same_demo_event_creates_only_one_observation_and_notification(self):
        clients = [self.client(self.admin_id), self.client(self.admin_id)]
        tokens = [self.token(client) for client in clients]
        barrier = Barrier(2)
        def prepare(db, scenario, event_id, **kwargs):
            source = prepare_demo_sync(db, scenario, event_id, **kwargs)
            fetch = source.fetch
            def synchronized_fetch(scenario):
                barrier.wait(timeout=10)
                return fetch(scenario=scenario)
            source.fetch = synchronized_fetch
            return source
        def execute(index):
            return clients[index].post("/admin/sync/demo", data={"scenario": "price-90", "event_id": "concurrent",
                                                                "game_id": "1",
                                                                "csrf_token": tokens[index]}).status_code
        with patch("admin_sync.prepare_demo_sync", side_effect=prepare):
            with ThreadPoolExecutor(max_workers=2) as pool:
                results = list(pool.map(execute, [0, 1]))
        self.assertEqual(results, [303, 303])
        self.assertEqual(get_db().execute("SELECT COUNT(*) FROM price_observations WHERE source_event_id='demo-manual-concurrent'").fetchone()[0], 1)
        self.assertEqual(get_db().execute("SELECT COUNT(*) FROM notifications WHERE source_event_id='demo-manual-concurrent'").fetchone()[0], 1)
        self.assertEqual([row["outcome"] for row in self.logs()], ["success", "success"])

    def test_upgrade_is_repeatable_and_preserves_business_and_audit_records(self):
        self.demo()
        before = self.business_records()
        logs = [tuple(row) for row in self.logs()]
        for _ in range(2):
            result = self.runner.invoke(args=["upgrade-db"])
            self.assertEqual(result.exit_code, 0, result.output)
            self.assertEqual(before, self.business_records())
            self.assertEqual(logs, [tuple(row) for row in self.logs()])
        self.assertEqual(get_db().execute("PRAGMA foreign_key_check").fetchall(), [])

    def test_upgrade_adds_missing_audit_table_without_reinitializing_data(self):
        self.cheap()
        before = self.business_records()
        status = [tuple(row) for row in get_db().execute("SELECT * FROM sync_status ORDER BY source_name")]
        get_db().execute("DROP TABLE admin_action_log")
        for _ in range(2):
            result = self.runner.invoke(args=["upgrade-db"])
            self.assertEqual(result.exit_code, 0, result.output)
            self.assertEqual(before, self.business_records())
            self.assertEqual(status, [tuple(row) for row in get_db().execute("SELECT * FROM sync_status ORDER BY source_name")])
        self.assertEqual(self.logs(), [])
        self.assertEqual(self.demo().status_code, 303)
        self.assertEqual(len(self.logs()), 1)

    def test_no_shell_execution_path_or_dangerous_web_endpoints(self):
        for relative in ["app.py", "admin_sync.py", "services/sync_inputs.py", "services/price_sync.py"]:
            tree = ast.parse((Path(app.root_path) / relative).read_text(encoding="utf-8"))
            for node in ast.walk(tree):
                if isinstance(node, ast.Import):
                    self.assertFalse(any(name.name == "subprocess" for name in node.names))
                if isinstance(node, ast.ImportFrom):
                    self.assertNotEqual(node.module, "subprocess")
                if isinstance(node, ast.Call):
                    target = ast.unparse(node.func)
                    self.assertNotIn(target, {"os.system", "os.popen", "eval", "exec"})
                    self.assertFalse(target.startswith("subprocess."))
        with patch("os.system", side_effect=AssertionError("No shell")), patch("subprocess.Popen", side_effect=AssertionError("No process")):
            self.demo()
            self.cheap()
        self.assertEqual(self.post("/admin/sync", {"command": "init-db"}).status_code, 405)
        for path in ["/admin/init-db", "/admin/sql", "/admin/command", "/admin/users/role"]:
            self.assertEqual(self.post(path).status_code, 404)


if __name__ == "__main__":
    unittest.main()
