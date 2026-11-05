CREATE TABLE IF NOT EXISTS admin_action_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER NOT NULL,
    action_type TEXT NOT NULL CHECK (action_type IN ('sync_demo', 'sync_cheapshark')),
    source TEXT NOT NULL CHECK (source IN ('demo', 'cheapshark')),
    outcome TEXT NOT NULL CHECK (outcome IN ('running', 'success', 'failure')),
    summary TEXT NOT NULL CHECK (length(summary) <= 500),
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    finished_at TEXT,
    FOREIGN KEY (user_id) REFERENCES users(id)
);
