import contextlib
import io
import json
import unittest
from unittest.mock import patch

from scripts.probe_cheapshark import classify_price, main, probe_game


class ExternalProbeTest(unittest.TestCase):
    def test_missing_price_is_not_free(self):
        for value in [None, ""]:
            self.assertEqual(classify_price(value), {"price_state": "missing", "price_cents": None})
        self.assertEqual(classify_price("0.00"), {"price_state": "zero_price", "price_cents": 0})
        self.assertEqual(classify_price("14.99"), {"price_state": "paid", "price_cents": 1499})
        for value in ["NaN", "Infinity", "-1", "12.345", "unknown"]:
            self.assertEqual(classify_price(value)["price_state"], "invalid")

    def test_empty_response_and_wrong_store_are_not_importable_prices(self):
        with patch("scripts.probe_cheapshark.urlopen") as opener:
            response = opener.return_value.__enter__.return_value
            response.status = 200
            with patch("scripts.probe_cheapshark.json.load", return_value=[]):
                self.assertEqual(probe_game("Dota 2", "570")["price_state"], "missing")
            with patch("scripts.probe_cheapshark.json.load", return_value=[
                {"steamAppID": "570", "storeID": "2", "salePrice": "0.00"}
            ]):
                self.assertIn("error", probe_game("Dota 2", "570"))
            self.assertEqual(opener.call_args.kwargs["timeout"], 10)

    def test_rate_limit_stops_further_requests(self):
        with patch("scripts.probe_cheapshark.probe_game", return_value={
            "http_status": 429, "retry_after": "60"
        }) as probe:
            output = io.StringIO()
            with contextlib.redirect_stdout(output):
                main()
            self.assertEqual(probe.call_count, 1)
            self.assertFalse(json.loads(output.getvalue())["business_database_written"])


if __name__ == "__main__":
    unittest.main()
