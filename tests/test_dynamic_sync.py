from tests.helpers import business_client
import io
import json
import sqlite3
import unittest
from urllib.error import HTTPError
from urllib.parse import parse_qs, urlparse
from unittest.mock import patch

from app import app, get_db
from adapters.cheapshark import CheapSharkSource, VerifiedSteamGame, SYNC_BATCH_SIZE
from adapters.contracts import PriceSyncGame
from services.game_tracking import track_verified_game
from services.price_sync import load_sync_games, sync_prices


class ApiResponse(io.BytesIO):
    def __init__(self, payload, headers=None):
        super().__init__(payload if isinstance(payload, bytes) else json.dumps(payload).encode())
        self.headers = headers or {}


def quote(game, price="14.99", changed=100):
    return {"storeID": "1", "steamAppID": game.steam_app_id,
            "salePrice": price, "lastChange": changed}


class DynamicSyncTest(unittest.TestCase):
    def setUp(self):
        self.config_before = dict(app.config)
        app.config.update(TESTING=True, DATABASE=":memory:")
        self.ctx = app.app_context()
        self.ctx.push()
        self.runner = app.test_cli_runner()
        result = self.runner.invoke(args=["init-db"])
        self.assertEqual(result.exit_code, 0)
        self.owner_id = business_client().user_id
        self.dynamic_id, _ = track_verified_game(get_db(), VerifiedSteamGame("555", "9999", "动态第五款", 12800), self.owner_id)

    def tearDown(self):
        self.ctx.pop()
        app.config.update(self.config_before)

    def add_games(self, count):
        for index in range(count):
            track_verified_game(get_db(), VerifiedSteamGame(str(100000 + index), str(200000 + index), f"游戏 {index}", 1499), self.owner_id)

    def payloads(self, games, changed=100):
        return [[quote(game, changed=changed) for game in games[start:start + SYNC_BATCH_SIZE]]
                for start in range(0, len(games), SYNC_BATCH_SIZE)]

    def run_sync(self, payloads, cli=False, now=None):
        responses = [item if isinstance(item, (Exception, ApiResponse)) else ApiResponse(item) for item in payloads]
        iterator = iter(responses)

        def respond(request, **kwargs):
            # No price write transaction or partial new rows during API fetching.
            self.assertFalse(get_db().in_transaction)
            self.assertEqual(kwargs["timeout"], 10)
            self.assertIn("GamePriceTracker", request.headers["User-agent"])
            response = next(iterator)
            if isinstance(response, Exception):
                raise response
            return response

        source = CheapSharkSource(load_sync_games(get_db()))
        with patch("adapters.cheapshark.urlopen", side_effect=respond) as opener:
            result = self.runner.invoke(args=["sync-prices", "--source", "cheapshark"]) if cli else sync_prices(get_db(), source, now=now)
            calls = opener.call_args_list
        return result, source, calls

    def snapshot(self):
        return {name: [tuple(row) for row in get_db().execute(f"SELECT * FROM {name} ORDER BY 1")]
                for name in ["games", "price_observations", "price_targets", "notifications"]}

    def test_dynamic_five_mapping_is_not_response_order_and_replay_is_idempotent(self):
        games = load_sync_games(get_db())
        self.assertEqual(len(games), 5)
        self.assertTrue(all(isinstance(game, PriceSyncGame) for game in games))
        rows = [quote(game, f"{game.game_id // 100}.{game.game_id % 100:02d}") for game in reversed(games)]
        result, _, calls = self.run_sync([rows])
        self.assertEqual((result.received, result.inserted, result.notifications), (5, 5, 0))
        requested = parse_qs(urlparse(calls[0].args[0].full_url).query)
        self.assertEqual(requested["storeID"], ["1"])
        self.assertEqual(set(requested["steamAppID"][0].split(",")), {game.steam_app_id for game in games})
        for game in games:
            row = get_db().execute("SELECT price_cents, typeof(price_cents) FROM price_observations WHERE game_id=?", (game.game_id,)).fetchone()
            self.assertEqual(tuple(row), (game.game_id, "integer"))
        before = self.snapshot()
        result, _, _ = self.run_sync([rows])
        self.assertEqual(result.inserted, 0)
        self.assertEqual(self.snapshot(), before)

    def test_missing_game_preserves_price_and_explicit_zero_is_recorded(self):
        games = load_sync_games(get_db())
        self.run_sync(self.payloads(games))
        rows = [quote(game, price="0.00" if game == games[0] else "9.99", changed=101) for game in games[:-1]]
        result, source, _ = self.run_sync([rows])
        self.assertEqual((result.received, result.inserted), (4, 4))
        self.assertEqual(source.missing_games, [games[-1]])
        self.assertEqual(get_db().execute("SELECT price_cents FROM price_observations WHERE game_id=? ORDER BY observed_at DESC LIMIT 1", (self.dynamic_id,)).fetchone()[0], 1499)
        self.assertEqual(get_db().execute("SELECT MIN(price_cents) FROM price_observations WHERE game_id=?", (games[0].game_id,)).fetchone()[0], 0)
        page = business_client().get(f"/games/{games[0].game_id}")
        self.assertIn(b"$0.00", page.data)
        self.assertIn("不代表永久免费".encode(), page.data)
        # A present row without salePrice is still missing, never a free price.
        absent_price = quote(games[-1], changed=102)
        del absent_price["salePrice"]
        result, source, _ = self.run_sync([[absent_price]])
        self.assertEqual(result.inserted, 0)
        self.assertEqual(len(source.missing_games), 5)

    def test_more_than_page_limit_is_batched(self):
        self.add_games(60)
        games = load_sync_games(get_db())
        self.assertEqual(len(games), 65)
        result, _, calls = self.run_sync(self.payloads(games))
        self.assertEqual((result.received, result.inserted), (65, 65))
        self.assertEqual(len(calls), 2)
        requested = [parse_qs(urlparse(call.args[0].full_url).query) for call in calls]
        self.assertEqual([len(params["steamAppID"][0].split(",")) for params in requested], [60, 5])
        self.assertTrue(all(params["pageSize"] == ["60"] for params in requested))

    def test_second_batch_failure_keeps_entire_previous_round_and_success_time(self):
        self.add_games(60)
        games = load_sync_games(get_db())
        self.run_sync(self.payloads(games), now="2026-01-01 10:00:00 UTC")
        before = self.snapshot()
        limited = HTTPError("https://example.invalid", 429, "limited", {"Retry-After": "120"}, None)
        try:
            for failure in [TimeoutError(), limited, b"not json", {"wrong": "shape"},
                            [quote(PriceSyncGame(999, "888888888", "wrong"), changed=101)]]:
                with self.subTest(failure=type(failure).__name__):
                    result, source, calls = self.run_sync([self.payloads(games, changed=101)[0], failure], now="2026-01-02 10:00:00 UTC")
                    self.assertFalse(result.success)
                    self.assertEqual(result.inserted, 0)
                    self.assertEqual(len(calls), 2)
                    self.assertEqual(source.missing_games, [])
                    self.assertEqual(self.snapshot(), before)
                    status = get_db().execute("SELECT * FROM sync_status WHERE source_name='cheapshark-steam-usd'").fetchone()
                    self.assertEqual(status["last_success_at"], "2026-01-01 10:00:00 UTC")
                    self.assertEqual(status["last_attempt_at"], "2026-01-02 10:00:00 UTC")
                    self.assertTrue(status["last_error"])
                    if failure is limited:
                        self.assertIn("Retry-After=120", status["last_error"])
        finally:
            limited.close()

    def test_invalid_store_app_price_timestamp_and_duplicate_quote_fail_atomically(self):
        game = load_sync_games(get_db())[0]
        before = self.snapshot()
        wrong_rows = [[{**quote(game), "storeID": "2"}], [{**quote(game), "steamAppID": []}],
                      [{**quote(game), "steamAppID": "777777"}], [{**quote(game), "salePrice": "NaN"}],
                      [{**quote(game), "salePrice": "1e999999"}], [{**quote(game), "salePrice": "1e-999999"}],
                      [{**quote(game), "lastChange": "100"}],
                      [quote(game), quote(game)]]
        for rows in wrong_rows:
            with self.subTest(rows=rows):
                result, _, _ = self.run_sync([rows])
                self.assertFalse(result.success)
                self.assertEqual(self.snapshot(), before)

    def test_dynamic_game_baseline_notifications_and_old_snapshot(self):
        client = business_client()
        client.post(f"/games/{self.dynamic_id}/target", data={"target_price": "100.00"})
        games = load_sync_games(get_db())
        for changed, price, expected_notices in [(100, "128.00", 0), (101, "100.00", 1),
                                                 (102, "88.00", 1), (103, "120.00", 1), (104, "90.00", 2)]:
            rows = [quote(game, price if game.game_id == self.dynamic_id else "14.99",
                          changed if game.game_id == self.dynamic_id else 100) for game in games]
            result, _, _ = self.run_sync([rows], now="2026-11-05 12:00:00 UTC")
            self.assertTrue(result.success)
            self.assertEqual(get_db().execute("SELECT COUNT(*) FROM notifications WHERE game_id=?", (self.dynamic_id,)).fetchone()[0], expected_notices)
        before = self.snapshot()
        self.assertEqual(self.run_sync([rows], now="2026-11-05 12:00:00 UTC")[0].inserted, 0)
        old_rows = [quote(game, price="0.00", changed=99) for game in games]
        self.assertEqual(self.run_sync([old_rows], now="2026-11-05 12:00:00 UTC")[0].inserted, 0)
        self.assertEqual(self.snapshot(), before)
        page = client.get(f"/games/{self.dynamic_id}")
        for text in ["$90.00", "$88.00", "2026-11-05 12:00:00 UTC", "5 次观测", "已收到 2 条降价通知"]:
            self.assertIn(text.encode(), page.data)
        self.assertEqual(get_db().execute("SELECT COUNT(*) FROM price_observations WHERE game_id<100").fetchone()[0], 20)

    def test_cli_counts_missing_titles_and_selection_excludes_ineligible_games(self):
        games = load_sync_games(get_db())
        result, _, _ = self.run_sync([[quote(game) for game in games[:-1]]], cli=True)
        self.assertEqual(result.exit_code, 0, repr(result.exception))
        for expected in ["用户追踪关系总数（真实）5", "去重后待同步游戏数 5", "收到报价数 4", "新增历史数 4", "缺失数 1", "通知数 0", "动态第五款", "9999"]:
            self.assertIn(expected, result.output)
        get_db().execute("UPDATE games SET steam_app_id=NULL WHERE id=101")
        get_db().execute("DELETE FROM user_game_tracking WHERE game_id=102")
        get_db().execute("UPDATE games SET currency='EUR' WHERE id=103")
        get_db().execute("UPDATE games SET platform='GOG' WHERE id=104")
        get_db().commit()
        self.assertEqual([game.game_id for game in load_sync_games(get_db())], [self.dynamic_id])
        result, _, calls = self.run_sync([[]], cli=True)
        self.assertEqual(result.exit_code, 0)
        for expected in ["用户追踪关系总数（真实）4", "去重后待同步游戏数 1", "缺失数 1", "跳过", "3 款"]:
            self.assertIn(expected, result.output)
        self.assertEqual(parse_qs(urlparse(calls[0].args[0].full_url).query)["steamAppID"], ["9999"])

    def test_empty_selection_does_not_make_network_requests(self):
        get_db().execute("DELETE FROM user_game_tracking WHERE game_id IN (SELECT id FROM games WHERE data_source='cheapshark')")
        get_db().commit()
        with patch("adapters.cheapshark.urlopen") as opener:
            result = self.runner.invoke(args=["sync-prices", "--source", "cheapshark"])
            self.assertEqual(result.exit_code, 0)
            opener.assert_not_called()
        self.assertIn("用户追踪关系总数（真实）0", result.output)

    def test_duplicate_target_mapping_and_unexpected_pagination_fail(self):
        games = load_sync_games(get_db())
        before = self.snapshot()
        duplicated = games + [PriceSyncGame(999, games[0].steam_app_id, "错误副本")]
        with patch("adapters.cheapshark.urlopen") as opener:
            result = sync_prices(get_db(), CheapSharkSource(duplicated))
            self.assertFalse(result.success)
            opener.assert_not_called()
        for page_count in ["invalid", "2", "-1"]:
            result, _, _ = self.run_sync([ApiResponse([quote(games[0])], {"X-Total-Page-Count": page_count})])
            self.assertFalse(result.success)
            self.assertEqual(self.snapshot(), before)

    def test_database_write_error_rolls_back_all_validated_prices(self):
        db = get_db()
        db.execute("""
            CREATE TRIGGER reject_round BEFORE INSERT ON price_observations
            WHEN (SELECT COUNT(*) FROM price_observations WHERE game_id>=100)>=2
            BEGIN SELECT RAISE(ABORT, 'test write failure'); END
        """)
        before = self.snapshot()
        with self.assertRaises(sqlite3.IntegrityError):
            self.run_sync(self.payloads(load_sync_games(db)))
        self.assertEqual(self.snapshot(), before)
        self.assertIsNone(db.execute("SELECT last_success_at FROM sync_status WHERE source_name='cheapshark-steam-usd'").fetchone())
