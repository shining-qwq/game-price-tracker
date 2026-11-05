"""Repeatable, transactional upgrades for the USD database only."""

# Explicit legacy associations; never infer a counterpart by adding/subtracting IDs.
LEGACY_GAMES = {
    101: ("413150", 1),
    102: ("367520", 2),
    103: ("1426210", 3),
    104: ("1868140", 4),
}


def upgrade_game_identifiers(db):
    with db:
        db.execute("BEGIN IMMEDIATE")
        columns = {row[1] for row in db.execute("PRAGMA table_info(games)")}
        for name, definition in [
            ("cheapshark_game_id", "TEXT"),
            ("steam_app_id", "TEXT"),
            ("demo_game_id", "INTEGER REFERENCES games(id)"),
            ("is_tracked", "INTEGER NOT NULL DEFAULT 1 CHECK (is_tracked IN (0, 1))"),
        ]:
            if name not in columns:
                db.execute(f"ALTER TABLE games ADD COLUMN {name} {definition}")
        for game_id, (steam_id, demo_id) in LEGACY_GAMES.items():
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
