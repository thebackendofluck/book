-- Migration: 003-add-unique-wallet-events
-- Target DB: new_acmetocasino (PostgreSQL 14+)
-- Reversible: yes
-- Precondition: migration 002 must have run and left zero duplicate groups.
--
-- Adds an idempotency_key column to wallet_events and a partial UNIQUE index
-- that enforces at-most-once semantics per (player_id, idempotency_key).
-- The index is partial (WHERE idempotency_key IS NOT NULL) to allow legacy
-- rows written before the column existed to remain NULL without conflict.
--
-- We also add a second UNIQUE on (reference_id, event_type) as defense-in-depth
-- against the exact bug reported in the audit. This is a HARD constraint; it
-- must not be added until 002 has verified zero duplicates.

\set ON_ERROR_STOP on

BEGIN;

-- Guard: refuse to proceed if duplicates still exist.
DO $$
DECLARE
    dup_count INTEGER;
BEGIN
    SELECT count(*) INTO dup_count
    FROM (
        SELECT reference_id, event_type
        FROM wallet_events
        WHERE reference_id IS NOT NULL
        GROUP BY reference_id, event_type
        HAVING count(*) > 1
    ) d;
    IF dup_count > 0 THEN
        RAISE EXCEPTION
            'Refusing to add UNIQUE: % duplicate groups still exist. Run 002 first.',
            dup_count;
    END IF;
END $$;

ALTER TABLE wallet_events
    ADD COLUMN IF NOT EXISTS idempotency_key TEXT;

-- End the transaction before building indexes: CREATE INDEX CONCURRENTLY
-- cannot run inside a transaction block.
COMMIT;

-- CONCURRENTLY: build without ACCESS EXCLUSIVE on the hot ledger.
-- Must run OUTSIDE a transaction block (autocommit); if a build is
-- interrupted it leaves an INVALID index -> DROP INDEX and re-run.
CREATE UNIQUE INDEX CONCURRENTLY IF NOT EXISTS idx_wallet_events_idem
    ON wallet_events (player_id, idempotency_key)
    WHERE idempotency_key IS NOT NULL;

CREATE UNIQUE INDEX CONCURRENTLY IF NOT EXISTS idx_wallet_events_reference_event
    ON wallet_events (reference_id, event_type)
    WHERE reference_id IS NOT NULL;

COMMENT ON COLUMN wallet_events.idempotency_key IS
    'Client-supplied Idempotency-Key forwarded by the middleware. '
    'Enforces at-most-once per (player_id, idempotency_key).';

-- ---------------------------------------------------------------------------
-- ROLLBACK
-- ---------------------------------------------------------------------------
-- BEGIN;
-- DROP INDEX IF EXISTS idx_wallet_events_reference_event;
-- DROP INDEX IF EXISTS idx_wallet_events_idem;
-- ALTER TABLE wallet_events DROP COLUMN IF EXISTS idempotency_key;
-- COMMIT;
