"""Read the stored observations and notifications without modifying SQLite."""
import argparse
from pathlib import Path
import sqlite3


parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument("--database", type=Path,
                    default=Path(__file__).resolve().parents[1] / "instance/price_tracker_usd.sqlite3")
parser.add_argument("--game-id", type=int, default=1, help="Demo IDs 1-4; real IDs 101-104")
args = parser.parse_args()
db = sqlite3.connect(args.database.resolve().as_uri() + "?mode=ro", uri=True)
db.row_factory = sqlite3.Row
try:
    print("GAME", args.game_id, "SUBSCRIPTION (integer USD cents)")
    for row in db.execute("SELECT user_id, game_id, target_cents FROM price_targets WHERE user_id=1 AND game_id=?", (args.game_id,)):
        print(dict(row))
    print("HISTORY (oldest first; integer USD cents)")
    for row in db.execute("""
        SELECT source_event_id, price_cents, observed_at, source_updated_at FROM price_observations
        WHERE game_id=? ORDER BY observed_at, id
    """, (args.game_id,)):
        print(dict(row))
    print("NOTIFICATIONS (fixed demo user; integer USD cents)")
    for row in db.execute("""
        SELECT n.id, g.title, g.data_source, n.source_event_id, n.trigger_price_cents,
               n.target_cents, n.observed_at, n.created_at
        FROM notifications n JOIN games g ON g.id=n.game_id
        WHERE n.user_id=1 ORDER BY n.observed_at, n.id
    """):
        print(dict(row))
    print("notification_count =", db.execute("SELECT COUNT(*) FROM notifications WHERE user_id=1").fetchone()[0])
finally:
    db.close()
