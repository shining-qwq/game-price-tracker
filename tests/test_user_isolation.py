"""Independent authenticated sessions and offline per-recipient delivery tests."""
from types import SimpleNamespace
import sqlite3
import unittest
from unittest.mock import patch

from app import app, get_db
from adapters.contracts import PriceObservationEvent
from adapters.demo_price_source import DemoPriceSource
from services.price_sync import sync_prices
from tests.test_accounts import FormParser


PASSWORD = "Test123!"  # Isolated test credential, never a real account password.


class UserIsolationTest(unittest.TestCase):
    def setUp(self):
        self.previous_config = dict(app.config)
        app.config.update(TESTING=True, DATABASE=":memory:")
        self.ctx = app.app_context()
        self.ctx.push()
        self.runner = app.test_cli_runner()
        self.assertEqual(self.runner.invoke(args=["init-db"]).exit_code, 0)
        self.network = patch("adapters.cheapshark.urlopen", side_effect=AssertionError("Offline tests only"))
        self.network.start()
        self.addCleanup(self.network.stop)
        self.alice, self.alice_id = self.new_user("Alice")
        self.bob, self.bob_id = self.new_user("Bob")

    def tearDown(self):
        self.ctx.pop()
        app.config.update(self.previous_config)

    def token(self, client):
        self.assertEqual(client.get("/login").status_code, 200)
        with client.session_transaction() as state:
            return state["_csrf_token"]

    def post(self, client, path, data):
        return client.post(path, data={**data, "csrf_token": self.token(client)})

    def new_user(self, username):
        client = app.test_client()
        self.assertEqual(self.post(client, "/register", {"username": username, "password": PASSWORD}).status_code, 303)
        self.assertEqual(self.post(client, "/login", {"username": username, "password": PASSWORD}).status_code, 303)
        with client.session_transaction() as state:
            return client, state["user_id"]

    def target(self, client, value, game_id=1, **forged):
        if game_id >= 100:
            steam_id = get_db().execute("SELECT steam_app_id FROM games WHERE id=?", (game_id,)).fetchone()[0]
            self.post(client, "/games/track", {"steam_app_id": steam_id})
        else:
            self.post(client, f"/games/{game_id}/demo-tracking", {"action": "add"})
        return self.post(client, f"/games/{game_id}/target", {"target_price": value, **forged})

    def target_value(self, client, game_id=1, query=""):
        response = client.get(f"/games/{game_id}{query}")
        forms = FormParser(response.data).forms
        return next(form["inputs"]["target_price"] for form in forms if "target_price" in form["inputs"])

    def notices(self, user_id):
        return [tuple(row) for row in get_db().execute(
            """SELECT source_event_id, trigger_price_cents, target_cents, observed_at
               FROM notifications WHERE user_id=? ORDER BY observed_at,id""", (user_id,))]

    def real_event(self, key, price, time):
        return PriceObservationEvent(key, 101, price, time)

    def sync_events(self, events):
        source = SimpleNamespace(name="offline-multi-user", fetch=lambda scenario: events)
        return sync_prices(get_db(), source)

    def snapshot(self):
        return {name: [tuple(row) for row in get_db().execute(f"SELECT * FROM {name} ORDER BY 1,2")]
                for name in ["users", "games", "price_targets", "price_observations", "notifications",
                             "notification_retries", "sync_status"]}

    def test_independent_targets_unique_constraint_and_public_page(self):
        self.assertEqual(self.target_value(self.alice), "")
        self.assertEqual(self.target_value(self.bob), "")
        self.target(self.alice, "101.23")
        self.target(self.bob, "88.76")
        self.assertEqual(self.target_value(self.alice), "101.23")
        self.assertEqual(self.target_value(self.bob), "88.76")
        self.target(self.alice, "99.99")
        self.assertEqual(self.target_value(self.bob), "88.76")
        self.assertEqual(get_db().execute("SELECT COUNT(*) FROM price_targets WHERE game_id=1").fetchone()[0], 3)
        with self.assertRaises(sqlite3.IntegrityError):
            with get_db() as db:
                db.execute("INSERT INTO price_targets(user_id,game_id,target_cents) VALUES (?,1,1)", (self.alice_id,))
        anonymous = app.test_client()
        page = anonymous.get("/games/1").get_data(as_text=True)
        self.assertIn("登录后设置自己的目标价", page)
        self.assertNotIn('name="target_price"', page)
        self.assertNotIn("saved-target", page)
        self.assertNotIn("已收到", page)
        for path in ["/games/1", "/games/101", "/games/search"]:
            self.assertEqual(anonymous.get(path).status_code, 200)
        response = anonymous.get("/notifications")
        self.assertEqual(response.status_code, 302)
        self.assertTrue(response.location.startswith("/login?next="))
        self.assertIn("no-store", self.alice.get("/games/1").headers["Cache-Control"])
        self.assertIn("no-store", self.alice.get("/notifications").headers["Cache-Control"])

    def test_session_for_missing_or_disabled_user_never_falls_back_to_demo(self):
        self.target(self.alice, "100.00")
        sync_prices(get_db(), DemoPriceSource(), scenario="price-drop")
        for user_id in [1, 99999999]:
            client = app.test_client()
            with client.session_transaction() as state:
                state["user_id"] = user_id
            page = client.get("/games/1").get_data(as_text=True)
            self.assertIn("登录后设置自己的目标价", page)
            self.assertNotIn('name="target_price"', page)
            self.assertNotIn("已收到", page)
            self.assertEqual(client.get("/notifications").status_code, 302)

    def test_forged_user_ids_never_select_or_update_another_user(self):
        self.target(self.bob, "88.76")
        self.target(self.alice, "101.23", user_id=str(self.bob_id), role="admin")
        self.post(self.alice, f"/games/1/target?user_id={self.bob_id}",
                  {"target_price": "102.34", "user_id": str(self.bob_id)})
        self.assertEqual(self.target_value(self.alice, query=f"?user_id={self.bob_id}"), "102.34")
        self.assertEqual(self.target_value(self.bob), "88.76")
        self.assertEqual(get_db().execute("SELECT target_cents FROM price_targets WHERE user_id=1 AND game_id=1").fetchone()[0], 10000)
        self.assertEqual(self.alice.get(f"/users/{self.bob_id}/notifications").status_code, 404)
        error_messages = []
        for forged_id in [self.bob_id, 99999999]:
            response = self.post(self.alice, f"/games/1/target?user_id={forged_id}",
                                 {"target_price": "0", "user_id": str(forged_id)})
            page = self.alice.get(response.location).get_data(as_text=True)
            error_messages.append("请输入大于 0、最多两位小数且在可存储范围内的目标价。" in page)
        self.assertEqual(error_messages, [True, True])
        self.assertEqual(self.notices(self.alice_id), [])

    def test_each_user_crossings_pages_notification_counts_and_replay(self):
        self.target(self.alice, "100.00")
        self.target(self.bob, "90.00")
        for scenario, counts in [("price-100", (1, 0)), ("price-88", (1, 1)),
                                 ("price-120", (1, 1)), ("price-90", (2, 2))]:
            self.assertTrue(sync_prices(get_db(), DemoPriceSource(scenario), scenario=scenario).success)
            self.assertEqual((len(self.notices(self.alice_id)), len(self.notices(self.bob_id))), counts)
        before = self.snapshot()
        # Replay exactly the previously persisted last event, not a new snapshot.
        last = get_db().execute("SELECT source_event_id,price_cents,observed_at FROM price_observations WHERE game_id=1 ORDER BY observed_at DESC,id DESC LIMIT 1").fetchone()
        replay = SimpleNamespace(name=DemoPriceSource.name, fetch=lambda scenario: [
            PriceObservationEvent(last["source_event_id"], 1, last["price_cents"], last["observed_at"])])
        self.assertEqual(sync_prices(get_db(), replay).notifications, 0)
        for table in ["price_observations", "price_targets", "notifications", "notification_retries"]:
            self.assertEqual(before[table], self.snapshot()[table])
        self.assertEqual([row[1] for row in self.notices(self.alice_id)], [10000, 9000])
        self.assertEqual([row[1] for row in self.notices(self.bob_id)], [8800, 9000])
        alice_page = self.alice.get(f"/notifications?user_id={self.bob_id}").get_data(as_text=True)
        bob_page = self.bob.get("/notifications", json={"user_id": self.alice_id}).get_data(as_text=True)
        self.assertIn("2 条 · Alice", alice_page)
        self.assertIn("2 条 · Bob", bob_page)
        self.assertNotIn("$88.00", alice_page)
        self.assertNotIn("$100.00", bob_page)
        for client in [self.alice, self.bob]:
            self.assertIn("已收到 2 条降价通知", client.get("/games/1").get_data(as_text=True))

    def test_anonymous_and_invalid_csrf_cannot_modify_targets(self):
        before = self.snapshot()
        anonymous = app.test_client()
        response = anonymous.post("/games/1/target", data={"target_price": "1.00", "user_id": self.bob_id,
                                                          "csrf_token": self.token(anonymous)})
        self.assertEqual(response.status_code, 302)
        self.assertEqual(anonymous.post("/games/1/target", data={"target_price": "1.00"}).status_code, 400)
        for client in [self.alice, self.bob]:
            self.assertEqual(client.post("/games/1/target", data={"target_price": "1.00", "csrf_token": "bad"}).status_code, 400)
        self.assertEqual(before, self.snapshot())

    def test_admin_has_no_implicit_access_to_other_users_data(self):
        self.target(self.alice, "100.00")
        self.assertEqual(self.runner.invoke(args=["create-admin", "--username", "admin_test"],
                                           input=PASSWORD + "\n" + PASSWORD + "\n").exit_code, 0)
        admin = app.test_client()
        self.post(admin, "/login", {"username": "admin_test", "password": PASSWORD})
        self.assertEqual(self.target_value(admin, query=f"?user_id={self.alice_id}"), "")
        sync_prices(get_db(), DemoPriceSource(), scenario="price-drop")
        page = admin.get(f"/notifications?user_id={self.alice_id}").get_data(as_text=True)
        self.assertIn("管理员", page)
        self.assertIn("0 条 · admin_test", page)
        self.assertNotIn("$88.00", page)
        self.target(admin, "50.00", user_id=str(self.alice_id))
        self.assertEqual(self.target_value(self.alice), "100.00")

    def test_failed_recipient_is_persisted_other_recipient_delivered_and_retry_is_idempotent(self):
        for client in [self.alice, self.bob]:
            self.target(client, "100.00", game_id=101)
        high = self.real_event("multi-high", 12800, "2026-11-06 10:00:00")
        low = self.real_event("multi-low", 9000, "2026-11-06 11:00:00")
        self.assertEqual(self.sync_events([high]).notifications, 0)
        get_db().execute(f"""CREATE TRIGGER fail_alice BEFORE INSERT ON notifications
                            WHEN NEW.user_id={self.alice_id}
                            BEGIN SELECT RAISE(ABORT, 'test recipient failure'); END""")
        result = self.sync_events([low])
        self.assertEqual((result.inserted, result.notifications, result.notification_failures, result.pending_notifications), (1, 1, 1, 1))
        self.assertEqual(self.notices(self.alice_id), [])
        self.assertEqual(len(self.notices(self.bob_id)), 1)
        failure = get_db().execute("SELECT * FROM notification_retries").fetchone()
        self.assertEqual((failure["user_id"], failure["target_cents"], failure["trigger_price_cents"], failure["attempts"]), (self.alice_id, 10000, 9000, 1))
        self.assertEqual(self.sync_events([low]).notification_failures, 1)
        self.assertEqual(get_db().execute("SELECT attempts FROM notification_retries").fetchone()[0], 2)
        # A duplicate pending delivery for Bob cannot stop Alice's later retry.
        get_db().execute("""INSERT INTO notification_retries
                           SELECT user_id,game_id,source_event_id,trigger_price_cents,target_cents,
                                  observed_at,created_at,created_at,1,'integrity_error'
                           FROM notifications WHERE user_id=?""", (self.bob_id,))
        get_db().execute("DROP TRIGGER fail_alice")
        get_db().commit()
        self.target(self.alice, "80.00", game_id=101)
        result = self.sync_events([low])
        self.assertEqual((result.inserted, result.notifications, result.notification_failures, result.pending_notifications), (0, 1, 0, 0))
        self.assertEqual(self.notices(self.alice_id)[0][1:3], (9000, 10000))
        self.assertEqual(len(self.notices(self.bob_id)), 1)
        self.assertEqual(self.sync_events([low]).notifications, 0)
        self.assertEqual(len(self.notices(self.alice_id)), 1)
        page = self.bob.get("/notifications").get_data(as_text=True)
        self.assertNotIn("Alice", page)
        self.assertNotIn("test recipient failure", page)

    def test_retry_bookkeeping_failure_rolls_back_price_and_other_notifications(self):
        for client in [self.alice, self.bob]:
            self.target(client, "100.00", game_id=101)
        self.sync_events([self.real_event("high", 12800, "2026-11-06 10:00:00")])
        # Bob fails after Alice's success, then failure bookkeeping also fails.
        get_db().execute(f"""CREATE TRIGGER fail_bob BEFORE INSERT ON notifications
                            WHEN NEW.user_id={self.bob_id}
                            BEGIN SELECT RAISE(ABORT, 'recipient failure'); END""")
        get_db().execute("""CREATE TRIGGER fail_retry BEFORE INSERT ON notification_retries
                            BEGIN SELECT RAISE(ABORT, 'storage failure'); END""")
        get_db().commit()
        before = self.snapshot()
        with self.assertRaises(sqlite3.IntegrityError):
            self.sync_events([self.real_event("low", 9000, "2026-11-06 11:00:00")])
        self.assertEqual(before, self.snapshot())

    def test_cli_reports_failure_and_replays_only_failed_recipient(self):
        self.target(self.alice, "100.00")
        self.target(self.bob, "100.00")
        get_db().execute(f"""CREATE TRIGGER fail_alice BEFORE INSERT ON notifications
                            WHEN NEW.user_id={self.alice_id}
                            BEGIN SELECT RAISE(ABORT, 'test recipient failure'); END""")
        args = ["sync-prices", "--scenario", "price-90", "--event-id", "cli-multi"]
        result = self.runner.invoke(args=args)
        self.assertEqual(result.exit_code, 0, result.output)
        self.assertIn("本轮失败 1 次，待重试 1 条", result.output)
        self.assertEqual(len(self.notices(self.bob_id)), 1)
        get_db().execute("DROP TRIGGER fail_alice")
        result = self.runner.invoke(args=args)
        self.assertEqual(result.exit_code, 0, result.output)
        self.assertIn("新增 0 条", result.output)
        self.assertIn("生成通知 1 条", result.output)
        self.assertEqual(len(self.notices(self.alice_id)), 1)
        self.assertEqual(len(self.notices(self.bob_id)), 1)
        self.assertNotIn("test recipient failure", self.alice.get("/notifications").get_data(as_text=True))

    def test_pending_notices_survive_source_failure_and_pause_until_retracked(self):
        self.target(self.alice, "100.00", game_id=101)
        self.sync_events([self.real_event("pending-high", 12800, "2026-11-06 10:00:00")])
        get_db().execute(f"""CREATE TRIGGER fail_alice BEFORE INSERT ON notifications
                            WHEN NEW.user_id={self.alice_id}
                            BEGIN SELECT RAISE(ABORT, 'recipient failure'); END""")
        self.sync_events([self.real_event("pending-low", 9000, "2026-11-06 11:00:00")])
        before = self.snapshot()
        status_before = dict(get_db().execute("SELECT * FROM sync_status WHERE source_name='offline-multi-user'").fetchone())
        result = sync_prices(get_db(), DemoPriceSource(), scenario="failure")
        self.assertFalse(result.success)
        after = self.snapshot()
        for name in before:
            if name != "sync_status":
                self.assertEqual(before[name], after[name])
        self.assertEqual(dict(get_db().execute("SELECT * FROM sync_status WHERE source_name='offline-multi-user'").fetchone()), status_before)
        get_db().execute("DROP TRIGGER fail_alice")
        get_db().execute("DELETE FROM user_game_tracking WHERE user_id=? AND game_id=101", (self.alice_id,))
        get_db().commit()
        self.assertEqual(self.sync_events([]).pending_notifications, 1)
        self.assertEqual(self.notices(self.alice_id), [])
        get_db().execute("INSERT INTO user_game_tracking(user_id,game_id) VALUES (?,101)", (self.alice_id,))
        get_db().commit()
        self.assertEqual(self.sync_events([]).notifications, 1)
        self.assertEqual(len(self.notices(self.alice_id)), 1)

    def test_new_subscription_does_not_receive_notifications_for_replayed_crossing(self):
        self.target(self.alice, "100.00", game_id=101)
        high = self.real_event("subscription-high", 12800, "2026-11-06 10:00:00")
        low = self.real_event("subscription-low", 9000, "2026-11-06 11:00:00")
        self.sync_events([high, low])
        self.target(self.bob, "100.00", game_id=101)
        self.assertEqual(self.sync_events([high, low]).notifications, 0)
        self.assertEqual(self.notices(self.bob_id), [])
        self.assertEqual(len(self.notices(self.alice_id)), 1)

    def test_repeated_upgrade_preserves_legacy_owner_and_all_user_records(self):
        self.target(self.alice, "100.00")
        self.target(self.bob, "90.00")
        sync_prices(get_db(), DemoPriceSource(), scenario="sequence")
        get_db().execute("DROP TABLE notification_retries")
        get_db().commit()
        tables = ["users", "games", "price_targets", "price_observations", "notifications", "sync_status"]
        before = {name: [tuple(row) for row in get_db().execute(f"SELECT * FROM {name} ORDER BY 1,2")] for name in tables}
        for _ in range(2):
            self.assertEqual(self.runner.invoke(args=["upgrade-db"]).exit_code, 0)
            self.assertEqual(before, {name: [tuple(row) for row in get_db().execute(f"SELECT * FROM {name} ORDER BY 1,2")] for name in tables})
        demo = get_db().execute("SELECT username,password_hash FROM users WHERE id=1").fetchone()
        self.assertEqual(tuple(demo), ("demo", None))
        self.assertEqual(get_db().execute("SELECT target_cents FROM price_targets WHERE user_id=1 AND game_id=1").fetchone()[0], 10000)
        self.assertEqual(get_db().execute("PRAGMA foreign_key_check").fetchall(), [])


if __name__ == "__main__":
    unittest.main()
