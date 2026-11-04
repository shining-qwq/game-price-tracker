import sqlite3
import unittest
from pathlib import Path

from app import app


class PriceSyncTest(unittest.TestCase):
    def setUp(self):
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

    def query_one(self, sql):
        db = sqlite3.connect(app.config["DATABASE"])
        try:
            return db.execute(sql).fetchone()
        finally:
            db.close()

    def test_price_drop_is_idempotent_and_triggers_reminder(self):
        client = app.test_client()
        before = client.get("/games/1")
        self.assertIn("¥128.00".encode(), before.data)
        self.assertNotIn("到达你的目标价了".encode(), before.data)

        first = app.test_cli_runner().invoke(args=["sync-prices"])
        self.assertEqual(first.exit_code, 0, first.output)
        self.assertIn("新增 1 条".encode(), first.output.encode())

        second = app.test_cli_runner().invoke(args=["sync-prices"])
        self.assertEqual(second.exit_code, 0, second.output)
        self.assertIn("新增 0 条".encode(), second.output.encode())
        self.assertEqual(self.query_one("SELECT COUNT(*) FROM price_observations")[0], 21)

        after = client.get("/games/1")
        self.assertIn("¥88.00".encode(), after.data)
        self.assertIn("到达你的目标价了".encode(), after.data)

    def test_late_event_does_not_replace_current_price_or_trigger(self):
        result = app.test_cli_runner().invoke(args=["sync-prices", "--scenario", "late"])
        self.assertEqual(result.exit_code, 0, result.output)

        page = app.test_client().get("/games/1")
        self.assertIn("¥128.00".encode(), page.data)
        self.assertNotIn("到达你的目标价了".encode(), page.data)

    def test_failure_preserves_price_and_last_success_time(self):
        success = app.test_cli_runner().invoke(args=["sync-prices"])
        self.assertEqual(success.exit_code, 0, success.output)
        before = self.query_one(
            "SELECT last_success_at FROM sync_status WHERE source_name = 'demo-steam-cn'"
        )[0]

        failure = app.test_cli_runner().invoke(args=["sync-prices", "--scenario", "failure"])
        self.assertNotEqual(failure.exit_code, 0)
        after = self.query_one(
            "SELECT last_success_at FROM sync_status WHERE source_name = 'demo-steam-cn'"
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


if __name__ == "__main__":
    unittest.main()
