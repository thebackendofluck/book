-- =============================================================================
-- FILE NOTE: This UPPERCASE file (VNJDGE07CASHTRANS.sql) uses Oracle naming conventions
-- (ALL-CAPS, VNJDGE* prefix) but contains PostgreSQL-compatible SQL.
-- Both this file and its lowercase counterpart are functionally identical.
-- The UPPERCASE naming is a legacy convention from the platform's Oracle origins.
-- See the lowercase counterpart in this directory for the full regulatory header.
-- NOTE: These views are PostgreSQL only; Oracle would require different syntax
-- (e.g., no DISTINCT ON, different timezone functions, no CREATE OR REPLACE VIEW).
-- =============================================================================
-- NJ DGE View 07: CASH TRANSACTIONS
-- Complete log of patron cash deposits and withdrawals.
-- Two-part UNION ALL: deposits from user_payments, withdrawals from user_withdraws.
-- Both filtered to US-NJ jurisdiction only (WHERE jurisdiction_id = 'US-NJ').
--
-- Deposit source: casino_replica.user_payments
--   CASH_TRANSACTION_AMOUNT = actual amount only for SUCCEEDED/VOIDED; 0 otherwise
--   CASH_TRANSACTION_REQAMOUNT = requested amount (always set)
--
-- Withdrawal source: casino_replica.user_withdraws
--   CASH_TRANSACTION_AMOUNT = actual amount only for status 1 (ACCEPTED),
--                             3 (REVERSED), 8 (RETURNED); 0 otherwise
--   Withdrawal status codes are mapped to descriptive strings for DGE readability
--
-- Key design choices:
--   - UNION ALL used (not UNION) to avoid deduplication cost; deposits and withdrawals
--     have different ID spaces so no natural duplicates exist
--   - Payment method label and provider name sanitized with regexp_replace
-- Supported by indexes: idx_up_user_id, idx_up_jurisdiction_id,
--                        idx_uw_changedate, idx_uw_jurisdiction_id

CREATE OR REPLACE VIEW dge."vNJDGE07CASHTRANS" AS
-- Part 1: Deposits
SELECT
    'ACMETOCASINO'::varchar                               AS SKIN_NAME,
    up.user_id::varchar                                 AS PATRON_ACCOUNT_ID,
    up.id::varchar                                      AS CASH_TRANSACTION_ID,
    to_char(up.date_updated, 'YYYYMMDD HH24MISS.MS +00:00')::varchar
                                                        AS CASH_TRANSACTIONTIME_SYSTEM,
    concat(
        to_char(up.date_updated AT TIME ZONE 'UTC' AT TIME ZONE 'America/New_York', 'YYYYMMDD HH24MISS.MS '),
        to_char(up.date_updated AT TIME ZONE 'America/New_York' - up.date_updated AT TIME ZONE 'UTC', '-HH24:MI')
    )::varchar                                          AS CASH_TRANSACTIONTIME_EASTERN,
    CASE
        WHEN up.status IN ('SUCCEEDED', 'VOIDED')
        THEN cast(up.amount / 100 AS numeric)
        ELSE 0.00
    END                                                 AS CASH_TRANSACTION_AMOUNT,
    'DEPOSIT'::varchar                                  AS CASH_TRANSACTION_TYPE,
    up.status::varchar                                  AS CASH_TRANSACTION_DESCRIPTION,
    upper(regexp_replace(pm.label, '\W+', '', 'g'))::varchar
                                                        AS CASH_TRANSACTION_SOURCE_TYPE,
    upper(regexp_replace(pp."name", '\W+', '', 'g'))::varchar
                                                        AS CASH_TRANSACTION_PROVIDER,
    cast(up.amount / 100 AS numeric)                    AS CASH_TRANSACTION_REQAMOUNT,
    up.date_updated AT TIME ZONE 'UTC' AT TIME ZONE 'America/New_York'
                                                        AS CASH_TRANSACTIONTIME_EASTERN_INDEX
FROM casino_replica.user_payments up
JOIN casino_replica.payment_method pm  ON up.payment_method = pm."name"
JOIN casino_replica.payment_provider pp ON up.provider_id = pp.id
WHERE up.jurisdiction_id = 'US-NJ'

UNION ALL

-- Part 2: Withdrawals
SELECT
    'ACMETOCASINO'::varchar                               AS SKIN_NAME,
    uw.userid::varchar                                  AS PATRON_ACCOUNT_ID,
    uw.historyid::varchar                               AS CASH_TRANSACTION_ID,
    to_char(uw.changedate, 'YYYYMMDD HH24MISS.MS +00:00')::varchar
                                                        AS CASH_TRANSACTIONTIME_SYSTEM,
    concat(
        to_char(uw.changedate AT TIME ZONE 'UTC' AT TIME ZONE 'America/New_York', 'YYYYMMDD HH24MISS.MS '),
        to_char(uw.changedate AT TIME ZONE 'America/New_York' - uw.changedate AT TIME ZONE 'UTC', '-HH24:MI')
    )::varchar                                          AS CASH_TRANSACTIONTIME_EASTERN,
    CASE
        WHEN uw.status IN (1, 3, 8)
        THEN cast(uw.amount / 100 AS numeric)
        ELSE 0.00
    END                                                 AS CASH_TRANSACTION_AMOUNT,
    'WITHDRAWAL'::varchar                               AS CASH_TRANSACTION_TYPE,
    CASE uw.status
        WHEN 0 THEN 'PENDING'
        WHEN 1 THEN 'ACCEPTED'
        WHEN 2 THEN 'REJECTED'
        WHEN 3 THEN 'REVERSED'
        WHEN 4 THEN 'PROCESSING'
        WHEN 5 THEN 'FAILED'
        WHEN 6 THEN 'REVIEW'
        WHEN 7 THEN 'TIMED_OUT'
        WHEN 8 THEN 'RETURNED'
        WHEN 9 THEN 'BATCH_APPROVED'
    END::varchar                                        AS CASH_TRANSACTION_DESCRIPTION,
    upper(regexp_replace(pm.label, '\W+', '', 'g'))::varchar
                                                        AS CASH_TRANSACTION_SOURCE_TYPE,
    upper(regexp_replace(pp."name", '\W+', '', 'g'))::varchar
                                                        AS CASH_TRANSACTION_PROVIDER,
    cast(uw.amount / 100 AS numeric)                    AS CASH_TRANSACTION_REQAMOUNT,
    uw.changedate AT TIME ZONE 'UTC' AT TIME ZONE 'America/New_York'
                                                        AS CASH_TRANSACTIONTIME_EASTERN_INDEX
FROM casino_replica.user_withdraws uw
JOIN casino_replica.payment_method pm  ON uw.method = pm."name"
JOIN casino_replica.payment_provider pp ON uw.processor = pp.id
WHERE uw.jurisdiction_id = 'US-NJ';

GRANT SELECT ON "dge"."vNJDGE07CASHTRANS" TO dge_readonly_external;
