from dataclasses import dataclass
from datetime import datetime, timezone
import re

from adapters.contracts import PriceSourceError, PriceSyncGame
from adapters.cheapshark import CheapSharkSource


def load_sync_games(db):
    """Select tracked Steam/USD games; the adapter never receives the database."""
    rows = db.execute(
        """
        SELECT id, steam_app_id, title FROM games
        WHERE data_source='cheapshark' AND is_tracked=1
          AND platform='Steam' AND currency='USD'
        ORDER BY id
        """
    ).fetchall()
    return [PriceSyncGame(row["id"], row["steam_app_id"], row["title"]) for row in rows
            if isinstance(row["steam_app_id"], str)
            and re.fullmatch(r"[1-9][0-9]{0,19}", row["steam_app_id"])]


@dataclass(frozen=True)
class SyncResult:
    success: bool
    received: int
    inserted: int
    attempted_at: str
    error: str | None = None
    notifications: int = 0


@dataclass(frozen=True)
class RealSyncReport:
    total_tracked: int
    games: tuple[PriceSyncGame, ...]
    result: SyncResult | None
    missing_games: tuple[PriceSyncGame, ...]


def sync_real_prices(db):
    """Same synchronous operation for the CLI and post-tracking web request."""
    total = db.execute("SELECT COUNT(*) FROM games WHERE data_source='cheapshark' AND is_tracked=1").fetchone()[0]
    games = tuple(load_sync_games(db))
    if not games:
        return RealSyncReport(total, games, None, ())
    source = CheapSharkSource(games)
    result = sync_prices(db, source)
    return RealSyncReport(total, games, result, tuple(source.missing_games))


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

    completed_at = now or _utc_now()
    inserted = 0
    notification_count = 0
    # Acquire the write lock BEFORE reading the previous valid price. The context
    # manager rolls back prices AND notifications if any SQL statement fails.
    with db:
        db.execute("BEGIN IMMEDIATE")
        for event in sorted(events, key=lambda item: (item.observed_at, item.event_id)):
            tracked = db.execute("SELECT is_tracked FROM games WHERE id=?", (event.game_id,)).fetchone()
            if tracked is not None and not tracked["is_tracked"]:
                continue
            existing = db.execute(
                "SELECT game_id, price_cents FROM price_observations WHERE source_event_id=?",
                (event.event_id,),
            ).fetchone()
            if existing is not None and (existing["game_id"] != event.game_id or existing["price_cents"] != event.price_cents):
                raise ValueError("同一事件 ID 的游戏或价格不一致；请为新观测使用新的事件 ID。")
            previous = db.execute(
                """
                SELECT price_cents, observed_at, source_updated_at FROM price_observations
                WHERE game_id = ? ORDER BY observed_at DESC, id DESC LIMIT 1
                """,
                (event.game_id,),
            ).fetchone()
            if (previous is not None and event.source_updated_at is not None
                    and previous["source_updated_at"] is not None
                    and event.source_updated_at < previous["source_updated_at"]):
                continue
            cursor = db.execute(
                """
                INSERT INTO price_observations
                    (source_event_id, game_id, price_cents, observed_at, source_updated_at)
                VALUES (?, ?, ?, ?, ?)
                ON CONFLICT(source_event_id) DO NOTHING
                """,
                (event.event_id, event.game_id, event.price_cents, event.observed_at, event.source_updated_at),
            )
            inserted += cursor.rowcount
            if cursor.rowcount == 0 or previous is None or event.observed_at <= previous["observed_at"]:
                continue

            # Only a new, chronologically later crossing generates notifications.
            cursor = db.execute(
                """
                INSERT INTO notifications
                    (user_id, game_id, source_event_id, trigger_price_cents,
                     target_cents, observed_at, created_at)
                SELECT user_id, game_id, ?, ?, target_cents, ?, ?
                FROM price_targets
                WHERE game_id = ? AND ? > target_cents AND ? <= target_cents
                ON CONFLICT(user_id, game_id, source_event_id) DO NOTHING
                """,
                (event.event_id, event.price_cents, event.observed_at, completed_at,
                 event.game_id, previous["price_cents"], event.price_cents),
            )
            notification_count += cursor.rowcount

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
            (source.name, attempted_at, completed_at),
        )
    return SyncResult(True, len(events), inserted, attempted_at, notifications=notification_count)
