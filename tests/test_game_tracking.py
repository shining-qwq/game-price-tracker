import sqlite3
import unittest
from pathlib import Path
from html.parser import HTMLParser
from types import SimpleNamespace
from urllib.error import HTTPError, URLError
from urllib.parse import parse_qs, urlparse
from unittest.mock import patch

from app import app, get_db, seed_demo_data
from adapters.cheapshark import CheapSharkSource, GameSearchResult
from adapters.contracts import PriceObservationEvent, PriceSourceError
from adapters.demo_price_source import DemoPriceSource
from migrations import upgrade_game_identifiers
from services.price_sync import sync_prices


def lookup_payload(steam_id="9999", title="服务端验证的游戏", price="19.99"):
    return {
        "info": {"title": title, "steamAppID": steam_id},
        "deals": [{"storeID": "2", "price": "0.01"}, {"storeID": "1", "price": price}],
        "cheapestPriceEver": {"price": "0.01"},
    }


class NavigationParser(HTMLParser):
    def __init__(self, html):
        super().__init__()
        self.section = None
        self.in_link = False
        self.bars = {}
        self.feed(html.decode())

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if tag == "nav":
            self.section = attrs.get("aria-label")
            self.bars.setdefault(self.section, [])
        if tag == "a" and self.section:
            self.in_link = True
            self.bars[self.section].append({"text": "", "href": attrs.get("href"), "active": attrs.get("aria-current") == "page"})

    def handle_data(self, data):
        if self.in_link:
            self.bars[self.section][-1]["text"] += data.strip()

    def handle_endtag(self, tag):
        if tag == "a":
            self.in_link = False
        if tag == "nav":
            self.section = None


class GameLookupTest(unittest.TestCase):
    def lookup(self, payload):
        with patch("adapters.cheapshark.urlopen") as opener:
            with patch("adapters.cheapshark.json.load", return_value=payload):
                result = CheapSharkSource().get_verified_steam_game("555")
            self.assertEqual(opener.call_args.kwargs["timeout"], 10)
            request = opener.call_args.args[0]
            self.assertEqual(parse_qs(urlparse(request.full_url).query), {"id": ["555"]})
            self.assertIn("GamePriceTracker", request.headers["User-agent"])
            return result

    def test_steam_offer_only_and_explicit_zero(self):
        verified = self.lookup(lookup_payload())
        self.assertEqual(verified.cheapshark_game_id, "555")
        self.assertEqual(verified.steam_price_cents, 1999)
        self.assertEqual(self.lookup(lookup_payload(price="0.00")).steam_price_cents, 0)

    def test_invalid_ids_never_request_network(self):
        with patch("adapters.cheapshark.urlopen") as opener:
            for value in ["", "0", "01", "-1", "1.2", "abc", "１", "1 OR 1=1", "1" * 21]:
                with self.subTest(value=value), self.assertRaises(ValueError):
                    CheapSharkSource().get_verified_steam_game(value)
            opener.assert_not_called()

    def test_missing_steam_and_malformed_payloads(self):
        payloads = [None, [], {}, {"info": None}, lookup_payload(steam_id=None),
                    lookup_payload(steam_id="abc"), lookup_payload(title=""),
                    lookup_payload(price=None), lookup_payload(price="NaN"),
                    {"info": {"title": "Game", "steamAppID": "9999"}, "deals": {}},
                    {"info": {"title": "Game", "steamAppID": "9999"}, "deals": ["bad"]},
                    {"info": {"title": "Game", "steamAppID": "9999"}, "deals": []},
                    {"info": {"title": "Game", "steamAppID": "9999"},
                     "deals": [{"storeID": "2", "price": "0.00"}]}]
        for payload in payloads:
            with self.subTest(payload=payload), self.assertRaises(PriceSourceError):
                self.lookup(payload)

    def test_http_429_network_timeout_and_bad_json(self):
        limited = HTTPError("https://example.invalid", 429, "limited", {"Retry-After": "60"}, None)
        missing = HTTPError("https://example.invalid", 404, "missing", {}, None)
        try:
            for error, message in [(limited, "Retry-After=60"), (missing, "HTTP 404"),
                                   (URLError("offline"), "网络"), (TimeoutError(), "超时")]:
                with self.subTest(message=message), patch("adapters.cheapshark.urlopen", side_effect=error):
                    with self.assertRaisesRegex(PriceSourceError, message):
                        CheapSharkSource().get_verified_steam_game("555")
            with patch("adapters.cheapshark.urlopen"), patch("adapters.cheapshark.json.load", side_effect=ValueError):
                with self.assertRaisesRegex(PriceSourceError, "格式错误"):
                    CheapSharkSource().get_verified_steam_game("555")
        finally:
            limited.close()
            missing.close()


class TrackingPageTest(unittest.TestCase):
    def setUp(self):
        self.original_config = dict(app.config)
        app.config.update(TESTING=True, DATABASE=":memory:")
        self.ctx = app.app_context()
        self.ctx.push()
        self.runner = app.test_cli_runner()
        self.assertEqual(self.runner.invoke(args=["init-db"]).exit_code, 0)
        self.client = app.test_client()

    def tearDown(self):
        self.ctx.pop()
        app.config.update(self.original_config)

    def snapshot(self):
        return {name: [tuple(row) for row in get_db().execute(f"SELECT * FROM {name} ORDER BY 1")]
                for name in ["games", "price_observations", "price_targets", "notifications", "sync_status"]}

    def post(self, game_id="555", payload=None, extra=None):
        with patch("adapters.cheapshark.urlopen"), patch(
            "adapters.cheapshark.json.load", return_value=payload if payload is not None else lookup_payload()
        ):
            return self.client.post("/games/track", data={"cheapshark_game_id": game_id, **(extra or {})})

    def test_first_add_uses_verified_metadata_and_no_history_or_fake_pair(self):
        result = self.post(extra={"title": "伪造标题", "price": "0", "user_id": "999", "game_id": "1"})
        self.assertEqual(result.status_code, 303)
        row = get_db().execute("SELECT * FROM games WHERE cheapshark_game_id='555'").fetchone()
        self.assertEqual((row["title"], row["steam_app_id"], row["platform"], row["currency"], row["data_source"]),
                         ("服务端验证的游戏", "9999", "Steam", "USD", "cheapshark"))
        self.assertEqual(row["subtitle"], "外部数据未提供")
        self.assertEqual(row["genre"], "外部数据未提供")
        self.assertIsNone(row["demo_game_id"])
        self.assertEqual(get_db().execute("SELECT COUNT(*) FROM price_observations WHERE game_id=?", (row["id"],)).fetchone()[0], 0)
        page = self.client.get(result.location)
        self.assertEqual(page.status_code, 200)
        self.assertIn("暂无已记录报价".encode(), page.data)
        self.assertIn(b"9999", page.data)
        self.assertNotIn(f'/games/{row["id"] - 100}"'.encode(), page.data)
        self.assertIn("当前批量同步仍只处理原四款".encode(), page.data)

    def test_duplicate_reposts_and_existing_legacy_game(self):
        first = self.post()
        second = self.post()
        self.assertEqual(first.location, second.location)
        self.assertEqual(get_db().execute("SELECT COUNT(*) FROM games").fetchone()[0], 9)
        legacy = self.post(game_id="150036", payload=lookup_payload(steam_id="413150", title="Stardew Valley"))
        self.assertTrue(legacy.location.endswith("/games/101"))
        self.assertEqual(get_db().execute("SELECT COUNT(*) FROM games").fetchone()[0], 9)
        self.assertEqual(get_db().execute("SELECT cheapshark_game_id FROM games WHERE id=101").fetchone()[0], "150036")

    def test_invalid_id_and_external_failures_do_not_create_partial_records(self):
        before = self.snapshot()
        with patch("adapters.cheapshark.urlopen") as opener:
            invalid = self.client.post("/games/track", data={"cheapshark_game_id": "1 OR 1=1"})
            opener.assert_not_called()
        self.assertEqual(invalid.status_code, 303)
        self.assertEqual(self.snapshot(), before)
        payloads = [lookup_payload(steam_id=None), lookup_payload(price=None),
                    {"info": {"title": "Game", "steamAppID": "9999"}, "deals": []}, {}]
        for payload in payloads:
            with self.subTest(payload=payload):
                response = self.post(payload=payload)
                self.assertTrue(response.location.endswith("/games/search"))
                self.assertEqual(self.snapshot(), before)
                self.assertIn(b'role="status"', self.client.get(response.location).data)
        with patch("adapters.cheapshark.urlopen", side_effect=TimeoutError):
            response = self.client.post("/games/track", data={"cheapshark_game_id": "555"}, follow_redirects=True)
        self.assertIn("超时".encode(), response.data)
        self.assertNotIn(b"Traceback", response.data)
        self.assertEqual(self.snapshot(), before)

    def test_unique_indexes_reject_duplicate_external_ids_and_conflicts(self):
        self.post()
        db = get_db()
        for cheap_id, steam_id in [("555", "8888"), ("666", "9999")]:
            with self.subTest(cheap_id=cheap_id), self.assertRaises(sqlite3.IntegrityError):
                with db:
                    db.execute("""
                        INSERT INTO games
                            (title, subtitle, genre, platform, region, currency, data_source,
                             accent, cheapshark_game_id, steam_app_id)
                        VALUES ('Duplicate', 'Unknown', 'Unknown', 'Steam', 'Unknown',
                                'USD', 'cheapshark', '#5066a6', ?, ?)
                    """, (cheap_id, steam_id))
        before = self.snapshot()
        conflict = self.post(game_id="555", payload=lookup_payload(steam_id="8888"))
        self.assertTrue(conflict.location.endswith("/games/search"))
        self.assertEqual(self.snapshot(), before)

    def test_post_only_buttons_and_original_details(self):
        self.assertEqual(self.client.get("/games/track").status_code, 405)
        with patch("app.CheapSharkSource.search_by_title", return_value=[
            GameSearchResult("555", "Game", "9999", 1), GameSearchResult("666", "No Steam", None, None)
        ]):
            page = self.client.get("/games/search?q=game")
        self.assertIn(b'method="post" action="/games/track"', page.data)
        self.assertEqual(page.data.count(b'name="cheapshark_game_id"'), 1)
        for demo_id, real_id in [(1, 101), (2, 102), (3, 103), (4, 104)]:
            for current, other in [(demo_id, real_id), (real_id, demo_id)]:
                page = self.client.get(f"/games/{current}")
                self.assertEqual(page.status_code, 200)
                self.assertIn(f'href="/games/{other}"'.encode(), page.data)

    def test_search_state_cancel_and_restore_preserve_all_business_records(self):
        first = self.post()
        game_id = get_db().execute("SELECT id FROM games WHERE cheapshark_game_id='555'").fetchone()[0]
        self.client.post(f"/games/{game_id}/target", data={"target_price": "100.00"})
        source = SimpleNamespace(name="tracking-test", fetch=lambda scenario: [
            PriceObservationEvent("track-high", game_id, 12800, "2026-11-01 10:00:00"),
            PriceObservationEvent("track-drop", game_id, 9000, "2026-11-02 10:00:00"),
        ])
        self.assertEqual(sync_prices(get_db(), source).notifications, 1)
        results = [GameSearchResult("555", "Game", "9999", 100)]
        with patch("app.CheapSharkSource.search_by_title", return_value=results):
            page = self.client.get("/games/search?q=Game")
        self.assertIn('title="点击取消追踪">已追踪'.encode(), page.data)
        before = self.snapshot()
        with patch("adapters.cheapshark.urlopen", side_effect=AssertionError("Cancel must not call API")):
            for _ in range(2):
                response = self.client.post("/games/untrack", data={"steam_app_id": "9999"})
                self.assertEqual(response.status_code, 303)
        self.assertIn("q=Game", response.location)
        after = self.snapshot()
        for name in before:
            if name != "games":
                self.assertEqual(before[name], after[name])
        self.assertEqual(get_db().execute("SELECT is_tracked FROM games WHERE id=?", (game_id,)).fetchone()[0], 0)
        with patch("app.CheapSharkSource.search_by_title", return_value=results):
            page = self.client.get(response.location)
        self.assertIn('>加入追踪</button>'.encode(), page.data)
        self.assertNotIn(b'action="/games/untrack"', page.data)
        old_detail = self.client.get(first.location)
        self.assertEqual(old_detail.status_code, 200)
        self.assertIn("已取消追踪".encode(), old_detail.data)
        self.assertIn(b"$90.00", old_detail.data)
        restored = self.post()
        self.assertEqual(restored.location, first.location)
        self.assertEqual(get_db().execute("SELECT is_tracked FROM games WHERE id=?", (game_id,)).fetchone()[0], 1)
        self.assertEqual(len(self.snapshot()["games"]), len(before["games"]))
        with patch("app.CheapSharkSource.search_by_title", return_value=results):
            self.assertIn('>已追踪</button>'.encode(), self.client.get("/games/search?q=Game").data)

    def test_legacy_game_matches_steam_id_and_cancelled_sync_is_skipped(self):
        self.assertIsNone(get_db().execute("SELECT cheapshark_game_id FROM games WHERE id=101").fetchone()[0])
        with patch("app.CheapSharkSource.search_by_title", return_value=[GameSearchResult("150036", "Stardew", "413150", 1499)]):
            page = self.client.get("/games/search?q=Stardew")
        self.assertIn('>已追踪</button>'.encode(), page.data)
        self.assertIn(b'value="413150"', page.data)
        self.client.post("/games/untrack", data={"steam_app_id": "413150"})
        before = get_db().execute("SELECT COUNT(*) FROM price_observations").fetchone()[0]
        source = SimpleNamespace(name="cancel-test", fetch=lambda scenario: [
            PriceObservationEvent("late-inflight", 101, 100, "2026-12-01 10:00:00")
        ])
        result = sync_prices(get_db(), source)
        self.assertEqual((result.inserted, result.notifications), (0, 0))
        self.assertEqual(get_db().execute("SELECT COUNT(*) FROM price_observations").fetchone()[0], before)
        with patch("adapters.cheapshark.urlopen") as opener, patch("adapters.cheapshark.json.load", return_value=[]):
            opener.return_value.__enter__.return_value.headers = {}
            result = self.runner.invoke(args=["sync-prices", "--source", "cheapshark"])
            self.assertEqual(result.exit_code, 0, repr(result.exception))
            params = parse_qs(urlparse(opener.call_args.args[0].full_url).query)
            self.assertNotIn("413150", params["steamAppID"][0].split(","))
        self.assertEqual(self.client.get("/games/untrack").status_code, 405)

    def test_navigation_order_is_stable_and_second_bar_contains_only_games(self):
        for path, section in [("/games/1", "模拟数据"), ("/games/101", "真实报价"),
                              ("/games/search", "搜索并加入游戏"), ("/notifications", None)]:
            with self.subTest(path=path):
                parser = NavigationParser(self.client.get(path).data)
                links = parser.bars["功能分类"]
                self.assertEqual([item["text"] for item in links], ["模拟数据", "真实报价", "搜索并加入游戏"])
                self.assertEqual([item["text"] for item in links if item["active"]], [section] if section else [])
                if section in ("模拟数据", "真实报价"):
                    self.assertEqual([item["text"] for item in parser.bars["游戏列表"]],
                                     ["星露谷物语", "空洞骑士", "双人成行", "潜水员戴夫"])
        get_db().execute("UPDATE games SET is_tracked=0 WHERE data_source='cheapshark'")
        get_db().commit()
        page = self.client.get("/games?source=cheapshark")
        self.assertEqual(page.status_code, 200)
        self.assertIn("暂无已追踪游戏".encode(), page.data)
        parser = NavigationParser(page.data)
        self.assertEqual([item["text"] for item in parser.bars["功能分类"]], ["模拟数据", "真实报价", "搜索并加入游戏"])
        with patch("adapters.cheapshark.urlopen") as opener:
            result = self.runner.invoke(args=["sync-prices", "--source", "cheapshark"])
            self.assertEqual(result.exit_code, 0)
            opener.assert_not_called()


class IdentifierUpgradeTest(unittest.TestCase):
    def test_tracking_flag_upgrade_preserves_records_and_cancelled_state(self):
        db = sqlite3.connect(":memory:")
        db.row_factory = sqlite3.Row
        try:
            root = Path(__file__).resolve().parents[1]
            schema = (root / "schema.sql").read_text(encoding="utf-8").replace(
                "    is_tracked INTEGER NOT NULL DEFAULT 1 CHECK (is_tracked IN (0, 1)),\n", ""
            )
            db.executescript(schema)
            db.executescript((root / "notifications.sql").read_text(encoding="utf-8"))
            seed_demo_data(db)
            db.commit()
            before = [tuple(row) for row in db.execute("SELECT * FROM price_observations ORDER BY id")]
            upgrade_game_identifiers(db)
            self.assertEqual(db.execute("SELECT COUNT(*) FROM games WHERE is_tracked=1").fetchone()[0], 8)
            db.execute("UPDATE games SET is_tracked=0 WHERE id=101")
            db.commit()
            upgrade_game_identifiers(db)
            self.assertEqual(db.execute("SELECT is_tracked FROM games WHERE id=101").fetchone()[0], 0)
            self.assertEqual(before, [tuple(row) for row in db.execute("SELECT * FROM price_observations ORDER BY id")])
        finally:
            db.close()

    def test_failed_upgrade_rolls_back_metadata_and_index_changes(self):
        db = sqlite3.connect(":memory:")
        db.row_factory = sqlite3.Row
        try:
            root = Path(__file__).resolve().parents[1]
            db.executescript((root / "schema.sql").read_text(encoding="utf-8"))
            seed_demo_data(db)
            # Simulate conflicting pre-existing external metadata, before indexes.
            db.execute("UPDATE games SET steam_app_id='413150' WHERE id=102")
            db.commit()
            before = [tuple(row) for row in db.execute("SELECT * FROM games ORDER BY id")]
            with self.assertRaises(sqlite3.IntegrityError):
                upgrade_game_identifiers(db)
            self.assertEqual(before, [tuple(row) for row in db.execute("SELECT * FROM games ORDER BY id")])
            self.assertEqual(db.execute(
                "SELECT name FROM sqlite_master WHERE type='index' AND name LIKE 'uq_real_%'"
            ).fetchall(), [])
        finally:
            db.close()

    def test_legacy_upgrade_is_repeatable_and_preserves_business_records(self):
        db = sqlite3.connect(":memory:")
        db.row_factory = sqlite3.Row
        db.execute("PRAGMA foreign_keys=ON")
        try:
            root = Path(__file__).resolve().parents[1]
            schema = (root / "schema.sql").read_text(encoding="utf-8")
            for column in ["    cheapshark_game_id TEXT,\n", "    steam_app_id TEXT,\n", "    demo_game_id INTEGER REFERENCES games(id),\n"]:
                schema = schema.replace(column, "")
            db.executescript(schema)
            db.executescript((root / "notifications.sql").read_text(encoding="utf-8"))
            seed_demo_data(db)
            db.commit()
            sync_prices(db, DemoPriceSource(), scenario="sequence")
            tables = ["price_observations", "price_targets", "notifications", "sync_status"]
            before = {table: [tuple(row) for row in db.execute(f"SELECT * FROM {table} ORDER BY 1")] for table in tables}
            old_games = [tuple(row) for row in db.execute("SELECT * FROM games ORDER BY id")]
            upgrade_game_identifiers(db)
            first = [tuple(row) for row in db.execute("SELECT * FROM games ORDER BY id")]
            upgrade_game_identifiers(db)
            self.assertEqual(first, [tuple(row) for row in db.execute("SELECT * FROM games ORDER BY id")])
            self.assertEqual(old_games, [row[:len(old_games[0])] for row in first])
            self.assertEqual(before, {table: [tuple(row) for row in db.execute(f"SELECT * FROM {table} ORDER BY 1")] for table in tables})
            self.assertEqual(db.execute("SELECT steam_app_id, demo_game_id FROM games WHERE id=101").fetchone()[:], ("413150", 1))
            self.assertEqual(db.execute("PRAGMA foreign_key_check").fetchall(), [])
        finally:
            db.close()
