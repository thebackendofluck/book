-- D1 schema for Cloudflare Worker idempotency.
-- Apply with: wrangler d1 execute <DB_NAME> --file=./d1-schema.sql

CREATE TABLE IF NOT EXISTS idempotency_records (
    key              TEXT PRIMARY KEY,
    user_id          TEXT,
    path             TEXT NOT NULL,
    body_hash        TEXT NOT NULL,
    state            TEXT NOT NULL CHECK (state IN ('in_progress','terminal')),
    response_status  INTEGER,
    response_body    TEXT,
    response_headers TEXT,
    created_at       INTEGER NOT NULL,
    expires_at       INTEGER NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_idempotency_expires
    ON idempotency_records (expires_at);

CREATE INDEX IF NOT EXISTS idx_idempotency_user_path
    ON idempotency_records (user_id, path);
