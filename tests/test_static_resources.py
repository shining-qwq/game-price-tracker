"""Local icon regression; no account/price changes or external resources."""
from html.parser import HTMLParser
import unittest
from unittest.mock import patch
from xml.etree import ElementTree

from app import app, get_db


class HeadLinks(HTMLParser):
    def __init__(self, html):
        super().__init__()
        self.icons = []
        self.styles = []
        self.feed(html)

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if tag == "link" and attrs.get("rel") == "icon":
            self.icons.append(attrs["href"])
        if tag == "link" and attrs.get("rel") == "stylesheet":
            self.styles.append(attrs["href"])


class StaticResourcesTest(unittest.TestCase):
    def setUp(self):
        self.previous_config = dict(app.config)
        app.config.update(TESTING=True, DATABASE=":memory:")
        self.ctx = app.app_context()
        self.ctx.push()
        self.assertEqual(app.test_cli_runner().invoke(args=["init-db"]).exit_code, 0)
        self.client = app.test_client()

    def tearDown(self):
        self.ctx.pop()
        app.config.update(self.previous_config)

    def test_shared_base_references_local_icon_and_styles_on_public_and_login_pages(self):
        before = "\n".join(get_db().iterdump())
        with patch("adapters.cheapshark.urlopen", side_effect=AssertionError("Static checks must be offline")):
            for path in ["/games/1", "/games?source=demo", "/games/search", "/login", "/register", "/notifications", "/admin/sync"]:
                with self.subTest(path=path):
                    response = self.client.get(path, follow_redirects=True)
                    self.assertEqual(response.status_code, 200)
                    links = HeadLinks(response.get_data(as_text=True))
                    self.assertEqual(links.icons, ["/static/favicon.svg"])
                    for asset in links.icons + links.styles:
                        self.assertTrue(asset.startswith("/static/"))
                        with self.client.get(asset) as asset_response:
                            self.assertEqual(asset_response.status_code, 200)
        self.assertEqual("\n".join(get_db().iterdump()), before)

    def test_default_favicon_path_serves_the_svg_without_querying_business_db(self):
        with patch("app.get_db", side_effect=AssertionError("Icon must not query business data")):
            with self.client.get("/favicon.ico") as fallback, self.client.get("/static/favicon.svg") as explicit:
                self.assertEqual(fallback.status_code, 200)
                self.assertEqual(fallback.mimetype, "image/svg+xml")
                self.assertEqual(fallback.data, explicit.data)
                self.assertEqual(ElementTree.fromstring(fallback.data).tag, "{http://www.w3.org/2000/svg}svg")


if __name__ == "__main__":
    unittest.main()
