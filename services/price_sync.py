from dataclasses import dataclass
from datetime import datetime, timezone

from adapters.demo_price_source import PriceSourceError


@dataclass(frozen=True)
class SyncResult:
    success: bool
    received: int
    inserted: int
    attempted_at: str
    error: str | None = None


def _utc_now():
    return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")


def sync_prices(db, source, scenario="price-drop", now=None):
    """Fetch from a source, then idempotently persist its observation events."""
    attempted_at = now or _utc_now()

    try:
        events = source.fetch(scenario=scenario)
    except PriceSourceError as exc:
        # Deliberately do not touch observations or last_success_at on failure.
        db.execute(
            """
            INSERT INTO sync_status
                (source_name, last_attempt_at, last_success_at, last_error)
            VALUES (?, ?, NULL, ?)
            ON CONFLICT(source_name) DO UPDATE SET
                last_attempt_at = excluded.last_attempt_at,
                last_error = excluded.last_error
            """,
            (source.name, attempted_at, str(exc)),
        )
        db.commit()
        return SyncResult(False, 0, 0, attempted_at, str(exc))

    inserted = 0
    for event in events:
        cursor = db.execute(
            """
            INSERT OR IGNORE INTO price_observations
                (source_event_id, game_id, price_cents, observed_at)
            VALUES (?, ?, ?, ?)
            """,
            (event.event_id, event.game_id, event.price_cents, event.observed_at),
        )
        inserted += cursor.rowcount

    db.execute(
        """
        INSERT INTO sync_status
            (source_name, last_attempt_at, last_success_at, last_error)
        VALUES (?, ?, ?, NULL)
        ON CONFLICT(source_name) DO UPDATE SET
            last_attempt_at = excluded.last_attempt_at,
            last_success_at = excluded.last_success_at,
            last_error = NULL
        """,
        (source.name, attempted_at, attempted_at),
    )
    db.commit()
    return SyncResult(True, len(events), inserted, attempted_at)
