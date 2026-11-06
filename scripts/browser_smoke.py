"""Read-only headless browser checks on an isolated local Flask database.

Install requirements-dev.txt and use an existing Edge/Chrome installation.
External HTTP is blocked both in the browser and at the CheapShark boundary.
"""
import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
import secrets
import sys
import tempfile
from threading import Thread
from unittest.mock import patch
from urllib.parse import urlsplit

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--report", type=Path, help="Optional JSON report output")
    parser.add_argument("--screenshots", type=Path, help="Optional screenshot directory")
    parser.add_argument("--browser", type=Path, help="Explicit local Edge/Chrome executable")
    args = parser.parse_args()
    from playwright.sync_api import sync_playwright
    from werkzeug.serving import make_server
    from app import app, get_db
    from scripts.capture_price_chart import find_browser

    executable = str(args.browser) if args.browser else find_browser()
    if not executable:
        parser.error("No local Edge/Chrome; use --browser with its executable path")
    report = {"pages": [], "console": [], "page_errors": [], "failed_requests": [],
              "http_errors": [], "external_requests": [], "favicon_requests": []}
    report["checked_at_utc"] = datetime.now(timezone.utc).isoformat()
    report["python_version"] = sys.version.split()[0]
    with tempfile.TemporaryDirectory(prefix="price-browser-") as temporary:
        app.config.update(TESTING=True, DATABASE=Path(temporary) / "browser.sqlite3", SECRET_KEY=secrets.token_hex(32))
        with app.app_context():
            result = app.test_cli_runner().invoke(args=["upgrade-db"])
            if result.exit_code:
                raise RuntimeError("Isolated database upgrade failed")
            before = "\n".join(get_db().iterdump())
        server = make_server("127.0.0.1", 0, app)
        base = f"http://127.0.0.1:{server.server_port}"
        worker = Thread(target=server.serve_forever, daemon=True)
        worker.start()
        try:
            with patch("adapters.cheapshark.urlopen", side_effect=AssertionError("External API forbidden")), sync_playwright() as playwright:
                browser = playwright.chromium.launch(executable_path=executable, headless=True)
                report["browser_version"] = browser.version
                try:
                    for mode, viewport in [("desktop", {"width": 1440, "height": 920}),
                                           ("mobile", {"width": 390, "height": 844})]:
                        context = browser.new_context(viewport=viewport)

                        def local_only(route):
                            if route.request.url.startswith(base + "/"):
                                route.continue_()
                            else:
                                report["external_requests"].append(route.request.url)
                                route.abort()

                        context.route("**/*", local_only)
                        page = context.new_page()
                        page.on("console", lambda message: report["console"].append({"type": message.type, "text": message.text}))
                        page.on("pageerror", lambda error: report["page_errors"].append(str(error)))
                        page.on("requestfailed", lambda request: report["failed_requests"].append({"path": urlsplit(request.url).path, "failure": request.failure}))
                        page.on("response", lambda response: report["http_errors"].append({"path": urlsplit(response.url).path, "status": response.status}) if response.status >= 400 else None)
                        # CDP also observes browser-initiated default favicon requests.
                        cdp = context.new_cdp_session(page)
                        cdp.send("Network.enable")
                        cdp.on("Network.responseReceived", lambda event: report["favicon_requests"].append({
                            "path": urlsplit(event["response"]["url"]).path, "status": event["response"]["status"]})
                            if "favicon" in urlsplit(event["response"]["url"]).path else None)
                        cases = [("detail", "/games/1"), ("demo-management", "/games?source=demo"),
                                 ("search-empty", "/games/search"), ("notifications-login", "/notifications"),
                                 ("admin-login", "/admin/sync"), ("register", "/register")]
                        for name, path in cases:
                            response = page.goto(base + path, wait_until="networkidle")
                            assert response.status == 200, (path, response.status)
                            if name == "detail":
                                assert page.locator("#price-history-chart svg").count() == 1
                                if mode == "desktop" and args.screenshots:
                                    args.screenshots.mkdir(parents=True, exist_ok=True)
                                    page.screenshot(path=str(args.screenshots / "game-detail.png"))
                            elif name == "demo-management":
                                assert page.locator("tr[data-game-id]").count() == 4
                            elif name.endswith("login"):
                                assert urlsplit(page.url).path == "/login"
                                assert page.locator("input[name=password]").count() == 1
                            elif name == "search-empty":
                                assert page.locator("input[name=q]").input_value() == ""
                            layout = page.evaluate("({viewport: innerWidth, content: document.documentElement.scrollWidth})")
                            assert layout["content"] <= layout["viewport"], (mode, path, layout)
                            report["pages"].append({"mode": mode, "path": path, "final_path": urlsplit(page.url).path,
                                                    "status": response.status, **layout})
                        # Explicitly verify old bookmark/browser fallback requests too.
                        # Some headless browser versions suppress implicit icon
                        # fetching. Explicitly probe that default path as well.
                        fallback_response = context.request.get(base + "/favicon.ico")
                        fallback = fallback_response.status
                        report["favicon_requests"].append({"path": "/favicon.ico", "status": fallback,
                                                           "kind": "explicit-fallback-probe"})
                        if fallback >= 400:
                            report["http_errors"].append({"path": "/favicon.ico", "status": fallback})
                        report["fallback_favicon_status"] = fallback
                        context.close()
                finally:
                    browser.close()
        finally:
            server.shutdown()
            worker.join(timeout=5)
            server.server_close()
        with app.app_context():
            report["database_unchanged"] = "\n".join(get_db().iterdump()) == before
    report["success"] = (not any(report[key] for key in ["page_errors", "failed_requests", "http_errors", "external_requests"])
                         and not any(item["type"] == "error" for item in report["console"])
                         and report["database_unchanged"] and report["fallback_favicon_status"] == 200)
    if args.report:
        args.report.parent.mkdir(parents=True, exist_ok=True)
        args.report.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if report["success"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
