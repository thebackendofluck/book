-- Migration: 001-create-idempotency-records
-- Target DB: new_acmetocasino (PostgreSQL 14+)
-- Reversible: yes (see rollback block at bottom)
-- Idempotent: yes (IF NOT EXISTS)
--
-- Creates the idempotency_records table used by the FastAPI middleware.
-- Safe to run on prod during a maintenance window; no data migration.

BEGIN;

CREATE TABLE IF NOT EXISTS idempotency_records (
    key              TEXT        PRIMARY KEY,
    user_id          TEXT,
    path             TEXT        NOT NULL,
    body_hash        TEXT        NOT NULL,
    state            TEXT        NOT NULL
                                 CHECK (state IN ('in_progress', 'terminal')),
    response_status  INTEGER,
    response_body    BYTEA,
    response_headers JSONB,
    created_at       TIMESTAMPTZ NOT NULL DEFAULT now(),
    expires_at       TIMESTAMPTZ NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_idempotency_expires
    ON idempotency_records (expires_at);

CREATE INDEX IF NOT EXISTS idx_idempotency_user_path
    ON idempotency_records (user_id, path);

COMMENT ON TABLE  idempotency_records IS
    'HTTP request idempotency cache. Rows are insert-as-lock (state=in_progress) '
    'then updated to state=terminal with the cached response. See '
    'new-platform/idempotency-impl/middleware/idempotency.py';
COMMENT ON COLUMN idempotency_records.body_hash IS
    'SHA-256 hex of canonicalized JSON body (keys sorted, no whitespace).';

COMMIT;

-- ---------------------------------------------------------------------------
-- ROLLBACK
-- ---------------------------------------------------------------------------
-- BEGIN;
-- DROP TABLE IF EXISTS idempotency_records;
-- COMMIT;
