"""Steam-only USD quotes. No API key is required by CheapShark."""
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
import hashlib
import json
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen
from urllib.parse import urlencode

from adapters.contracts import PriceObservationEvent, PriceSourceError


STEAM_GAMES = {"413150": 101, "367520": 102, "1426210": 103, "1868140": 104}
TIMEOUT_SECONDS = 10


def parse_price(value):
    if value is None or value == "":
        return None
    try:
        amount = Decimal(str(value))
        if not amount.is_finite() or amount < 0 or amount * 100 != (amount * 100).to_integral_value():
            raise InvalidOperation
        cents = int(amount * 100)
        if cents > 9223372036854775807:
            raise InvalidOperation
        return cents
    except (InvalidOperation, ValueError):
        raise PriceSourceError("CheapShark 返回无效价格，未写入本批次数据。") from None


class CheapSharkSource:
    name = "cheapshark-steam-usd"

    def __init__(self):
        self.missing_games = []

    def fetch(self, scenario=None):
        self.missing_games = []
        params = urlencode({"storeID": "1", "steamAppID": ",".join(STEAM_GAMES), "pageSize": "60"})
        request = Request("https://www.cheapshark.com/api/1.0/deals?" + params,
                          headers={"User-Agent": "GamePriceTracker-Learning/0.1", "Accept": "application/json"})
        try:
            with urlopen(request, timeout=TIMEOUT_SECONDS) as response:
                if int(response.headers.get("X-Total-Page-Count", "1")) > 1:
                    raise PriceSourceError("报价响应超出一页，本次不进行不完整同步。")
                rows = json.load(response)
        except HTTPError as exc:
            if exc.code == 429:
                raise PriceSourceError(f"CheapShark 限流，请等待 Retry-After={exc.headers.get('Retry-After', '未提供')} 后手动重试。") from None
            raise PriceSourceError(f"CheapShark HTTP {exc.code}，请稍后手动重试。") from None
        except (URLError, OSError, TimeoutError):
            raise PriceSourceError("CheapShark 网络、代理或超时错误。") from None
        except (ValueError, TypeError):
            raise PriceSourceError("CheapShark 响应格式错误。") from None
        if not isinstance(rows, list):
            raise PriceSourceError("CheapShark 响应不是报价列表。")

        observed_at = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S.%f")
        events = []
        seen = set()
        for row in rows:
            if not isinstance(row, dict) or row.get("storeID") != "1" or row.get("steamAppID") not in STEAM_GAMES:
                raise PriceSourceError("CheapShark 返回了不匹配的游戏或商店。")
            app_id = row["steamAppID"]
            if app_id in seen:
                raise PriceSourceError("同一 Steam 游戏返回了多条报价，本次停止同步。")
            seen.add(app_id)
            price = parse_price(row.get("salePrice"))
            if price is None:
                continue
            changed = row.get("lastChange")
            if type(changed) is not int or changed < 0:
                raise PriceSourceError("CheapShark 缺少有效的源更新时间。")
            # No provider observation ID is supplied. Derive a stable snapshot key;
            # do not pretend the deal ID itself identifies a price change.
            key = f"steam:USD:{app_id}:{changed}:{price}"
            event_id = "cheapshark-" + hashlib.sha256(key.encode()).hexdigest()
            events.append(PriceObservationEvent(event_id, STEAM_GAMES[app_id], price, observed_at, changed))
        priced = {event.game_id for event in events}
        self.missing_games = [app_id for app_id, game_id in STEAM_GAMES.items() if game_id not in priced]
        return events
