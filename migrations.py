"""Repeatable, transactional upgrades for the USD database only."""
from pathlib import Path
from contextlib import contextmanager


@contextmanager
def migration_transaction(db):
    """Allow individual upgrades and an atomic upgrade-db orchestration."""
    if db.in_transaction:
        yield
    else:
        with db:
            db.execute("BEGIN IMMEDIATE")
            yield


def upgrade_user_tracking(db):
    """Snapshot the old shared list once; never restore later cancellations."""
    with migration_transaction(db):
        had_table = db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='user_game_tracking'").fetchone() is not None
        db.execute("""CREATE TABLE IF NOT EXISTS schema_migrations (
            version TEXT PRIMARY KEY, applied_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP)""")
        db.execute("""CREATE TABLE IF NOT EXISTS user_game_tracking (
            user_id INTEGER NOT NULL, game_id INTEGER NOT NULL,
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            PRIMARY KEY (user_id, game_id),
            FOREIGN KEY (user_id) REFERENCES users(id),
            FOREIGN KEY (game_id) REFERENCES games(id))""")
        db.execute("CREATE INDEX IF NOT EXISTS idx_tracking_game_user ON user_game_tracking(game_id,user_id)")
        version = "user_game_tracking_v1"
        if db.execute("SELECT 1 FROM schema_migrations WHERE version=?", (version,)).fetchone():
            return
        columns = {row[1] for row in db.execute("PRAGMA table_info(games)")}
        if "is_tracked" in columns:
            db.execute("""INSERT INTO user_game_tracking(user_id,game_id)
                SELECT u.id,g.id FROM users u CROSS JOIN games g WHERE g.is_tracked=1
                ON CONFLICT(user_id,game_id) DO NOTHING""")
        elif not had_table:
            # Older releases before the global flag showed every game.
            db.execute("""INSERT INTO user_game_tracking(user_id,game_id)
                SELECT u.id,g.id FROM users u CROSS JOIN games g WHERE 1
                ON CONFLICT(user_id,game_id) DO NOTHING""")
        db.execute("INSERT INTO schema_migrations(version) VALUES (?)", (version,))


def upgrade_notification_retries(db):
    """Add failed-delivery snapshots without moving or deleting subscriptions."""
    with migration_transaction(db):
        db.execute((Path(__file__).with_name("notification_retries.sql")).read_text(encoding="utf-8"))


def upgrade_admin_action_log(db):
    with migration_transaction(db):
        db.execute((Path(__file__).with_name("admin_action_log.sql")).read_text(encoding="utf-8"))


def upgrade_accounts(db):
    with migration_transaction(db):
        columns = {row[1] for row in db.execute("PRAGMA table_info(users)")}
        for name, definition in [
            ("username", "TEXT"),
            ("username_normalized", "TEXT"),
            ("password_hash", "TEXT"),
            ("role", "TEXT NOT NULL DEFAULT 'user' CHECK (role IN ('user', 'admin'))"),
            ("created_at", "TEXT"),
        ]:
            if name not in columns:
                db.execute(f"ALTER TABLE users ADD COLUMN {name} {definition}")
        for row in db.execute("SELECT id FROM users WHERE username_normalized IS NULL").fetchall():
            username = "demo" if row[0] == 1 else f"legacy_{row[0]}"
            db.execute("UPDATE users SET username=?, username_normalized=? WHERE id=?", (username, username, row[0]))
        db.execute("UPDATE users SET created_at=CURRENT_TIMESTAMP WHERE created_at IS NULL")
        db.execute("""CREATE UNIQUE INDEX IF NOT EXISTS uq_users_username
                      ON users(username_normalized COLLATE NOCASE)""")

# Explicit legacy associations; never infer a counterpart by adding/subtracting IDs.
LEGACY_GAMES = {
    101: ("413150", 1),
    102: ("367520", 2),
    103: ("1426210", 3),
    104: ("1868140", 4),
}


def upgrade_game_identifiers(db, backfill_legacy=True):
    with migration_transaction(db):
        columns = {row[1] for row in db.execute("PRAGMA table_info(games)")}
        for name, definition in [
            ("cheapshark_game_id", "TEXT"),
            ("steam_app_id", "TEXT"),
            ("demo_game_id", "INTEGER REFERENCES games(id)"),
        ]:
            if name not in columns:
                db.execute(f"ALTER TABLE games ADD COLUMN {name} {definition}")
        for game_id, (steam_id, demo_id) in (LEGACY_GAMES.items() if backfill_legacy else ()):
            db.execute(
                """
                UPDATE games SET steam_app_id = COALESCE(steam_app_id, ?),
                    demo_game_id = COALESCE(demo_game_id, (
                        SELECT id FROM games WHERE id = ? AND data_source = 'demo'
                    ))
                WHERE id = ? AND data_source = 'cheapshark'
                  AND platform = 'Steam' AND currency = 'USD'
                """,
                (steam_id, demo_id, game_id),
            )
        db.execute("""
            CREATE UNIQUE INDEX IF NOT EXISTS uq_real_cheapshark_id
            ON games(data_source, cheapshark_game_id)
            WHERE data_source = 'cheapshark' AND cheapshark_game_id IS NOT NULL
        """)
        db.execute("""
            CREATE UNIQUE INDEX IF NOT EXISTS uq_real_steam_app_id
            ON games(data_source, steam_app_id)
            WHERE data_source = 'cheapshark' AND steam_app_id IS NOT NULL
        """)
