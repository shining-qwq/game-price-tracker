"""Small, read-only API probe. Never writes external prices to the application DB."""
import json
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
import socket
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen


TIMEOUT_SECONDS = 10
GAMES = [("Stardew Valley", "413150"), ("Dota 2", "570")]


def classify_price(value):
    """Zero is a reported zero price; absent or invalid is never treated as free."""
    if value is None or value == "":
        return {"price_state": "missing", "price_cents": None}
    try:
        amount = Decimal(str(value))
        if not amount.is_finite() or amount < 0 or amount * 100 != (amount * 100).to_integral_value():
            raise InvalidOperation
    except (InvalidOperation, ValueError):
        return {"price_state": "invalid", "price_cents": None}
    return {
        "price_state": "zero_price" if amount == 0 else "paid",
        "price_cents": int(amount * 100),
    }


def probe_game(title, steam_app_id):
    params = urlencode({"storeID": "1", "steamAppID": steam_app_id, "pageSize": "1"})
    url = "https://www.cheapshark.com/api/1.0/deals?" + params
    result = {"requested_title": title, "requested_steam_app_id": steam_app_id, "url": url}
    request = Request(url, headers={
        "User-Agent": "GamePriceTracker-LearningProbe/0.1",
        "Accept": "application/json",
    })
    try:
        with urlopen(request, timeout=TIMEOUT_SECONDS) as response:
            result["http_status"] = response.status
            rows = json.load(response)
        if not isinstance(rows, list):
            result["error"] = "Unexpected response shape"
        elif not rows:
            result.update(price_state="missing", price_cents=None, matching_deals=0)
        else:
            row = rows[0]
            if row.get("steamAppID") != steam_app_id or row.get("storeID") != "1":
                result["error"] = "Returned game or store does not match request"
                return result
            result["returned_fields"] = {
                key: row[key] for key in ["title", "gameID", "steamAppID", "storeID",
                                         "dealID", "salePrice", "normalPrice", "lastChange"]
                if key in row
            }
            result.update(classify_price(row.get("salePrice")))
            # These are deliberately separate: USD is documented, not inferred
            # from a dollar sign; region remains unknown even when USD is used.
            result["currency_from_documentation"] = "USD"
            result["currency_in_response"] = row.get("currency")
            result["region_in_response"] = row.get("country") or row.get("region")
    except HTTPError as exc:
        result.update(http_status=exc.code, error="HTTP request rejected",
                      retry_after=exc.headers.get("Retry-After"))
    except (URLError, TimeoutError, socket.timeout, OSError):
        result["error"] = "Network, TLS, proxy or timeout failure; price unverified"
    except (ValueError, TypeError):
        result["error"] = "Invalid JSON payload; price unverified"
    return result


def main():
    results = []
    for title, app_id in GAMES:
        result = probe_game(title, app_id)
        results.append(result)
        # Do not continue requests or retry automatically after a rate limit.
        if result.get("http_status") == 429:
            break
    print(json.dumps({
        "checked_at_utc": datetime.now(timezone.utc).isoformat(),
        "provider": "CheapShark", "timeout_seconds": TIMEOUT_SECONDS,
        "results": results,
        "business_database_written": False,
    }, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
