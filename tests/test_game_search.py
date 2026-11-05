import sqlite3
import unittest
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import parse_qs, urlparse
from unittest.mock import patch

from adapters.cheapshark import CheapSharkSource, GameSearchResult
from adapters.contracts import PriceSourceError
from app import app


class CheapSharkGameSearchAdapterTest(unittest.TestCase):
    def search_rows(self, query, rows):
        with patch("adapters.cheapshark.urlopen") as opener:
            opener.return_value.__enter__.return_value.headers = {}
            steam_rows = [{"storeID": "1", "steamAppID": row["steamAppID"], "salePrice": "14.99", "lastChange": 100}
                          for row in rows if row.get("steamAppID")]
            with patch("adapters.cheapshark.json.load", side_effect=[rows, steam_rows]):
                results = CheapSharkSource().search_by_title(query)
            request = opener.call_args_list[0].args[0]
            self.assertEqual(opener.call_args.kwargs["timeout"], 10)
        return results, request

    def test_normal_result_and_standard_url_encoding(self):
        rows = [{
            "gameID": "123",
            "steamAppID": "456",
            "external": "中文 & 100% Game",
            "cheapest": "12.34",
        }]
        results, request = self.search_rows("  中文 & 100%  ", rows)
        self.assertEqual(results, [GameSearchResult("123", "中文 & 100% Game", "456", 1234, 1499)])
        self.assertEqual(parse_qs(urlparse(request.full_url).query)["title"], ["中文 & 100%"])
        self.assertIn("GamePriceTracker-Learning", request.headers["User-agent"])

    def test_empty_results_and_missing_steam_app_id(self):
        results, _ = self.search_rows("nothing", [])
        self.assertEqual(results, [])
        results, _ = self.search_rows("console game", [{
            "gameID": "9", "steamAppID": None, "external": "Console Game", "cheapest": None
        }])
        self.assertIsNone(results[0].steam_app_id)
        self.assertIsNone(results[0].cheapest_cents)

    def test_rate_limit_timeout_and_malformed_responses(self):
        rate_limit = HTTPError("https://example.invalid", 429, "limited", {"Retry-After": "60"}, None)
        for failure, expected in [
            (rate_limit, "Retry-After=60"),
            (TimeoutError(), "超时"),
            (URLError("offline"), "网络"),
        ]:
            with self.subTest(expected=expected), patch("adapters.cheapshark.urlopen", side_effect=failure):
                with self.assertRaisesRegex(PriceSourceError, expected):
                    CheapSharkSource().search_by_title("Hades")
        rate_limit.close()

        malformed = [
            {},
            ["not-an-object"],
            [{"gameID": "bad", "external": "Bad ID"}],
            [{"gameID": "1", "external": "", "steamAppID": "2"}],
            [{"gameID": "1", "external": "Bad Steam ID", "steamAppID": "abc"}],
            [{"gameID": "1", "external": "Bad Price", "cheapest": "NaN"}],
        ]
        for rows in malformed:
            with self.subTest(rows=rows), patch("adapters.cheapshark.urlopen"):
                with patch("adapters.cheapshark.json.load", return_value=rows):
                    with self.assertRaises(PriceSourceError):
                        CheapSharkSource().search_by_title("Hades")


class GameSearchPageTest(unittest.TestCase):
    def setUp(self):
        self.original_config = dict(app.config)
        self.database_path = Path(__file__).parent / "_test_game_search.sqlite3"
        self.database_path.unlink(missing_ok=True)
        app.config.update(TESTING=True, DATABASE=self.database_path)
        result = app.test_cli_runner().invoke(args=["init-db"])
        self.assertEqual(result.exit_code, 0, result.output)
        self.client = app.test_client()

    def tearDown(self):
        self.database_path.unlink(missing_ok=True)
        app.config.update(self.original_config)

    def database_counts(self):
        db = sqlite3.connect(self.database_path)
        try:
            return db.execute(
                "SELECT (SELECT COUNT(*) FROM games), (SELECT COUNT(*) FROM price_observations)"
            ).fetchone()
        finally:
            db.close()

    def test_empty_whitespace_and_too_long_queries_do_not_call_api(self):
        with patch("app.CheapSharkSource.search_by_title") as search:
            first = self.client.get("/games/search")
            empty = self.client.get("/games/search?q=")
            whitespace = self.client.get("/games/search?q=%20%20%20")
            too_long = self.client.get("/games/search?q=" + "a" * 81)
        search.assert_not_called()
        self.assertNotIn("请输入游戏名称".encode(), first.data)
        self.assertIn("请输入游戏名称".encode(), empty.data)
        self.assertIn("请输入游戏名称".encode(), whitespace.data)
        self.assertIn("不能超过 80".encode(), too_long.data)

    def test_results_are_escaped_and_missing_steam_id_is_explicit(self):
        results = [
            GameSearchResult("10", "<script>alert(1)</script>", "20", 499),
            GameSearchResult("11", "No Steam", None, None),
        ]
        with patch("app.CheapSharkSource.search_by_title", return_value=results):
            page = self.client.get("/games/search?q=game")
        self.assertEqual(page.status_code, 200)
        self.assertNotIn(b"<script>", page.data)
        self.assertIn(b"&lt;script&gt;", page.data)
        self.assertIn("未提供，不能假装为 Steam 游戏".encode(), page.data)
        self.assertIn("跨商店最低报价".encode(), page.data)
        self.assertIn(b"$4.99", page.data)

    def test_no_results_and_external_failure_leave_database_unchanged(self):
        before = self.database_counts()
        with patch("app.CheapSharkSource.search_by_title", return_value=[]):
            page = self.client.get("/games/search?q=does-not-exist")
        self.assertIn("没有找到".encode(), page.data)

        with patch(
            "app.CheapSharkSource.search_by_title",
            side_effect=PriceSourceError("CheapShark 搜索网络、代理或超时错误，请稍后重试。"),
        ):
            page = self.client.get("/games/search?q=Hades")
        self.assertEqual(page.status_code, 200)
        self.assertIn("请稍后重试".encode(), page.data)
        self.assertNotIn(b"Traceback", page.data)
        self.assertEqual(self.database_counts(), before)


if __name__ == "__main__":
    unittest.main()
