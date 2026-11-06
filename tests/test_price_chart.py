"""Offline chart rendering, integer scaling and unchanged read-only detail pages."""
from datetime import datetime, timedelta
from decimal import Decimal
import unittest
from unittest.mock import patch
from xml.etree import ElementTree

from app import app, get_db
from price_chart import build_price_chart


SVG = {"svg": "http://www.w3.org/2000/svg"}


def observation(record_id, cents, timestamp):
    return {"id": record_id, "price_cents": cents, "observed_at": timestamp}


def coordinates(chart):
    return [tuple(Decimal(value) for value in pair.split(","))
            for pair in chart["points"].split()]


class PriceChartCoordinatesTest(unittest.TestCase):
    def test_empty_history_has_no_invented_zero_price(self):
        self.assertEqual(build_price_chart([]), {"count": 0})

    def test_unsorted_history_uses_actual_time_spacing_without_mutating_input(self):
        rows = [observation(3, 8800, "2026-11-06 10:00:00"),
                observation(1, 12800, "2026-11-06 00:00:00"),
                observation(2, 10000, "2026-11-06 01:00:00")]
        before = [dict(row) for row in rows]
        chart = build_price_chart(rows)
        points = coordinates(chart)
        self.assertLess(points[0][0], points[1][0])
        self.assertLess(points[1][0], points[2][0])
        self.assertLess(points[0][1], points[1][1])
        self.assertLess(points[1][1], points[2][1])
        self.assertLessEqual(abs((points[1][0] - points[0][0]) * 10 - (points[2][0] - points[0][0])), Decimal("0.10"))
        self.assertEqual([item["price_cents"] for item in chart["markers"]], [12800, 10000, 8800])
        self.assertEqual(rows, before)

    def test_same_time_records_use_stable_ids_and_never_divide_by_zero(self):
        rows = [observation(2, 0, "2026-11-06 10:00:00"), observation(1, 999, "2026-11-06 10:00:00")]
        chart = build_price_chart(rows)
        points = coordinates(chart)
        self.assertEqual(points[0][0], points[1][0])
        self.assertEqual([item["price_cents"] for item in chart["markers"]], [999, 0])
        self.assertEqual(len(chart["x_ticks"]), 1)

    def test_one_point_is_centered_and_retains_exact_cents(self):
        chart = build_price_chart([observation(1, 101, "2026-11-06 10:00:00")])
        self.assertEqual(chart["count"], 1)
        self.assertEqual(coordinates(chart)[0][0], Decimal(chart["left"] + chart["right"]) / 2)
        self.assertEqual(chart["markers"][0]["price_cents"], 101)

    def test_equal_prices_including_zero_make_finite_horizontal_lines(self):
        for cents in [0, 2900]:
            with self.subTest(cents=cents):
                chart = build_price_chart([observation(1, cents, "2026-11-06 00:00:00"),
                                           observation(2, cents, "2026-11-07 00:00:00")])
                points = coordinates(chart)
                self.assertEqual(points[0][1], points[1][1])
                self.assertTrue(all(value.is_finite() for point in points for value in point))
                self.assertGreater(chart["y_ticks"][-1]["price_cents"], cents)

    def test_full_sqlite_integer_range_fits_inside_plot(self):
        largest = 9223372036854775807
        chart = build_price_chart([observation(1, 0, "2026-11-06 00:00:00"),
                                   observation(2, largest, "2026-11-07 00:00:00")])
        self.assertEqual(chart["highest_cents"], largest)
        self.assertGreater(chart["y_ticks"][-1]["price_cents"], largest)
        for x, y in coordinates(chart):
            self.assertTrue(x.is_finite() and y.is_finite())
            self.assertLessEqual(chart["left"], x)
            self.assertLessEqual(x, chart["right"])
            self.assertLessEqual(chart["top"], y)
            self.assertLessEqual(y, chart["bottom"])

    def test_long_history_keeps_every_line_point_but_bounds_circle_count(self):
        start = datetime(2026, 1, 1)
        rows = [observation(index, (index % 20) * 100, (start + timedelta(minutes=index)).isoformat(sep=" "))
                for index in range(5000)]
        chart = build_price_chart(list(reversed(rows)))
        self.assertEqual(chart["count"], 5000)
        self.assertEqual(len(coordinates(chart)), 5000)
        self.assertEqual(len(chart["markers"]), 2)
        self.assertTrue(chart["markers_limited"])
        self.assertEqual(chart["markers"][0]["observed_at"], rows[0]["observed_at"])
        self.assertEqual(chart["markers"][-1]["observed_at"], rows[-1]["observed_at"])


class PriceChartPageTest(unittest.TestCase):
    def setUp(self):
        self.previous_config = dict(app.config)
        app.config.update(TESTING=True, DATABASE=":memory:")
        self.ctx = app.app_context()
        self.ctx.push()
        self.assertEqual(app.test_cli_runner().invoke(args=["init-db"]).exit_code, 0)
        self.client = app.test_client()
        self.network = patch("adapters.cheapshark.urlopen", side_effect=AssertionError("Charts stay offline"))
        self.network.start()
        self.addCleanup(self.network.stop)

    def tearDown(self):
        self.ctx.pop()
        app.config.update(self.previous_config)

    def insert_prices(self, prices, game_id=101):
        with get_db() as db:
            for index, (cents, timestamp) in enumerate(prices):
                db.execute("""INSERT INTO price_observations(source_event_id,game_id,price_cents,observed_at)
                              VALUES (?,?,?,?)""", (f"chart-{game_id}-{index}", game_id, cents, timestamp))

    def page(self, game_id=101):
        response = self.client.get(f"/games/{game_id}")
        self.assertEqual(response.status_code, 200)
        return response.get_data(as_text=True)

    def svg(self, html):
        return ElementTree.fromstring("<svg" + html.split("<svg", 1)[1].split("</svg>", 1)[0] + "</svg>")

    def test_empty_real_history_has_explanation_and_accessible_table(self):
        html = self.page()
        self.assertIn("暂无已记录报价", html)
        self.assertIn("缺失报价不代表免费", html)
        self.assertNotIn("<svg", html)
        self.assertNotIn("$0.00", html)
        self.assertIn('id="price-history-table"', html)
        self.assertIn('tabindex="0" role="region"', html)
        self.assertIn("<caption", html)
        self.assertIn("暂无记录，请先手动同步此来源", html)

    def test_chart_is_ascending_and_table_remains_newest_first_with_exact_usd(self):
        self.insert_prices([(123456789, "2026-11-06 10:00:00"),
                            (101, "2026-11-06 00:00:00"), (0, "2026-11-06 01:00:00")])
        html = self.page()
        svg = self.svg(html)
        titles = [item.text for item in svg.findall("svg:circle/svg:title", SVG)]
        self.assertEqual(titles, ["2026-11-06 00:00:00 UTC · $1.01", "2026-11-06 01:00:00 UTC · $0.00",
                                  "2026-11-06 10:00:00 UTC · $1,234,567.89"])
        table = html.split('id="price-history-table"', 1)[1]
        self.assertLess(table.index("chart-101-0"), table.index("chart-101-2"))
        self.assertLess(table.index("chart-101-2"), table.index("chart-101-1"))
        self.assertIn('role="img"', html)
        self.assertIn("UTC", html)
        self.assertIn("USD", html)
        self.assertIn('href="#price-history-table"', html)
        self.assertIn("不代表期间一直以该价出售", html)

    def test_one_observation_has_dot_without_implying_a_continuous_line(self):
        self.insert_prices([(101, "2026-11-06 10:00:00")])
        html = self.page()
        svg = self.svg(html)
        self.assertEqual(len(svg.findall("svg:circle", SVG)), 1)
        self.assertEqual(svg.findall("svg:polyline", SVG), [])
        self.assertIn("仅有一次观测，暂不连线", html)
        self.assertIn("$1.01", html)

    def test_extreme_price_and_zero_render_exactly_without_scientific_notation(self):
        self.insert_prices([(0, "2026-11-06 10:00:00"), (9223372036854775807, "2026-11-07 10:00:00")])
        html = self.page()
        self.assertIn("$92,233,720,368,547,758.07", html)
        self.assertIn("$0.00", html)
        svg = self.svg(html)
        self.assertNotIn("e+", svg.find("svg:polyline", SVG).attrib["points"])
        self.assertEqual(len(svg.findall("svg:circle", SVG)), 2)

    def test_long_page_keeps_full_table_and_single_svg_line(self):
        start = datetime(2026, 1, 1)
        self.insert_prices([((index % 20) * 100, (start + timedelta(minutes=index)).isoformat(sep=" "))
                            for index in range(5000)])
        html = self.page()
        svg = self.svg(html)
        self.assertEqual(len(svg.findall("svg:polyline", SVG)), 1)
        self.assertEqual(len(svg.find("svg:polyline", SVG).attrib["points"].split()), 5000)
        self.assertEqual(len(svg.findall("svg:circle", SVG)), 2)
        self.assertEqual(html.count('<code class="event-id">chart-101-'), 5000)
        self.assertIn("全部 5000 条记录仍在下表", html)

    def test_game_title_is_escaped_in_svg_without_script_or_json_injection(self):
        title = '游戏 </title><script>alert("unsafe")</script> & 演示'
        with get_db() as db:
            db.execute("UPDATE games SET title=? WHERE id=101", (title,))
        self.insert_prices([(100, "2026-11-06 10:00:00")])
        html = self.page()
        svg = self.svg(html)
        self.assertEqual(svg.find("svg:title", SVG).text, title + "的已记录价格走势（USD）")
        self.assertEqual(svg.findall(".//svg:script", SVG), [])
        self.assertNotIn("<script", html)

    def test_chart_gets_do_not_change_business_records_or_mix_sources(self):
        self.insert_prices([(101, "2026-11-06 10:00:00")])
        before = "\n".join(get_db().iterdump())
        with patch("app.sync_prices", side_effect=AssertionError("GET cannot sync")):
            real = self.svg(self.page())
            demo = self.svg(self.page(1))
        self.assertEqual(len(real.findall("svg:circle", SVG)), 1)
        self.assertGreater(len(demo.findall("svg:circle", SVG)), 1)
        self.assertEqual("\n".join(get_db().iterdump()), before)


if __name__ == "__main__":
    unittest.main()
