-- Migration: 002-dedup-wallet-events
-- Target DB: new_acmetocasino (PostgreSQL 14+)
-- Reversible: yes (backup table preserved for 90 days)
-- Destructive: YES - deletes duplicate rows. DO NOT RUN WITHOUT DBA SIGN-OFF.
--
-- Deduplicates wallet_events rows that share (reference_id, event_type).
-- Preserves the OLDEST row in each duplicate group (lowest id).
--
-- Rationale: the platform currently emits 4 BET events per logical bet because
-- the wallet.create_event call is invoked multiple times (audit
-- security/idempotency-audit-2026-04-14.md, section I-CRIT-01).
-- We must dedup BEFORE adding the UNIQUE constraint (migration 003).
--
-- Must be run inside a maintenance window with writes paused on wallet_events.

\set ON_ERROR_STOP on

BEGIN;

-- Step 1: Full backup (timestamp is static, matches audit date).
DROP TABLE IF EXISTS wallet_events_predeup_2026_04_14;
CREATE TABLE wallet_events_predeup_2026_04_14 AS
SELECT * FROM wallet_events;

SELECT 'BEFORE_DEDUP' AS phase, count(*) AS row_count FROM wallet_events;

-- Step 2: Identify duplicate groups for reporting.
CREATE TEMP TABLE _dup_report AS
SELECT reference_id,
       event_type,
       count(*) AS dup_count,
       min(id)  AS keep_id,
       array_agg(id ORDER BY id) AS all_ids
FROM wallet_events
WHERE reference_id IS NOT NULL
GROUP BY reference_id, event_type
HAVING count(*) > 1;

SELECT 'DUPLICATE_GROUPS_FOUND' AS phase,
       count(*)           AS groups,
       coalesce(sum(dup_count - 1), 0) AS rows_to_delete
FROM _dup_report;

-- Step 3: Delete all but the oldest row per group.
WITH victims AS (
    SELECT id
    FROM wallet_events we
    WHERE reference_id IS NOT NULL
      AND EXISTS (
          SELECT 1 FROM _dup_report d
          WHERE d.reference_id = we.reference_id
            AND d.event_type   = we.event_type
            AND we.id <> d.keep_id
      )
)
DELETE FROM wallet_events
WHERE id IN (SELECT id FROM victims);

SELECT 'AFTER_DEDUP' AS phase, count(*) AS row_count FROM wallet_events;

-- Step 4: Sanity check - no more duplicates should exist.
SELECT 'REMAINING_DUPLICATES' AS phase,
       count(*) AS groups
FROM (
    SELECT reference_id, event_type
    FROM wallet_events
    WHERE reference_id IS NOT NULL
    GROUP BY reference_id, event_type
    HAVING count(*) > 1
) d;

COMMIT;

-- ---------------------------------------------------------------------------
-- ROLLBACK
-- ---------------------------------------------------------------------------
-- To fully restore pre-dedup state (within 90-day backup retention):
--
-- BEGIN;
-- TRUNCATE wallet_events;
-- INSERT INTO wallet_events SELECT * FROM wallet_events_predeup_2026_04_14;
-- COMMIT;
--
-- After 90 days of clean operation, drop the backup:
--   DROP TABLE wallet_events_predeup_2026_04_14;
