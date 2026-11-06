-- Safe to run against the existing database; no price or subscription is deleted.
CREATE TABLE IF NOT EXISTS notifications (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER NOT NULL,
    game_id INTEGER NOT NULL,
    source_event_id TEXT NOT NULL,
    trigger_price_cents INTEGER NOT NULL CHECK (trigger_price_cents >= 0),
    target_cents INTEGER NOT NULL CHECK (target_cents > 0),
    observed_at TEXT NOT NULL,
    created_at TEXT NOT NULL,
    read_at TEXT,
    UNIQUE (user_id, game_id, source_event_id),
    FOREIGN KEY (user_id, game_id) REFERENCES price_targets (user_id, game_id),
    FOREIGN KEY (source_event_id) REFERENCES price_observations (source_event_id)
);
