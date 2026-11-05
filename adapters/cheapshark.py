"""CheapShark search plus Steam-only USD quotes. No API key is required."""
from dataclasses import dataclass
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
SEARCH_LIMIT = 20
MAX_SEARCH_LENGTH = 80
API_BASE_URL = "https://www.cheapshark.com/api/1.0"
USER_AGENT = "GamePriceTracker-Learning/0.1"


@dataclass(frozen=True)
class GameSearchResult:
    """Validated fields returned by CheapShark's title search."""

    game_id: str
    title: str
    steam_app_id: str | None
    cheapest_cents: int | None


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

    def search_by_title(self, title):
        """Search CheapShark's game catalog without changing local state."""
        query = title.strip()
        if not query:
            return []
        if len(query) > MAX_SEARCH_LENGTH:
            raise ValueError(f"搜索关键词不能超过 {MAX_SEARCH_LENGTH} 个字符。")

        params = urlencode({"title": query, "limit": SEARCH_LIMIT})
        request = Request(
            f"{API_BASE_URL}/games?{params}",
            headers={"User-Agent": USER_AGENT, "Accept": "application/json"},
        )
        try:
            with urlopen(request, timeout=TIMEOUT_SECONDS) as response:
                rows = json.load(response)
        except HTTPError as exc:
            if exc.code == 429:
                retry_after = exc.headers.get("Retry-After", "未提供") if exc.headers else "未提供"
                raise PriceSourceError(
                    f"CheapShark 搜索请求过于频繁，请等待 Retry-After={retry_after} 后重试。"
                ) from None
            raise PriceSourceError(f"CheapShark 搜索返回 HTTP {exc.code}，请稍后重试。") from None
        except (URLError, OSError, TimeoutError):
            raise PriceSourceError("CheapShark 搜索网络、代理或超时错误，请稍后重试。") from None
        except (ValueError, TypeError):
            raise PriceSourceError("CheapShark 搜索响应格式错误。") from None

        if not isinstance(rows, list):
            raise PriceSourceError("CheapShark 搜索响应不是游戏列表。")

        results = []
        for row in rows:
            if not isinstance(row, dict):
                raise PriceSourceError("CheapShark 搜索结果格式错误。")
            game_id = row.get("gameID")
            result_title = row.get("external")
            steam_app_id = row.get("steamAppID")
            if not isinstance(game_id, str) or not game_id.isdigit():
                raise PriceSourceError("CheapShark 搜索结果缺少有效 gameID。")
            if not isinstance(result_title, str) or not result_title.strip():
                raise PriceSourceError("CheapShark 搜索结果缺少有效标题。")
            if steam_app_id in (None, ""):
                steam_app_id = None
            elif not isinstance(steam_app_id, str) or not steam_app_id.isdigit():
                raise PriceSourceError("CheapShark 搜索结果包含无效 Steam AppID。")
            results.append(
                GameSearchResult(
                    game_id=game_id,
                    title=result_title.strip(),
                    steam_app_id=steam_app_id,
                    cheapest_cents=parse_price(row.get("cheapest")),
                )
            )
        return results

    def fetch(self, scenario=None):
        self.missing_games = []
        params = urlencode({"storeID": "1", "steamAppID": ",".join(STEAM_GAMES), "pageSize": "60"})
        request = Request(
            f"{API_BASE_URL}/deals?{params}",
            headers={"User-Agent": USER_AGENT, "Accept": "application/json"},
        )
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
