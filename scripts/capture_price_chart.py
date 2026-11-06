"""Capture the actual history template with isolated fixtures and local Edge/Chrome.

No server, API, account or existing database is used. Browser profiles and HTML
files live in a newly created temporary directory, then are removed.
"""
import argparse
from pathlib import Path
import shutil
import secrets
import subprocess
import sys
import tempfile
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def find_browser():
    candidates = [
        shutil.which("msedge"), shutil.which("google-chrome"), shutil.which("chromium"),
        r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
        r"C:\Program Files\Google\Chrome\Application\chrome.exe",
    ]
    return next((path for path in candidates if path and Path(path).is_file()), None)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--browser", help="Local Edge/Chrome executable; detected by default")
    parser.add_argument("--output", type=Path, default=ROOT / "docs" / "screenshots")
    args = parser.parse_args()
    browser = args.browser or find_browser()
    if not browser or not Path(browser).is_file():
        parser.error("No local Edge/Chrome found; pass --browser with its executable path.")

    from app import app, get_db

    output = args.output.resolve()
    output.mkdir(parents=True, exist_ok=True)
    css = (ROOT / "static" / "style.css").read_text(encoding="utf-8")
    # Crop to the real history section using CSS; no chart reconstruction.
    crop_css = "header.site-header, main > :not(#price-history) { display: none; } main { padding-top: 24px; }"
    with tempfile.TemporaryDirectory(prefix="price-chart-preview-", dir=app.instance_path) as temporary:
        work = Path(temporary)
        app.config.update(TESTING=True, DATABASE=work / "preview.sqlite3", SECRET_KEY=secrets.token_hex(32))
        with app.app_context(), patch("adapters.cheapshark.urlopen", side_effect=AssertionError("Offline screenshots")):
            result = app.test_cli_runner().invoke(args=["init-db"])
            if result.exit_code:
                raise RuntimeError("Could not initialize the isolated screenshot fixture")
            client = app.test_client()
            for name, game_id, height in [("demo", 1, 1060), ("empty", 101, 480), ("single", 102, 820)]:
                if name == "single":
                    with get_db() as db:
                        db.execute("""INSERT INTO price_observations(source_event_id,game_id,price_cents,observed_at)
                                      VALUES ('screenshot-single',102,101,'2026-11-06 10:00:00')""")
                response = client.get(f"/games/{game_id}")
                if response.status_code != 200:
                    raise RuntimeError("Screenshot detail failed to render")
                html = response.get_data(as_text=True).replace(
                    '<link rel="stylesheet" href="/static/style.css">',
                    f"<style>{css}\n{crop_css}</style>")
                html = html.replace('href="/static/favicon.svg"', f'href="{(ROOT / "static/favicon.svg").as_uri()}"')
                html = html.replace("<body>", '<body><p style="position:absolute;top:4px;right:32px;margin:0;font-size:12px">离线演示截图 · 非实时报价</p>', 1)
                page = work / f"{name}.html"
                page.write_text(html, encoding="utf-8")
                screenshot = output / f"price-history-{name}.png"
                subprocess.run([
                    str(browser), "--headless=new", "--disable-gpu", "--no-first-run",
                    "--no-default-browser-check", "--disable-extensions", "--disable-background-networking",
                    "--disable-component-update", "--disable-sync", "--hide-scrollbars",
                    "--force-device-scale-factor=1", f"--user-data-dir={work / ('profile-' + name)}",
                    f"--window-size=1440,{height}", "--virtual-time-budget=1000",
                    f"--screenshot={screenshot}", page.as_uri(),
                ], check=True, timeout=40, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                if not screenshot.is_file():
                    raise RuntimeError("Browser did not create the screenshot")
                print(f"Created {screenshot.name}", flush=True)


if __name__ == "__main__":
    main()
