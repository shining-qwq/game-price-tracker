from datetime import datetime, timezone
import re
from uuid import uuid4

from adapters.contracts import PriceObservationEvent, PriceSourceError


INT_MAX = 2147483647
DEMO_GAME_IDS = (1, 2, 3, 4)
LEGACY_SCENARIOS = frozenset({"price-drop", "late", "failure", "sequence"})


def validate_scenario(scenario):
    if scenario in LEGACY_SCENARIOS:
        return
    match = re.fullmatch(r"price-([0-9]{1,10})", scenario)
    if match is None or int(match[1]) > INT_MAX:
        raise ValueError("场景须为 price-整数（美元，0～2147483647），或 price-drop/late/failure/sequence")


class DemoPriceSource:
    """A deterministic stand-in for a future real platform API."""

    name = "demo-steam-usd"

    def __init__(self, event_id=None, observed_at=None, *, game_id=1):
        if type(game_id) is not int or game_id not in DEMO_GAME_IDS:
            raise ValueError("请选择现有四款模拟游戏之一。")
        self.game_id = game_id
        self.event_id = event_id
        self.observed_at = observed_at

    def fetch(self, scenario="price-drop"):
        validate_scenario(scenario)
        if scenario in LEGACY_SCENARIOS and self.game_id != 1:
            raise ValueError("固定兼容场景只支持模拟游戏 1，请为其他游戏使用 price-整数场景。")
        if scenario == "failure":
            raise PriceSourceError("模拟数据源暂时不可用")

        if re.fullmatch(r"price-[0-9]+", scenario):
            price = int(scenario.removeprefix("price-")) * 100
            timestamp = self.observed_at or datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S.%f")
            return [PriceObservationEvent(
                "demo-manual-" + (self.event_id or uuid4().hex), self.game_id, price, timestamp,
            )]

        # These event IDs and times never change, including during a replay.
        steps = [
            ("price-100", 10000, "2026-11-05 10:00:00"),
            ("price-88", 8800, "2026-11-05 11:00:00"),
            ("price-120", 12000, "2026-11-05 12:00:00"),
            ("price-90", 9000, "2026-11-05 13:00:00"),
        ]
        if scenario == "sequence":
            return [
                PriceObservationEvent(f"demo-sequence-{name}", 1, price, time)
                for name, price, time in steps
            ]

        if scenario == "late":
            return [
                PriceObservationEvent(
                    event_id="demo-stardew-late-20261015-8800",
                    game_id=1,
                    price_cents=8800,
                    observed_at="2026-10-15 09:30:00",
                )
            ]

        return [
            PriceObservationEvent(
                event_id="demo-stardew-drop-20261104-8800",
                game_id=1,
                price_cents=8800,
                observed_at="2026-11-04 09:30:00",
            )
        ]
