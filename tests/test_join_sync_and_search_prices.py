from tests.helpers import business_client
import io
import json
import unittest
from urllib.error import HTTPError
from urllib.parse import parse_qs, urlparse
from unittest.mock import patch

from app import app, get_db
from adapters.cheapshark import CheapSharkSource
from services.price_sync import load_sync_games, sync_real_prices


class Response(io.BytesIO):
    def __init__(self, payload):
        super().__init__(json.dumps(payload).encode())
        self.headers = {}


class JoinSyncAndSearchPriceTest(unittest.TestCase):
    def setUp(self):
        self.original_config = dict(app.config)
        app.config.update(TESTING=True, DATABASE=":memory:")
        self.ctx = app.app_context()
        self.ctx.push()
        self.assertEqual(app.test_cli_runner().invoke(args=["init-db"]).exit_code, 0)
        self.client = business_client()

    def tearDown(self):
        self.ctx.pop()
        app.config.update(self.original_config)

    def http_mock(self, payloads):
        iterator = iter(payloads)

        def respond(request, **kwargs):
            self.assertEqual(kwargs["timeout"], 10)
            self.assertIn("GamePriceTracker", request.headers["User-agent"])
            payload = next(iterator)
            if isinstance(payload, Exception):
                raise payload
            return Response(payload)

        return patch("adapters.cheapshark.urlopen", side_effect=respond)

    def quote(self, app_id, price="19.99"):
        return {"steamAppID": app_id, "storeID": "1", "salePrice": price, "lastChange": 100}

    def lookup(self):
        return {"info": {"title": "新加入的游戏", "steamAppID": "9999"},
                "deals": [{"storeID": "2", "price": "0.01"}, {"storeID": "1", "price": "19.99"}]}

    def all_quotes(self, include_new=True):
        app_ids = [game.steam_app_id for game in load_sync_games(get_db())]
        if include_new and "9999" not in app_ids:
            app_ids.append("9999")
        return [self.quote(app_id) for app_id in app_ids]

    def post_join(self):
        return self.client.post("/games/track", data={"cheapshark_game_id": "555"})

    def test_join_automatically_syncs_entire_list_and_shows_new_price(self):
        with self.http_mock([self.lookup(), self.all_quotes()]) as http:
            result = self.post_join()
        self.assertEqual(result.status_code, 303)
        self.assertEqual(http.call_count, 2)
        params = parse_qs(urlparse(http.call_args_list[1].args[0].full_url).query)
        self.assertEqual(params["storeID"], ["1"])
        self.assertEqual(len(params["steamAppID"][0].split(",")), 5)
        row = get_db().execute("SELECT id FROM games WHERE cheapshark_game_id='555'").fetchone()
        price = get_db().execute("SELECT price_cents,typeof(price_cents) FROM price_observations WHERE game_id=?", (row["id"],)).fetchone()
        self.assertEqual(tuple(price), (1999, "integer"))
        self.assertEqual(get_db().execute("SELECT COUNT(*) FROM price_observations WHERE game_id>=100").fetchone()[0], 5)
        self.assertEqual(get_db().execute("SELECT COUNT(*) FROM notifications").fetchone()[0], 0)
        page = self.client.get(result.location)
        self.assertIn(b"$19.99", page.data)
        self.assertIn("已自动同步追踪列表".encode(), page.data)
        self.assertNotIn("暂无已记录报价".encode(), page.data)

    def test_auto_sync_failure_retains_join_and_previous_prices_and_success_time(self):
        with self.http_mock([self.all_quotes(include_new=False)]):
            baseline = sync_real_prices(get_db())
        self.assertTrue(baseline.result.success)
        before_prices = [tuple(row) for row in get_db().execute("SELECT * FROM price_observations ORDER BY id")]
        before_success = get_db().execute("SELECT last_success_at FROM sync_status WHERE source_name='cheapshark-steam-usd'").fetchone()[0]
        with self.http_mock([self.lookup(), TimeoutError()]):
            result = self.post_join()
        self.assertEqual(result.status_code, 303)
        self.assertIsNotNone(get_db().execute("SELECT id FROM games WHERE cheapshark_game_id='555'").fetchone())
        self.assertEqual(before_prices, [tuple(row) for row in get_db().execute("SELECT * FROM price_observations ORDER BY id")])
        self.assertEqual(before_success, get_db().execute("SELECT last_success_at FROM sync_status WHERE source_name='cheapshark-steam-usd'").fetchone()[0])
        page = self.client.get(result.location)
        self.assertIn("追踪已保存，但自动同步失败".encode(), page.data)
        self.assertIn("暂无已记录报价".encode(), page.data)

    def test_join_with_missing_followup_quote_does_not_save_lookup_or_cheapest_price(self):
        with self.http_mock([self.lookup(), []]):
            result = self.post_join()
        new_id = get_db().execute("SELECT id FROM games WHERE cheapshark_game_id='555'").fetchone()[0]
        self.assertEqual(get_db().execute("SELECT COUNT(*) FROM price_observations WHERE game_id=?", (new_id,)).fetchone()[0], 0)
        page = self.client.get(result.location)
        self.assertIn("该游戏本次没有 Steam 报价".encode(), page.data)
        self.assertIn("暂无已记录报价".encode(), page.data)

    def test_repeated_join_auto_sync_is_idempotent(self):
        for _ in range(2):
            with self.http_mock([self.lookup(), self.all_quotes()]):
                self.assertEqual(self.post_join().status_code, 303)
        self.assertEqual(get_db().execute("SELECT COUNT(*) FROM games WHERE cheapshark_game_id='555'").fetchone()[0], 1)
        self.assertEqual(get_db().execute("SELECT COUNT(*) FROM price_observations WHERE game_id>=100").fetchone()[0], 5)

    def test_search_shows_steam_price_before_secondary_cross_store_reference_and_does_not_write(self):
        catalog = [{"gameID": "555", "external": "游戏", "steamAppID": "9999", "cheapest": "0.01"}]
        with self.http_mock([catalog, [self.quote("9999")]]) as http:
            page = self.client.get("/games/search?q=Game")
        self.assertEqual(http.call_count, 2)
        self.assertEqual(page.status_code, 200)
        text = page.data.decode()
        self.assertLess(text.index("Steam 当前报价"), text.index("跨商店最低报价"))
        self.assertLess(text.index("$19.99"), text.index("$0.01"))
        self.assertIn('class="reference-price"', text)
        self.assertEqual(get_db().execute("SELECT COUNT(*) FROM price_observations").fetchone()[0], 20)
        self.assertEqual(get_db().execute("SELECT COUNT(*) FROM games").fetchone()[0], 8)
        self.assertEqual(get_db().execute("SELECT COUNT(*) FROM sync_status").fetchone()[0], 0)

    def test_search_zero_missing_and_failed_prices_never_fall_back_to_cross_store_price(self):
        catalog = [
            {"gameID": "555", "external": "Zero", "steamAppID": "9999", "cheapest": "0.01"},
            {"gameID": "556", "external": "Missing", "steamAppID": "8888", "cheapest": "0.01"},
            {"gameID": "557", "external": "No Steam", "steamAppID": None, "cheapest": "0.01"},
        ]
        with self.http_mock([catalog, [self.quote("9999", "0.00")]]):
            results = CheapSharkSource().search_by_title("Game")
        self.assertEqual([item.steam_price_cents for item in results], [0, None, None])
        limited = HTTPError("https://example.invalid", 429, "limited", {"Retry-After": "60"}, None)
        try:
            for error in [TimeoutError(), limited]:
                with self.subTest(error=type(error).__name__), self.http_mock([catalog, error]):
                    page = self.client.get("/games/search?q=Game")
                self.assertEqual(page.status_code, 200)
                self.assertIn("Steam 报价暂不可用".encode(), page.data)
                self.assertIn("跨商店最低报价".encode(), page.data)
                self.assertNotIn(b"<strong>$0.01</strong>", page.data)
                self.assertNotIn(b"Traceback", page.data)
        finally:
            limited.close()
