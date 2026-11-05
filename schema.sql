DROP TABLE IF EXISTS notifications;
DROP TABLE IF EXISTS sync_status;
DROP TABLE IF EXISTS price_targets;
DROP TABLE IF EXISTS price_observations;
DROP TABLE IF EXISTS games;
DROP TABLE IF EXISTS users;

CREATE TABLE users (
    id INTEGER PRIMARY KEY,
    display_name TEXT NOT NULL
);

CREATE TABLE games (
    id INTEGER PRIMARY KEY,
    title TEXT NOT NULL,
    subtitle TEXT NOT NULL,
    genre TEXT NOT NULL,
    platform TEXT NOT NULL,
    region TEXT NOT NULL,
    currency TEXT NOT NULL,
    data_source TEXT NOT NULL DEFAULT 'demo',
    cheapshark_game_id TEXT,
    steam_app_id TEXT,
    demo_game_id INTEGER REFERENCES games(id),
    is_tracked INTEGER NOT NULL DEFAULT 1 CHECK (is_tracked IN (0, 1)),
    accent TEXT NOT NULL
);

CREATE TABLE price_observations (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    source_event_id TEXT NOT NULL UNIQUE,
    game_id INTEGER NOT NULL,
    price_cents INTEGER NOT NULL CHECK (price_cents >= 0),
    observed_at TEXT NOT NULL,
    source_updated_at INTEGER,
    FOREIGN KEY (game_id) REFERENCES games (id)
);

CREATE TABLE sync_status (
    source_name TEXT PRIMARY KEY,
    last_attempt_at TEXT NOT NULL,
    last_success_at TEXT,
    last_error TEXT
);

CREATE TABLE price_targets (
    user_id INTEGER NOT NULL,
    game_id INTEGER NOT NULL,
    target_cents INTEGER NOT NULL CHECK (target_cents > 0),
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (user_id, game_id),
    FOREIGN KEY (user_id) REFERENCES users (id),
    FOREIGN KEY (game_id) REFERENCES games (id)
);

CREATE INDEX idx_price_observations_game_time
ON price_observations (game_id, observed_at DESC);
