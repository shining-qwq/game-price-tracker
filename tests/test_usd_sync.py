import sqlite3
import unittest
from unittest.mock import patch

from app import app, get_db
from adapters.cheapshark import CheapSharkSource
from services.price_sync import sync_prices


class UsdSyncTest(unittest.TestCase):
    def setUp(self):
        self.original_config = dict(app.config)
        app.config.update(TESTING=True, DATABASE=":memory:")
        self.ctx = app.app_context()
        self.ctx.push()
        self.runner = app.test_cli_runner()
        result = self.runner.invoke(args=["init-db"])
        self.assertEqual(result.exit_code, 0, repr(result.exception))

    def tearDown(self):
        self.ctx.pop()
        app.config.update(self.original_config)

    def test_arbitrary_integer_and_int32_boundaries(self):
        for value in ["123", "0", "2147483647"]:
            result = self.runner.invoke(args=["sync-prices", "--scenario", "price-" + value])
            self.assertEqual(result.exit_code, 0, repr(result.exception))
            self.assertEqual(get_db().execute(
                "SELECT price_cents FROM price_observations WHERE game_id=1 ORDER BY observed_at DESC,id DESC LIMIT 1"
            ).fetchone()[0], int(value) * 100)
        before = get_db().execute("SELECT COUNT(*) FROM price_observations").fetchone()[0]
        for invalid in ["price--1", "price-2147483648", "price-1.5", "price-abc", "price-", "price-999999999999999999999999"]:
            result = self.runner.invoke(args=["sync-prices", "--scenario", invalid])
            self.assertEqual(result.exit_code, 2, result.output)
        self.assertEqual(get_db().execute("SELECT COUNT(*) FROM price_observations").fetchone()[0], before)

    def test_repeated_price_can_cross_again_but_same_event_cannot_change(self):
        for value in ["90", "128", "90"]:
            result = self.runner.invoke(args=["sync-prices", "--scenario", "price-" + value])
            self.assertEqual(result.exit_code, 0)
        self.assertEqual(get_db().execute("SELECT COUNT(*) FROM notifications").fetchone()[0], 2)
        args = ["sync-prices", "--scenario", "price-20", "--event-id", "my-event"]
        for _ in range(2):
            self.assertEqual(self.runner.invoke(args=args).exit_code, 0)
        self.assertEqual(get_db().execute(
            "SELECT COUNT(*) FROM price_observations WHERE source_event_id='demo-manual-my-event'"
        ).fetchone()[0], 1)
        bad = self.runner.invoke(args=["sync-prices", "--scenario", "price-21", "--event-id", "my-event"])
        self.assertNotEqual(bad.exit_code, 0)

    def sync_rows(self, rows):
        with patch("adapters.cheapshark.urlopen") as opener:
            opener.return_value.__enter__.return_value.headers = {}
            with patch("adapters.cheapshark.json.load", return_value=rows):
                result = sync_prices(get_db(), CheapSharkSource())
            self.assertEqual(opener.call_args.kwargs["timeout"], 10)
        return result

    def test_real_prices_are_isolated_stable_and_missing_is_not_free(self):
        row = {"storeID": "1", "steamAppID": "413150", "salePrice": "14.99", "lastChange": 100}
        self.assertEqual(self.sync_rows([row]).inserted, 1)
        self.assertEqual(self.sync_rows([row]).inserted, 0)
        self.assertEqual(get_db().execute("SELECT COUNT(*) FROM notifications").fetchone()[0], 0)
        self.assertEqual(get_db().execute("SELECT MIN(price_cents) FROM price_observations WHERE game_id=1").fetchone()[0], 12800)
        client = app.test_client()
        client.post("/games/101/target", data={"target_price": "10.00", "user_id": "999"})
        self.assertEqual(self.sync_rows([{**row, "salePrice": None, "lastChange": 101}]).inserted, 0)
        self.assertEqual(self.sync_rows([]).inserted, 0)
        self.assertEqual(get_db().execute("SELECT COUNT(*) FROM notifications").fetchone()[0], 0)
        self.assertEqual(self.sync_rows([{**row, "salePrice": "0.00", "lastChange": 102}]).notifications, 1)
        self.assertEqual(get_db().execute("SELECT MIN(price_cents) FROM price_observations WHERE game_id=101").fetchone()[0], 0)
        self.assertEqual(self.sync_rows([{**row, "salePrice": "99.00", "lastChange": 101}]).inserted, 0)
        detail = client.get("/games/101").data
        self.assertIn(b"$0.00", detail)
        self.assertNotIn("人民币".encode(), detail)
        self.assertNotIn("¥".encode(), detail)
        self.assertIn(b"CheapShark", detail)
        self.assertIn("地区未由源提供".encode(), detail)

    def test_real_failure_and_unmatched_store_do_not_write_prices(self):
        self.sync_rows([{"storeID": "1", "steamAppID": "413150", "salePrice": "14.99", "lastChange": 100}])
        before = tuple(get_db().execute("SELECT last_success_at FROM sync_status WHERE source_name='cheapshark-steam-usd'").fetchone())
        with patch("adapters.cheapshark.urlopen", side_effect=TimeoutError):
            result = sync_prices(get_db(), CheapSharkSource())
        self.assertFalse(result.success)
        self.assertEqual(tuple(get_db().execute("SELECT last_success_at FROM sync_status WHERE source_name='cheapshark-steam-usd'").fetchone()), before)
        self.assertFalse(self.sync_rows([{"storeID": "2", "steamAppID": "413150"}]).success)
        self.assertEqual(get_db().execute("SELECT COUNT(*) FROM price_observations WHERE game_id=101").fetchone()[0], 1)

    def test_empty_real_page_has_no_simulated_history(self):
        page = app.test_client().get("/games/101")
        self.assertEqual(page.status_code, 200)
        self.assertIn("暂无已记录报价".encode(), page.data)
        self.assertIn("0 次观测".encode(), page.data)
        self.assertNotIn(b"$128.00", page.data)
