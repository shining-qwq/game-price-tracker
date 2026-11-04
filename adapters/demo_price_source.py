from dataclasses import dataclass


class PriceSourceError(RuntimeError):
    """The external price source could not return observations."""


@dataclass(frozen=True)
class PriceObservationEvent:
    event_id: str
    game_id: int
    price_cents: int
    observed_at: str


class DemoPriceSource:
    """A deterministic stand-in for a future real platform API."""

    name = "demo-steam-cn"

    def fetch(self, scenario="price-drop"):
        if scenario == "failure":
            raise PriceSourceError("模拟数据源暂时不可用")

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
