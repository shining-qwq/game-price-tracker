from tests.helpers import business_client
import sqlite3
import unittest
from pathlib import Path

from app import app
from adapters.demo_price_source import DemoPriceSource
from services.price_sync import sync_prices


class PriceSyncTest(unittest.TestCase):
    def setUp(self):
        self.original_config = dict(app.config)
        self.database_path = Path(__file__).parent / "_test_price_sync.sqlite3"
        self.database_path.unlink(missing_ok=True)
        app.config.update(
            TESTING=True,
            DATABASE=self.database_path,
        )
        result = app.test_cli_runner().invoke(args=["init-db"])
        self.assertEqual(result.exit_code, 0, result.output)

    def tearDown(self):
        self.database_path.unlink(missing_ok=True)
        app.config.update(self.original_config)

    def query_one(self, sql):
        db = sqlite3.connect(app.config["DATABASE"])
        try:
            return db.execute(sql).fetchone()
        finally:
            db.close()

    def test_price_drop_is_idempotent_and_triggers_reminder(self):
        client = business_client()
        before = client.get("/games/1")
        self.assertIn("$128.00".encode(), before.data)
        self.assertEqual(self.query_one("SELECT COUNT(*) FROM notifications")[0], 0)

        first = app.test_cli_runner().invoke(args=["sync-prices"])
        self.assertEqual(first.exit_code, 0, first.output)
        self.assertIn("新增 1 条".encode(), first.output.encode())

        second = app.test_cli_runner().invoke(args=["sync-prices"])
        self.assertEqual(second.exit_code, 0, second.output)
        self.assertIn("新增 0 条".encode(), second.output.encode())
        self.assertEqual(self.query_one("SELECT COUNT(*) FROM price_observations")[0], 21)

        after = client.get("/games/1")
        self.assertIn("$88.00".encode(), after.data)
        self.assertEqual(self.query_one("SELECT COUNT(*) FROM notifications")[0], 1)

    def test_late_event_does_not_replace_current_price_or_trigger(self):
        result = app.test_cli_runner().invoke(args=["sync-prices", "--scenario", "late"])
        self.assertEqual(result.exit_code, 0, result.output)

        page = business_client().get("/games/1")
        self.assertIn("$128.00".encode(), page.data)
        self.assertEqual(self.query_one("SELECT COUNT(*) FROM notifications")[0], 0)

    def test_failure_preserves_price_and_last_success_time(self):
        success = app.test_cli_runner().invoke(args=["sync-prices"])
        self.assertEqual(success.exit_code, 0, success.output)
        before = self.query_one(
            "SELECT last_success_at FROM sync_status WHERE source_name = 'demo-steam-usd'"
        )[0]

        failure = app.test_cli_runner().invoke(args=["sync-prices", "--scenario", "failure"])
        self.assertNotEqual(failure.exit_code, 0)
        after = self.query_one(
            "SELECT last_success_at FROM sync_status WHERE source_name = 'demo-steam-usd'"
        )[0]

        self.assertEqual(before, after)
        self.assertEqual(
            self.query_one(
                """
                SELECT price_cents FROM price_observations
                WHERE game_id = 1 ORDER BY observed_at DESC, id DESC LIMIT 1
                """
            )[0],
            8800,
        )

    def test_crossings_sequence_and_replay(self):
        client = business_client()
        client.post("/games/1/target", data={"target_price": "100.00"})
        runner = app.test_cli_runner()
        for scenario, expected in [
            ("price-100", 1), ("price-88", 1),
            ("price-120", 1), ("price-90", 2), ("price-90", 2),
        ]:
            with self.subTest(scenario=scenario):
                result = runner.invoke(args=["sync-prices", "--scenario", scenario, "--event-id", scenario])
                self.assertEqual(result.exit_code, 0, repr(result.exception))
                self.assertEqual(self.query_one(f"SELECT COUNT(*) FROM notifications WHERE user_id={client.user_id}")[0], expected)
        self.assertEqual(self.query_one("SELECT COUNT(*) FROM price_observations WHERE game_id=1")[0], 9)
        self.assertEqual(self.query_one(
            f"SELECT group_concat(trigger_price_cents) FROM (SELECT trigger_price_cents FROM notifications WHERE user_id={client.user_id} ORDER BY observed_at)"
        )[0], "10000,9000")
        page = client.get("/notifications?user_id=999")
        self.assertEqual(page.status_code, 200)
        for text in ["星露谷物语", "$100.00", "$90.00", "模拟数据"]:
            self.assertIn(text.encode(), page.data)

    def test_target_validation_and_current_user_without_immediate_notification(self):
        client = business_client()
        for value in ["0", "-1", "12.345", "100.000", "NaN", "Infinity", "1e2", "", "9999999999999999999999"]:
            with self.subTest(value=value):
                response = client.post("/games/1/target", data={"target_price": value}, follow_redirects=True)
                self.assertIn("请输入大于 0".encode(), response.data)
                self.assertEqual(self.query_one("SELECT target_cents FROM price_targets WHERE game_id=1")[0], 10000)
        # Even though $128 is below this new target, saving must not notify.
        client.post("/games/1/target", data={"target_price": "150.01", "user_id": "999"})
        self.assertEqual(self.query_one(f"SELECT user_id, target_cents FROM price_targets WHERE game_id=1 AND user_id={client.user_id}"), (client.user_id, 15001))
        self.assertEqual(self.query_one("SELECT target_cents FROM price_targets WHERE game_id=1 AND user_id=1"), (10000,))
        self.assertEqual(self.query_one(f"SELECT COUNT(*) FROM notifications WHERE user_id={client.user_id}")[0], 0)
        runner = app.test_cli_runner()
        result = runner.invoke(args=["sync-prices", "--scenario", "price-100"])
        self.assertEqual(result.exit_code, 0)
        self.assertEqual(self.query_one(f"SELECT COUNT(*) FROM notifications WHERE user_id={client.user_id}")[0], 0)

    def test_batch_sequence_replay_and_notification_snapshot(self):
        runner = app.test_cli_runner()
        for _ in range(2):
            result = runner.invoke(args=["sync-prices", "--scenario", "sequence"])
            self.assertEqual(result.exit_code, 0, repr(result.exception))
        self.assertEqual(self.query_one("SELECT COUNT(*) FROM notifications")[0], 2)
        business_client().post("/games/1/target", data={"target_price": "80.00"})
        self.assertEqual(self.query_one("SELECT COUNT(*) FROM notifications WHERE target_cents=10000")[0], 2)

    def test_notification_constraint_and_transaction_rollback(self):
        db = sqlite3.connect(app.config["DATABASE"])
        db.row_factory = sqlite3.Row
        db.execute("PRAGMA foreign_keys = ON")
        try:
            # Transaction-wide failures still roll back prices and sync status.
            db.execute("""
                CREATE TRIGGER fail_notification BEFORE INSERT ON notifications
                BEGIN SELECT RAISE(ROLLBACK, 'test failure'); END
            """)
            with self.assertRaises(sqlite3.IntegrityError):
                sync_prices(db, DemoPriceSource(), scenario="price-100")
            self.assertEqual(db.execute("SELECT COUNT(*) FROM price_observations").fetchone()[0], 20)
            self.assertEqual(db.execute("SELECT COUNT(*) FROM notifications").fetchone()[0], 0)
            self.assertEqual(db.execute("SELECT COUNT(*) FROM sync_status").fetchone()[0], 0)
            db.execute("DROP TRIGGER fail_notification")
            sync_prices(db, DemoPriceSource(), scenario="price-100")
            with self.assertRaises(sqlite3.IntegrityError):
                db.execute("""
                    INSERT INTO notifications
                        (user_id, game_id, source_event_id, trigger_price_cents,
                         target_cents, observed_at, created_at)
                    SELECT user_id, game_id, source_event_id, trigger_price_cents,
                           target_cents, observed_at, created_at FROM notifications
                """)
            db.rollback()
        finally:
            db.close()


if __name__ == "__main__":
    unittest.main()
