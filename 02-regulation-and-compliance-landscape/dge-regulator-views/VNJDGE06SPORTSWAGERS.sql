-- =============================================================================
-- FILE NOTE: This UPPERCASE file (VNJDGE06SPORTSWAGERS.sql) uses Oracle naming conventions
-- (ALL-CAPS, VNJDGE* prefix) but contains PostgreSQL-compatible SQL.
-- Both this file and its lowercase counterpart are functionally identical.
-- The UPPERCASE naming is a legacy convention from the platform's Oracle origins.
-- See the lowercase counterpart in this directory for the full regulatory header.
-- NOTE: These views are PostgreSQL only; Oracle would require different syntax
-- (e.g., no DISTINCT ON, different timezone functions, no CREATE OR REPLACE VIEW).
-- =============================================================================
-- NJ DGE View 06: SPORTS WAGERS
-- Transaction log for sports betting via the Kambi OSP integration.
-- Maps Kambi bet_status_id values to DGE-standard transaction types.
-- Extracts event IDs and league names from the JSONB betslip payload.
--
-- Source: analytics_dw.feed_messages (Kambi feed, one row per bet status change)
-- DISTINCT ON (combination_ref): one row per betslip, taking the latest status update.
--
-- Kambi bet_status_id mapping to DGE TRANSACTION_TYPE:
--   1 = OPEN     -> CREATED
--   2 = WON      -> PAID
--   3 = LOST     -> SETTLED
--   4 = VOID     -> CANCELLED
--   5 = CASHED_OUT -> PAID
--   8 = DELETED  -> CANCELLED
--
-- Key design choices:
--   - customer_player_id cast to DECIMAL for the join to players.id (Kambi uses string IDs)
--   - SPORTS_BETSLIP_ID and SPORTS_WAGER_ID both map to combination_ref (DGE uses separate fields)
--   - TO_WIN = stake * odds (pre-tax estimated win, not actual payout)
--   - SPORTS_WAGER_STYLE is always 'WIN' (no place/each-way betting in this integration)
-- Supported by indexes: idx_players_external_id, idx_fm_update_date, idx_fm_combination_ref,
--                        idx_tempuser_session_userid_created_invalidation

CREATE OR REPLACE VIEW "dge"."vNJDGE06SPORTSWAGERS" AS
SELECT DISTINCT ON (spoke_feed_messages.combination_ref)
    'ACMETOCASINO' AS SKIN_NAME,
    'KAMBI' AS OSP_NAME,
    CASE hub_user_session.userid IS NOT NULL
        WHEN true THEN CONCAT(hub_user_session.userid, '-', extract(epoch FROM hub_user_session.created))
        ELSE 'NOSESSION'
    END AS SESSION_ID,
    spoke_players.external_id::text AS PATRON_ACCOUNT_ID,
    spoke_feed_messages.id::text AS TRANSACTION_ID,
    TO_CHAR(spoke_feed_messages.update_date, 'YYYYMMDD HH24MISS.MS +00:00')
        AS TRANSACTIONTIME_SYSTEM,
    CONCAT(
        TO_CHAR(spoke_feed_messages.update_date AT TIME ZONE 'UTC' AT TIME ZONE 'America/New_York', 'YYYYMMDD HH24MISS.MS '),
        TO_CHAR(spoke_feed_messages.update_date AT TIME ZONE 'America/New_York' - spoke_feed_messages.update_date AT TIME ZONE 'UTC', '-HH24:MI')
    ) AS TRANSACTIONTIME_EASTERN,
    (
        CASE spoke_feed_messages.bet_status_id
            WHEN 1 THEN 'CREATED'     -- KAMBI-OPEN
            WHEN 2 THEN 'PAID'        -- KAMBI-WON
            WHEN 3 THEN 'SETTLED'     -- KAMBI-LOST
            WHEN 4 THEN 'CANCELLED'   -- KAMBI-VOID
            WHEN 5 THEN 'PAID'        -- KAMBI-CASHED OUT
            WHEN 8 THEN 'CANCELLED'   -- KAMBI-DELETED
        END
    ) AS TRANSACTION_TYPE,
    (
        CASE
            WHEN spoke_feed_messages.bet_status_id IN (4, 8)
                 AND spoke_feed_messages.acme_void_type = 'CANCELLED' THEN 'CANCELLED'
            WHEN spoke_feed_messages.bet_status_id IN (4, 8)
                 AND spoke_feed_messages.acme_void_type = 'VOIDED' THEN 'VOIDED'
            WHEN spoke_feed_messages.bet_status_id IN (4, 8) THEN 'N/A'
        END
    ) AS TRANSACTION_REASON,
    spoke_feed_messages.combination_ref::text AS SPORTS_BETSLIP_ID,
    spoke_feed_messages.combination_ref::text AS SPORTS_WAGER_ID,
    spoke_feed_messages.stake AS TO_WAGER,
    spoke_feed_messages.payout AS TO_PAY,
    spoke_feed_messages.payout AS ACTUAL_PAYOUT,
    COALESCE(
        (
            SELECT string_agg(upper(outcomes.outcome ->> 'eventId'), '-')
            FROM jsonb_array_elements(spoke_feed_messages.json -> 'combination' -> 'outcomes') AS outcomes(outcome)
        ),
        'NONE'
    ) AS SPORTS_WAGER_EVENT_ID,
    COALESCE(
        (
            SELECT string_agg(upper(eventGroups.eventGroup ->> 'name'), ', ')
            FROM jsonb_array_elements(spoke_feed_messages.json -> 'combination' -> 'outcomes') AS outcomes(outcome),
                 jsonb_array_elements(outcomes.outcome -> 'eventGroupPath') AS eventGroups(eventGroup)
        ),
        'NONE'
    ) AS SPORTS_WAGER_LEAGUES,
    spoke_feed_messages.json -> 'combination' ->> 'odds' AS SPORTS_WAGER_ODDS,
    (
        COALESCE(spoke_feed_messages.stake::DECIMAL, 0.0) *
        COALESCE((spoke_feed_messages.json -> 'combination' ->> 'odds')::DECIMAL, 0.0)
    ) AS TO_WIN,
    0.0 AS FEE_PAID,
    CASE WHEN spoke_feed_messages.is_combination = true THEN 'PARLEY' ELSE 'STRAIGHT' END
        AS SPORTS_WAGER_TYPE,
    'WIN' AS SPORTS_WAGER_STYLE,
    spoke_feed_messages.update_date AT TIME ZONE 'UTC' AT TIME ZONE 'America/New_York'
        AS TRANSACTIONTIME_EASTERN_INDEX
FROM analytics_dw.feed_messages spoke_feed_messages
JOIN casino_core.players spoke_players
    ON spoke_feed_messages.customer_player_id::DECIMAL = spoke_players.id
LEFT JOIN casino_replica.temp_user_session_persistent hub_user_session
    ON spoke_feed_messages.update_date > hub_user_session.created
    AND (spoke_feed_messages.update_date < hub_user_session.invalidation_time
         OR hub_user_session.invalidation_time IS NULL)
    AND hub_user_session.userid::text = spoke_players.external_id
ORDER BY spoke_feed_messages.combination_ref DESC, spoke_feed_messages.id DESC;

GRANT SELECT ON "dge"."vNJDGE06SPORTSWAGERS" TO dge_readonly_external;
