from dataclasses import dataclass


class PriceSourceError(RuntimeError):
    """A price source failed; existing business data must be preserved."""


@dataclass(frozen=True)
class PriceObservationEvent:
    event_id: str
    game_id: int
    price_cents: int
    observed_at: str
    source_updated_at: int | None = None


@dataclass(frozen=True)
class PriceSyncGame:
    """Database-selected identity passed into a source, with no DB connection."""
    game_id: int
    steam_app_id: str
    title: str
