-- =============================================================================
-- FILE NOTE: This UPPERCASE file (VNJDGE03WALLETTRANSFERS.sql) uses Oracle naming conventions
-- (ALL-CAPS, VNJDGE* prefix) but contains PostgreSQL-compatible SQL.
-- Both this file and its lowercase counterpart are functionally identical.
-- The UPPERCASE naming is a legacy convention from the platform's Oracle origins.
-- See the lowercase counterpart in this directory for the full regulatory header.
-- NOTE: These views are PostgreSQL only; Oracle would require different syntax
-- (e.g., no DISTINCT ON, different timezone functions, no CREATE OR REPLACE VIEW).
-- =============================================================================
-- NJ DGE View 03: WALLET TRANSFERS
-- Complete transaction log for every patron's casino and poker game fund transfers.
-- Maps internal change types to DGE-standard TOWALLET/FROMWALLET categories.
--
-- Source: casino_core.user_account_history (uah)
-- Session matching: joins temp_user_session_persistent on time-window overlap.
--   Sessions without a match produce SESSION_ID = 'NOSESSION'.
-- Sports wager records use refsystem = -5; game name is resolved from feed_messages.
--
-- Key design choices:
--   - amount is stored in cents; divided by 100 for AMOUNT column
--   - 'countdown_bonus' changetype is excluded (internal mechanic, not reportable)
--   - ORDER BY uah.changedate ensures chronological output for the regulator feed
-- Supported by indexes: idx_uah_userid, idx_uah_comments, idx_fm_combination_ref,
--                        idx_tempuser_session_userid_created_invalidation

CREATE OR REPLACE VIEW dge."vNJDGE03WALLETTRANSFERS" AS
SELECT
    'ACMETOCASINO'::varchar                               AS SKIN_NAME,
    uah.userid::varchar                                 AS PATRON_ACCOUNT_ID,
    CASE hub_user_session.userid IS NOT NULL
        WHEN true THEN concat(hub_user_session.userid, '-', extract(epoch FROM hub_user_session.created))
        ELSE 'NOSESSION'
    END::varchar                                        AS SESSION_ID,
    uah.id::varchar                                     AS WALLET_TRANSACTION_ID,
    'N'::varchar                                        AS TRANSACTION_FAILURE_INDICATOR,
    to_char(uah.changedate, 'YYYYMMDD HH24MISS.MS +00:00')::varchar
                                                        AS TRANSFERTIME_SYSTEM,
    concat(
        to_char(uah.changedate AT TIME ZONE 'UTC' AT TIME ZONE 'America/New_York', 'YYYYMMDD HH24MISS.MS '),
        to_char(uah.changedate AT TIME ZONE 'America/New_York' - uah.changedate AT TIME ZONE 'UTC', '-HH24:MI')
    )::varchar                                          AS TRANSFERTIME_EASTERN,
    upper(regexp_replace(gc."name", '\W+', '', 'g'))::varchar
                                                        AS GAME_TYPE,
    CASE WHEN g.refsystem = '-5' THEN upper(fm.event_group)
         ELSE upper(g."name")
    END::varchar                                        AS GAME_NAME,
    '000.000.0000'::varchar                             AS GAME_VERSION,
    upper(regexp_replace(s."name", '\W+', '', 'g'))::varchar
                                                        AS RGS_NAME,
    CASE
        WHEN uah.changetype = 'manual_change' AND uah.referralid = 'credit' THEN 'TOWALLET'
        WHEN uah.changetype = 'tax_deducted' AND uat.category = 'cash' THEN 'FROMWALLET'
        WHEN uah.changetype IN ('tax_released', 'released_bonus') AND uat.category = 'cash' THEN 'TOWALLET'
        WHEN uah.changetype IN (
            'deposit', 'credit', 'refund', 'withdraw_reversed', 'withdraw_rejected',
            'withdraw_returned', 'rollover_bonus', 'cash_bonus', 'refund_bonus',
            'tip_cancel', 'tax_deducted', 'transferin'
        ) THEN 'TOWALLET'
        ELSE 'FROMWALLET'
    END::varchar                                        AS WALLET_TRANSFER_TYPE,
    CASE
        WHEN uah.changetype IN ('debit', 'credit') THEN upper(regexp_replace(uat.category, '\W+', '', 'g'))
        WHEN uah.changetype = 'revoke' THEN 'EXPIRED'
        WHEN uah.changetype IN ('tax_deducted', 'tax_paid') THEN 'STATE_TAX_DEDUCTION'
        WHEN uah.changetype LIKE '%bonus%' OR uah.changetype LIKE '%withdraw%' THEN upper(uah.changetype)
        ELSE upper(concat(uah.changetype, '_', uat.category))
    END::varchar                                        AS WALLET_TRANSFER_DESCRIPTION,
    cast(uah.amount / 100 AS numeric)                   AS AMOUNT,
    uah.changedate AT TIME ZONE 'UTC' AT TIME ZONE 'America/New_York'
                                                        AS TRANSFERTIME_EASTERN_INDEX
FROM casino_core.user_account_history uah
JOIN casino_core.user_accounts ua           ON uah.accountid = ua.id
JOIN casino_core.user_account_types uat     ON ua.typeid = uat.id
JOIN casino_replica.temp_user_session_persistent hub_user_session
    ON uah.userid = hub_user_session.userid
    AND uah.changedate > hub_user_session.created
    AND (uah.changedate < hub_user_session.invalidation_time
         OR hub_user_session.invalidation_time IS NULL)
LEFT JOIN casino_core.games g               ON uah.referralsystem = g.refsystem::varchar
LEFT JOIN casino_core.game_categories gc    ON g.categoryid = gc.id
LEFT JOIN casino_core.suppliers s           ON g.supplierid = s.id
LEFT JOIN (
    SELECT DISTINCT ON (fm.combination_ref) *
    FROM analytics_dw.feed_messages fm
    ORDER BY fm.combination_ref DESC
) fm ON uah."comments" = fm.combination_ref::text
WHERE uah.amount <> 0.00
  AND uah.changetype <> 'countdown_bonus'
ORDER BY uah.changedate;

GRANT SELECT ON "dge"."vNJDGE03WALLETTRANSFERS" TO dge_readonly_external;
