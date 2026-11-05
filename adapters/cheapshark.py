"""CheapShark search plus Steam-only USD quotes. No API key is required."""
from dataclasses import dataclass, replace
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
import hashlib
import json
import re
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen
from urllib.parse import urlencode

from adapters.contracts import PriceObservationEvent, PriceSourceError, PriceSyncGame


TIMEOUT_SECONDS = 10
SYNC_BATCH_SIZE = 60
DEALS_PAGE_SIZE = 60
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
    steam_price_cents: int | None = None
    steam_price_error: str | None = None


@dataclass(frozen=True)
class SteamQuote:
    steam_app_id: str
    price_cents: int
    source_updated_at: int
    observed_at: str


@dataclass(frozen=True)
class VerifiedSteamGame:
    cheapshark_game_id: str
    steam_app_id: str
    title: str
    steam_price_cents: int


def validate_external_id(value):
    """Canonical ASCII decimal IDs avoid aliases such as 001 vs 1."""
    if not isinstance(value, str) or not re.fullmatch(r"[1-9][0-9]{0,19}", value):
        raise ValueError("游戏标识必须是 1～20 位、无前导零的正整数。")
    return value


def parse_price(value):
    if value is None or value == "":
        return None
    try:
        amount = Decimal(str(value))
        if not amount.is_finite() or amount < 0 or amount > Decimal("92233720368547758.07"):
            raise InvalidOperation
        # Check fractional digits before arithmetic: a tiny exponent must not
        # underflow to zero and accidentally become a "free" price.
        _, digits, exponent = amount.as_tuple()
        if exponent < -2 and any(digits[exponent + 2:]):
            raise InvalidOperation
        cents = int(amount * 100)
        if cents > 9223372036854775807:
            raise InvalidOperation
        return cents
    except (InvalidOperation, ValueError):
        raise PriceSourceError("CheapShark 返回无效价格，未写入本批次数据。") from None


class CheapSharkSource:
    name = "cheapshark-steam-usd"

    def __init__(self, games=()):
        self.missing_games = []
        self.games = tuple(games)

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

        if not isinstance(rows, list) or len(rows) > SEARCH_LIMIT:
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
        app_ids = list(dict.fromkeys(item.steam_app_id for item in results if item.steam_app_id))
        if not app_ids:
            return results
        try:
            quotes = {quote.steam_app_id: quote.price_cents for quote in self.get_steam_quotes(app_ids)}
        except PriceSourceError as exc:
            # Directory search remains useful even if the Steam quote request fails.
            return [replace(item, steam_price_error=str(exc)) if item.steam_app_id else item for item in results]
        return [replace(item, steam_price_cents=quotes.get(item.steam_app_id)) for item in results]

    def get_verified_steam_game(self, game_id):
        """Recheck a specific catalog game and its Steam offer on the server."""
        game_id = validate_external_id(game_id)
        request = Request(
            f"{API_BASE_URL}/games?" + urlencode({"id": game_id}),
            headers={"User-Agent": USER_AGENT, "Accept": "application/json"},
        )
        try:
            with urlopen(request, timeout=TIMEOUT_SECONDS) as response:
                payload = json.load(response)
        except HTTPError as exc:
            if exc.code == 429:
                retry_after = exc.headers.get("Retry-After", "未提供") if exc.headers else "未提供"
                raise PriceSourceError(f"CheapShark 限流，请等待 Retry-After={retry_after} 后重试加入。") from None
            raise PriceSourceError(f"CheapShark 查询返回 HTTP {exc.code}，未加入追踪。") from None
        except (URLError, OSError, TimeoutError):
            raise PriceSourceError("CheapShark 网络、代理或超时错误，未加入追踪。") from None
        except (ValueError, TypeError):
            raise PriceSourceError("CheapShark 游戏查询响应格式错误，未加入追踪。") from None

        if not isinstance(payload, dict) or not isinstance(payload.get("info"), dict):
            raise PriceSourceError("游戏不存在或 CheapShark 响应格式错误，未加入追踪。")
        info = payload["info"]
        title = info.get("title")
        if not isinstance(title, str) or not title.strip():
            raise PriceSourceError("CheapShark 未提供有效游戏标题，未加入追踪。")
        steam_id = info.get("steamAppID")
        if steam_id in (None, ""):
            raise PriceSourceError("该游戏未提供 Steam AppID，不能加入 Steam 追踪。")
        try:
            steam_id = validate_external_id(steam_id)
        except ValueError:
            raise PriceSourceError("CheapShark 返回无效 Steam AppID，未加入追踪。") from None
        deals = payload.get("deals")
        if not isinstance(deals, list) or any(not isinstance(deal, dict) for deal in deals):
            raise PriceSourceError("CheapShark 报价列表格式错误，未加入追踪。")
        steam_deals = [deal for deal in deals if deal.get("storeID") == "1"]
        if not steam_deals:
            raise PriceSourceError("该游戏当前没有 Steam 商店报价，不能加入追踪。")
        if len(steam_deals) != 1:
            raise PriceSourceError("Steam 报价不唯一，未加入追踪。")
        # Game lookup uses price; search's cheapest and cheapestPriceEver are ignored.
        cents = parse_price(steam_deals[0].get("price"))
        if cents is None:
            raise PriceSourceError("Steam 报价缺失，不能加入追踪；缺失不代表免费。")
        return VerifiedSteamGame(game_id, steam_id, title.strip(), cents)

    def _fetch_quote_batch(self, app_ids):
        expected_ids = set(app_ids)
        params = urlencode({"storeID": "1", "steamAppID": ",".join(app_ids), "pageSize": str(DEALS_PAGE_SIZE)})
        request = Request(
            f"{API_BASE_URL}/deals?{params}",
            headers={"User-Agent": USER_AGENT, "Accept": "application/json"},
        )
        try:
            with urlopen(request, timeout=TIMEOUT_SECONDS) as response:
                page_count = int(response.headers.get("X-Total-Page-Count", "1"))
                if page_count < 0 or page_count > 1:
                    raise PriceSourceError("报价分页超出本批预期，整轮不进行不完整同步。")
                rows = json.load(response)
        except HTTPError as exc:
            if exc.code == 429:
                retry_after = exc.headers.get('Retry-After', '未提供') if exc.headers else '未提供'
                raise PriceSourceError(f"CheapShark 限流，请等待 Retry-After={retry_after} 后手动重试。") from None
            raise PriceSourceError(f"CheapShark HTTP {exc.code}，请稍后手动重试。") from None
        except (URLError, OSError, TimeoutError):
            raise PriceSourceError("CheapShark 网络、代理或超时错误。") from None
        except (ValueError, TypeError):
            raise PriceSourceError("CheapShark 响应格式错误。") from None
        if not isinstance(rows, list) or len(rows) > DEALS_PAGE_SIZE:
            raise PriceSourceError("CheapShark 响应不是报价列表。")

        observed_at = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S.%f")
        quotes = []
        seen = set()
        for row in rows:
            if (not isinstance(row, dict) or row.get("storeID") != "1"
                    or not isinstance(row.get("steamAppID"), str)
                    or row["steamAppID"] not in expected_ids):
                raise PriceSourceError("CheapShark 返回了不匹配的游戏或商店。")
            app_id = row["steamAppID"]
            if app_id in seen:
                raise PriceSourceError("同一 Steam 游戏返回了多条报价，本次停止同步。")
            seen.add(app_id)
            price = parse_price(row.get("salePrice"))
            if price is None:
                continue
            changed = row.get("lastChange")
            if type(changed) is not int or not 0 <= changed <= 9223372036854775807:
                raise PriceSourceError("CheapShark 缺少有效的源更新时间。")
            quotes.append(SteamQuote(app_id, price, changed, observed_at))
        return quotes

    def get_steam_quotes(self, app_ids):
        """Shared Steam-only, batched validation for search display and sync."""
        app_ids = tuple(app_ids)
        for app_id in app_ids:
            try:
                validate_external_id(app_id)
            except ValueError:
                raise PriceSourceError("报价目标的 Steam AppID 无效。") from None
        app_ids = tuple(dict.fromkeys(app_ids))
        quotes = []
        for start in range(0, len(app_ids), SYNC_BATCH_SIZE):
            try:
                quotes.extend(self._fetch_quote_batch(app_ids[start:start + SYNC_BATCH_SIZE]))
            except PriceSourceError as exc:
                raise PriceSourceError(f"第 {start // SYNC_BATCH_SIZE + 1} 批同步失败：{exc}") from None
        return quotes

    def fetch(self, scenario=None):
        """Materialize ALL validated batches before the caller begins its write transaction."""
        self.missing_games = []
        seen_ids, seen_apps = set(), set()
        for game in self.games:
            if not isinstance(game, PriceSyncGame) or type(game.game_id) is not int or game.game_id <= 0:
                raise PriceSourceError("同步目标的内部游戏标识无效。")
            try:
                validate_external_id(game.steam_app_id)
            except ValueError:
                raise PriceSourceError("同步目标的 Steam AppID 无效。") from None
            if game.game_id in seen_ids or game.steam_app_id in seen_apps:
                raise PriceSourceError("同步目标包含重复的游戏或 Steam AppID。")
            seen_ids.add(game.game_id)
            seen_apps.add(game.steam_app_id)
        by_app_id = {game.steam_app_id: game for game in self.games}
        events = []
        for quote in self.get_steam_quotes(tuple(by_app_id)):
            # Same stable snapshot format as before, independent of display/search.
            key = f"steam:USD:{quote.steam_app_id}:{quote.source_updated_at}:{quote.price_cents}"
            event_id = "cheapshark-" + hashlib.sha256(key.encode()).hexdigest()
            events.append(PriceObservationEvent(event_id, by_app_id[quote.steam_app_id].game_id,
                                                quote.price_cents, quote.observed_at, quote.source_updated_at))
        priced = {event.game_id for event in events}
        self.missing_games = [game for game in self.games if game.game_id not in priced]
        return events
