"""Explicit read actions, per-user counters and lossless notification upgrades."""
from html.parser import HTMLParser
from pathlib import Path
import sqlite3
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from app import app, get_db, seed_demo_data
from adapters.contracts import PriceObservationEvent
from adapters.demo_price_source import DemoPriceSource
from migrations import migration_transaction, upgrade_notification_read_state
from services.price_sync import sync_prices
from tests.helpers import TEST_HASH


class NotificationRows(HTMLParser):
    def __init__(self, html):
        super().__init__()
        self.rows = []
        self.current = None
        self.cell = None
        self.feed(html)

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if tag == "tr" and "data-notification-id" in attrs:
            self.current = {"id": int(attrs["data-notification-id"]), "cells": [], "actions": []}
        if self.current is not None:
            if tag == "td":
                self.cell = []
            if tag == "form":
                self.current["actions"].append(attrs.get("action"))

    def handle_data(self, text):
        if self.cell is not None:
            self.cell.append(text)

    def handle_endtag(self, tag):
        if tag == "td" and self.current is not None and self.cell is not None:
            self.current["cells"].append(" ".join("".join(self.cell).split()))
            self.cell = None
        if tag == "tr" and self.current is not None:
            self.rows.append(self.current)
            self.current = None


class NotificationReadTest(unittest.TestCase):
    def setUp(self):
        self.previous_config = dict(app.config)
        app.config.update(TESTING=True, DATABASE=":memory:")
        self.ctx = app.app_context()
        self.ctx.push()
        self.runner = app.test_cli_runner()
        self.assertEqual(self.runner.invoke(args=["init-db"]).exit_code, 0)
        ids = []
        with get_db() as db:
            for name, role in [("Alice", "user"), ("Bob", "user"), ("Admin", "admin")]:
                ids.append(db.execute("""INSERT INTO users(display_name,username,username_normalized,password_hash,role)
                    VALUES (?,?,?,?,?)""", (name, name, name.lower(), TEST_HASH, role)).lastrowid)
            self.alice_id, self.bob_id, self.admin_id = ids
            for user_id, target in [(self.alice_id, 10000), (self.bob_id, 9000)]:
                db.execute("INSERT INTO user_game_tracking(user_id,game_id) VALUES (?,2)", (user_id,))
                db.execute("INSERT INTO price_targets(user_id,game_id,target_cents) VALUES (?,2,?)", (user_id, target))
        self.alice = self.client(self.alice_id)
        self.bob = self.client(self.bob_id)
        self.admin = self.client(self.admin_id)
        self.network = patch("adapters.cheapshark.urlopen", side_effect=AssertionError("Offline tests only"))
        self.network.start()
        self.addCleanup(self.network.stop)

    def tearDown(self):
        self.ctx.pop()
        app.config.update(self.previous_config)

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

    def post(self, client, path, **data):
        return client.post(path, data={**data, "csrf_token": self.token(client)})

    def sync(self, steps):
        events = [PriceObservationEvent(f"read-test-{number}", 2, price, f"2026-11-07 {number + 9:02d}:00:00")
                  for number, price in steps]
        return sync_prices(get_db(), SimpleNamespace(name="read-test", fetch=lambda scenario: events))

    def generate(self):
        self.sync([(1, 12800), (2, 10000), (3, 8800)])

    def notices(self, user_id):
        return get_db().execute("SELECT * FROM notifications WHERE user_id=? ORDER BY observed_at,id", (user_id,)).fetchall()

    def unread(self, user_id):
        return get_db().execute("SELECT COUNT(*) FROM notifications WHERE user_id=? AND read_at IS NULL", (user_id,)).fetchone()[0]

    def row(self, notification_id):
        return get_db().execute("SELECT * FROM notifications WHERE id=?", (notification_id,)).fetchone()

    def test_new_notifications_are_unread_and_lists_do_not_mark_them(self):
        self.generate()
        self.assertEqual((self.unread(self.alice_id), self.unread(self.bob_id)), (1, 1))
        before = [tuple(row) for row in get_db().execute("SELECT * FROM notifications ORDER BY id")]
        for _ in range(2):
            page = self.alice.get("/notifications")
            rows = NotificationRows(page.get_data(as_text=True)).rows
            self.assertEqual(len(rows), 1)
            self.assertEqual(rows[0]["cells"][0], "空洞骑士")
            self.assertEqual(rows[0]["cells"][1], "模拟数据")
            self.assertEqual(rows[0]["cells"][2:4], ["$100.00", "$100.00"])
            self.assertEqual(rows[0]["cells"][5], "未读")
        self.assertEqual(before, [tuple(row) for row in get_db().execute("SELECT * FROM notifications ORDER BY id")])

    def test_single_read_changes_only_owned_row_and_uses_post_redirect_get(self):
        self.generate()
        alice_id = self.notices(self.alice_id)[0]["id"]
        bob_before = tuple(self.notices(self.bob_id)[0])
        result = self.post(self.alice, f"/notifications/{alice_id}/read")
        self.assertEqual((result.status_code, result.location), (303, "/notifications"))
        read_at = self.row(alice_id)["read_at"]
        self.assertIsNotNone(read_at)
        self.assertEqual(tuple(self.notices(self.bob_id)[0]), bob_before)
        page = self.alice.get(result.location).get_data(as_text=True)
        self.assertIn("我的通知（未读 0）", page)
        self.assertTrue(NotificationRows(page).rows[0]["cells"][5].startswith("已读"))
        self.assertEqual(NotificationRows(page).rows[0]["actions"], [])
        self.alice.get(result.location)
        self.assertEqual(self.row(alice_id)["read_at"], read_at)

    def test_repeated_single_read_preserves_first_timestamp_and_records(self):
        self.generate()
        notification_id = self.notices(self.alice_id)[0]["id"]
        self.post(self.alice, f"/notifications/{notification_id}/read")
        # Use a distinct, known first-read time to detect a wrongly repeated
        # UPDATE without depending on clock resolution or sleeping in tests.
        with get_db() as db:
            db.execute("UPDATE notifications SET read_at='2000-01-01 00:00:00' WHERE id=?", (notification_id,))
        before = [tuple(row) for row in get_db().execute("SELECT * FROM notifications ORDER BY id")]
        for _ in range(2):
            self.assertEqual(self.post(self.alice, f"/notifications/{notification_id}/read").status_code, 303)
        self.assertEqual(before, [tuple(row) for row in get_db().execute("SELECT * FROM notifications ORDER BY id")])

    def test_bulk_read_only_updates_own_unread_and_is_idempotent(self):
        self.generate()
        self.sync([(4, 12000), (5, 9000)])
        first_id = self.notices(self.alice_id)[0]["id"]
        with get_db() as db:
            db.execute("UPDATE notifications SET read_at='2000-01-01 00:00:00' WHERE id=?", (first_id,))
        bob_before = [tuple(row) for row in self.notices(self.bob_id)]
        result = self.post(self.alice, "/notifications/read-all", user_id=str(self.bob_id))
        self.assertEqual((result.status_code, result.location), (303, "/notifications"))
        self.assertEqual(self.unread(self.alice_id), 0)
        self.assertEqual(self.row(first_id)["read_at"], "2000-01-01 00:00:00")
        self.assertEqual(bob_before, [tuple(row) for row in self.notices(self.bob_id)])
        before = [tuple(row) for row in get_db().execute("SELECT * FROM notifications ORDER BY id")]
        self.post(self.alice, "/notifications/read-all")
        self.assertEqual(before, [tuple(row) for row in get_db().execute("SELECT * FROM notifications ORDER BY id")])

    def test_new_crossing_restores_unread_count_and_replay_keeps_old_read_state(self):
        self.generate()
        self.post(self.alice, "/notifications/read-all")
        old = [tuple(row) for row in self.notices(self.alice_id)]
        self.assertIn("我的通知（未读 0）", self.alice.get("/games?source=demo").get_data(as_text=True))
        self.sync([(4, 12000), (5, 9000)])
        self.assertEqual(self.unread(self.alice_id), 1)
        self.assertIn("我的通知（未读 1）", self.alice.get("/notifications").get_data(as_text=True))
        self.assertEqual(tuple(self.notices(self.alice_id)[0]), old[0])
        self.assertIsNone(self.notices(self.alice_id)[1]["read_at"])
        before = [tuple(row) for row in get_db().execute("SELECT * FROM notifications ORDER BY id")]
        self.sync([(4, 12000), (5, 9000)])
        self.assertEqual(before, [tuple(row) for row in get_db().execute("SELECT * FROM notifications ORDER BY id")])

    def test_list_stays_newest_first_regardless_of_read_state(self):
        self.generate()
        self.sync([(4, 12000), (5, 9000)])
        newest_id = self.notices(self.alice_id)[-1]["id"]
        self.post(self.alice, f"/notifications/{newest_id}/read")
        rows = NotificationRows(self.alice.get("/notifications").get_data(as_text=True)).rows
        self.assertEqual([row["id"] for row in rows], [row["id"] for row in reversed(self.notices(self.alice_id))])
        self.assertTrue(rows[0]["cells"][5].startswith("已读"))
        self.assertEqual(rows[1]["cells"][5], "未读")

    def test_foreign_and_missing_ids_share_same_404_and_cannot_change_owner(self):
        self.generate()
        bob_id = self.notices(self.bob_id)[0]["id"]
        before = [tuple(row) for row in get_db().execute("SELECT * FROM notifications ORDER BY id")]
        foreign = self.post(self.alice, f"/notifications/{bob_id}/read?user_id={self.bob_id}", user_id=str(self.bob_id))
        missing = self.post(self.alice, "/notifications/999999/read")
        self.assertEqual((foreign.status_code, missing.status_code), (404, 404))
        self.assertEqual(foreign.data, missing.data)
        for notification_id in [0, 9223372036854775808]:
            self.assertEqual(self.post(self.alice, f"/notifications/{notification_id}/read").status_code, 404)
        response = self.alice.post(f"/notifications/{bob_id}/read", json={"user_id": self.bob_id},
                                   headers={"X-CSRF-Token": self.token(self.alice)})
        self.assertEqual(response.status_code, 404)
        self.assertEqual(before, [tuple(row) for row in get_db().execute("SELECT * FROM notifications ORDER BY id")])
        page = self.alice.get(f"/notifications?user_id={self.bob_id}").get_data(as_text=True)
        self.assertEqual([row["id"] for row in NotificationRows(page).rows], [self.notices(self.alice_id)[0]["id"]])
        self.assertEqual(self.alice.get(f"/notifications/{bob_id}").status_code, 404)

    def test_bulk_json_and_query_user_ids_never_choose_another_user(self):
        self.generate()
        response = self.alice.post(f"/notifications/read-all?user_id={self.bob_id}", json={"user_id": self.bob_id},
                                   headers={"X-CSRF-Token": self.token(self.alice)})
        self.assertEqual(response.status_code, 303)
        self.assertEqual((self.unread(self.alice_id), self.unread(self.bob_id)), (0, 1))

    def test_anonymous_cannot_read_or_mark_and_has_no_unread_count_query(self):
        self.generate()
        anonymous = self.client()
        notification_id = self.notices(self.alice_id)[0]["id"]
        before = [tuple(row) for row in get_db().execute("SELECT * FROM notifications ORDER BY id")]
        self.assertEqual(anonymous.get("/notifications").status_code, 302)
        for path in [f"/notifications/{notification_id}/read", "/notifications/read-all"]:
            self.assertEqual(self.post(anonymous, path).status_code, 302)
        statements = []
        get_db().set_trace_callback(statements.append)
        try:
            response = anonymous.get("/games/2")
        finally:
            get_db().set_trace_callback(None)
        self.assertNotIn("我的通知（未读", response.get_data(as_text=True))
        self.assertFalse(any("COUNT(*) FROM notifications" in sql for sql in statements))
        self.assertEqual(before, [tuple(row) for row in get_db().execute("SELECT * FROM notifications ORDER BY id")])

    def test_csrf_and_get_methods_cannot_change_read_status(self):
        self.generate()
        notification_id = self.notices(self.alice_id)[0]["id"]
        before = [tuple(row) for row in get_db().execute("SELECT * FROM notifications ORDER BY id")]
        for path in [f"/notifications/{notification_id}/read", "/notifications/read-all"]:
            self.assertEqual(self.alice.get(path).status_code, 405)
            self.assertEqual(self.alice.post(path).status_code, 400)
            self.assertEqual(self.alice.post(path, data={"csrf_token": "invalid"}).status_code, 400)
        self.assertEqual(before, [tuple(row) for row in get_db().execute("SELECT * FROM notifications ORDER BY id")])

    def test_admin_has_only_own_notifications_and_unread_count(self):
        self.generate()
        bob_id = self.notices(self.bob_id)[0]["id"]
        page = self.admin.get(f"/notifications?user_id={self.bob_id}").get_data(as_text=True)
        self.assertIn("我的通知（未读 0）", page)
        self.assertEqual(NotificationRows(page).rows, [])
        self.assertEqual(self.post(self.admin, f"/notifications/{bob_id}/read").status_code, 404)
        self.post(self.admin, "/notifications/read-all", user_id=str(self.bob_id))
        self.assertEqual(self.unread(self.bob_id), 1)

    def test_navigation_uses_current_user_count_on_other_pages(self):
        self.generate()
        self.post(self.bob, "/notifications/read-all")
        for path in ["/games/2", "/games?source=demo", "/games/search", "/notifications"]:
            self.assertIn("我的通知（未读 1）", self.alice.get(path).get_data(as_text=True))
            self.assertIn("我的通知（未读 0）", self.bob.get(path).get_data(as_text=True))

    def test_repeated_upgrade_keeps_existing_read_times_and_all_business_records(self):
        self.generate()
        self.post(self.alice, "/notifications/read-all")
        tables = ["users", "games", "price_observations", "price_targets", "notifications", "notification_retries", "sync_status", "user_game_tracking", "admin_action_log"]
        before = {table: [tuple(row) for row in get_db().execute(f"SELECT * FROM {table} ORDER BY 1,2")] for table in tables}
        for _ in range(2):
            self.assertEqual(self.runner.invoke(args=["upgrade-db"]).exit_code, 0)
            self.assertEqual(before, {table: [tuple(row) for row in get_db().execute(f"SELECT * FROM {table} ORDER BY 1,2")] for table in tables})


class NotificationReadMigrationTest(unittest.TestCase):
    def legacy_db(self):
        db = sqlite3.connect(":memory:")
        db.row_factory = sqlite3.Row
        db.execute("PRAGMA foreign_keys=ON")
        root = Path(app.root_path)
        db.executescript((root / "schema.sql").read_text(encoding="utf-8"))
        old_notifications = (root / "notifications.sql").read_text(encoding="utf-8").replace("    read_at TEXT,\n", "")
        db.executescript(old_notifications)
        db.executescript((root / "notification_retries.sql").read_text(encoding="utf-8"))
        seed_demo_data(db)
        db.commit()
        sync_prices(db, DemoPriceSource(), scenario="sequence")
        return db

    def test_legacy_records_are_preserved_and_start_unread(self):
        db = self.legacy_db()
        try:
            before = [tuple(row) for row in db.execute("SELECT * FROM notifications ORDER BY id")]
            self.assertEqual(len(before), 2)
            upgrade_notification_read_state(db)
            after = db.execute("SELECT * FROM notifications ORDER BY id").fetchall()
            self.assertEqual(before, [tuple(row)[:len(before[0])] for row in after])
            self.assertTrue(all(row["read_at"] is None for row in after))
            with db:
                db.execute("UPDATE notifications SET read_at='2000-01-01 00:00:00' WHERE id=?", (after[0]["id"],))
            rows = [tuple(row) for row in db.execute("SELECT * FROM notifications ORDER BY id")]
            upgrade_notification_read_state(db)
            self.assertEqual(rows, [tuple(row) for row in db.execute("SELECT * FROM notifications ORDER BY id")])
            self.assertEqual([row[2] for row in db.execute("PRAGMA index_info(idx_notifications_user_read)")], ["user_id", "read_at"])
            self.assertEqual(db.execute("PRAGMA foreign_key_check").fetchall(), [])
        finally:
            db.close()

    def test_failed_outer_upgrade_rolls_back_column_index_and_preserves_records(self):
        db = self.legacy_db()
        try:
            before = [tuple(row) for row in db.execute("SELECT * FROM notifications ORDER BY id")]
            with self.assertRaises(sqlite3.IntegrityError):
                with migration_transaction(db):
                    upgrade_notification_read_state(db)
                    db.execute("CREATE UNIQUE INDEX fail_upgrade ON notifications(user_id)")
            self.assertNotIn("read_at", {row[1] for row in db.execute("PRAGMA table_info(notifications)")})
            self.assertIsNone(db.execute("SELECT 1 FROM sqlite_master WHERE name='idx_notifications_user_read'").fetchone())
            self.assertEqual(before, [tuple(row) for row in db.execute("SELECT * FROM notifications ORDER BY id")])
        finally:
            db.close()


if __name__ == "__main__":
    unittest.main()
