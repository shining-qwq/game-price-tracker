from dataclasses import dataclass
from datetime import datetime, timezone
import re
import sqlite3

from adapters.contracts import PriceSourceError, PriceSyncGame
from adapters.cheapshark import CheapSharkSource


def load_sync_games(db):
    """Select tracked Steam/USD games; the adapter never receives the database."""
    rows = db.execute(
        """
        SELECT id, steam_app_id, title FROM games g
        WHERE data_source='cheapshark'
          AND EXISTS (SELECT 1 FROM user_game_tracking ugt WHERE ugt.game_id=g.id)
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
    notification_failures: int = 0
    pending_notifications: int = 0


@dataclass(frozen=True)
class RealSyncReport:
    tracking_relationships: int
    tracked_games: int
    games: tuple[PriceSyncGame, ...]
    result: SyncResult | None
    missing_games: tuple[PriceSyncGame, ...]


def sync_real_prices(db):
    """Same synchronous operation for the CLI and post-tracking web request."""
    total = db.execute("""SELECT COUNT(*) FROM user_game_tracking ugt
                          JOIN games g ON g.id=ugt.game_id WHERE g.data_source='cheapshark'""").fetchone()[0]
    distinct = db.execute("""SELECT COUNT(*) FROM games g WHERE g.data_source='cheapshark'
                             AND EXISTS (SELECT 1 FROM user_game_tracking ugt WHERE ugt.game_id=g.id)""").fetchone()[0]
    games = tuple(load_sync_games(db))
    if not games:
        return RealSyncReport(total, distinct, games, None, ())
    source = CheapSharkSource(games)
    result = sync_prices(db, source)
    return RealSyncReport(total, distinct, games, result, tuple(source.missing_games))


def _utc_now():
    return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")


def _deliver_notification(db, values, attempted_at):
    """Isolate one recipient's constraint failure and retain its exact snapshot."""
    db.execute("SAVEPOINT notification_delivery")
    try:
        cursor = db.execute(
            """INSERT INTO notifications
               (user_id, game_id, source_event_id, trigger_price_cents,
                target_cents, observed_at, created_at)
               VALUES (?, ?, ?, ?, ?, ?, ?)
               ON CONFLICT(user_id, game_id, source_event_id) DO NOTHING""",
            values,
        )
    except sqlite3.IntegrityError:
        # A transaction-wide ROLLBACK or database failure cannot be isolated.
        if not db.in_transaction:
            raise
        db.execute("ROLLBACK TO SAVEPOINT notification_delivery")
        db.execute("RELEASE SAVEPOINT notification_delivery")
        db.execute(
            """INSERT INTO notification_retries
               (user_id, game_id, source_event_id, trigger_price_cents,
                target_cents, observed_at, created_at, last_attempt_at, attempts, error_code)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, 1, 'integrity_error')
               ON CONFLICT(user_id, game_id, source_event_id) DO UPDATE SET
                   last_attempt_at=excluded.last_attempt_at,
                   attempts=notification_retries.attempts + 1,
                   error_code=excluded.error_code""",
            (*values, attempted_at),
        )
        return 0, 1
    else:
        db.execute("RELEASE SAVEPOINT notification_delivery")
        db.execute(
            "DELETE FROM notification_retries WHERE user_id=? AND game_id=? AND source_event_id=?",
            values[:3],
        )
        return cursor.rowcount, 0


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
    notification_failures = 0
    # Acquire the write lock BEFORE reading the previous valid price. The context
    # manager rolls back the whole round on infrastructure/storage failure.
    # Recipient constraint failures use savepoints and durable retry snapshots.
    with db:
        db.execute("BEGIN IMMEDIATE")
        # Existing failures are retried once per successful sync, even when all
        # fetched price events are duplicates. Paused games retain pending rows.
        pending = db.execute(
            """SELECT nr.user_id, nr.game_id, nr.source_event_id, nr.trigger_price_cents,
                      nr.target_cents, nr.observed_at, nr.created_at
               FROM notification_retries nr
               WHERE EXISTS (SELECT 1 FROM user_game_tracking ugt
                             WHERE ugt.user_id=nr.user_id AND ugt.game_id=nr.game_id)
               ORDER BY nr.observed_at, nr.user_id, nr.game_id, nr.source_event_id"""
        ).fetchall()
        for row in pending:
            delivered, failed = _deliver_notification(db, tuple(row), completed_at)
            notification_count += delivered
            notification_failures += failed
        for event in sorted(events, key=lambda item: (item.observed_at, item.event_id)):
            # The union was selected before network I/O. Recheck after acquiring
            # the write lock: a last cancellation committed during fetch wins.
            tracked = db.execute("""SELECT data_source,
                EXISTS (SELECT 1 FROM user_game_tracking ugt WHERE ugt.game_id=g.id) AS has_followers
                FROM games g WHERE id=?""", (event.game_id,)).fetchone()
            if tracked is not None and tracked["data_source"] == "cheapshark" and not tracked["has_followers"]:
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
            subscriptions = db.execute(
                """SELECT pt.user_id, pt.target_cents FROM price_targets pt
                   JOIN user_game_tracking ugt ON ugt.user_id=pt.user_id AND ugt.game_id=pt.game_id
                   WHERE pt.game_id=? AND ? > pt.target_cents AND ? <= pt.target_cents
                   ORDER BY pt.user_id""",
                (event.game_id, previous["price_cents"], event.price_cents),
            ).fetchall()
            for subscription in subscriptions:
                delivered, failed = _deliver_notification(db, (
                    subscription["user_id"], event.game_id, event.event_id, event.price_cents,
                    subscription["target_cents"], event.observed_at, completed_at,
                ), completed_at)
                notification_count += delivered
                notification_failures += failed

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
        pending_count = db.execute("SELECT COUNT(*) FROM notification_retries").fetchone()[0]
    return SyncResult(True, len(events), inserted, attempted_at, notifications=notification_count,
                      notification_failures=notification_failures, pending_notifications=pending_count)
