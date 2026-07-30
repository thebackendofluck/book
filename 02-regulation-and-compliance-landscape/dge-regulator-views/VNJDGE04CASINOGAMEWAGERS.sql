-- =============================================================================
-- FILE NOTE: This UPPERCASE file (VNJDGE04CASINOGAMEWAGERS.sql) uses Oracle naming conventions
-- (ALL-CAPS, VNJDGE* prefix) but contains PostgreSQL-compatible SQL.
-- Both this file and its lowercase counterpart are functionally identical.
-- The UPPERCASE naming is a legacy convention from the platform's Oracle origins.
-- See the lowercase counterpart in this directory for the full regulatory header.
-- NOTE: These views are PostgreSQL only; Oracle would require different syntax
-- (e.g., no DISTINCT ON, different timezone functions, no CREATE OR REPLACE VIEW).
-- =============================================================================
-- NJ DGE View 04: CASINO GAME WAGERS
-- Complete transaction log for casino gaming wagers (slots, roulette, etc.).
-- Calculates coin-in, coin-out, and win/loss per game round using
-- correlated subqueries against user_account_history.
--
-- Source: casino_core.user_game_round (one row per completed or failed round)
-- Excludes categoryid = 8 (sports/non-casino game categories).
--
-- Coin-in/out calculation pattern:
--   Each game round links to user_account_history via round_id = comments.
--   Three separate correlated subqueries sum debit (coin-in), credit+refund (coin-out),
--   and the net win/loss. All values divided by 100 (stored in cents).
--
-- Key design choices:
--   - CASINO_GAME_TRANS_FAILURE_FLAG = 'Y' for any non-CLOSED round status
--   - RGS_NAME resolved from a static supplier ID lookup (extend as new suppliers onboard)
--   - Session match uses started_at time-window; no user_id join on session (hub architecture)
-- Supported by indexes: idx_ugr_userid, idx_ugr_started_at, idx_ugr_round_id,
--                        idx_uah_comments_changetype

CREATE OR REPLACE VIEW "dge"."vNJDGE04CASINOGAMEWAGERS" AS
SELECT
    'ACMETOCASINO' AS SKIN_NAME,
    spoke_user_game_round.user_id::text AS PATRON_ACCOUNT_ID,
    CASE hub_user_session.userid IS NOT NULL
        WHEN true THEN CONCAT(hub_user_session.userid, '-', extract(epoch FROM hub_user_session.created))
        ELSE 'NOSESSION'
    END AS SESSION_ID,
    spoke_user_game_round.round_id AS CASINO_GAME_TRANSACTION_ID,
    CASE
        WHEN spoke_user_game_round.status != 'CLOSED' THEN 'Y'
        ELSE 'N'
    END AS CASINO_GAME_TRANS_FAILURE_FLAG,
    TO_CHAR(spoke_user_game_round.started_at, 'YYYYMMDD HH24MISS.MS +00:00')
        AS CASINO_GAME_STARTTIME_SYSTEM,
    TO_CHAR(spoke_user_game_round.completed_at, 'YYYYMMDD HH24MISS.MS +00:00')
        AS CASINO_GAME_ENDTIME_SYSTEM,
    CONCAT(
        TO_CHAR(spoke_user_game_round.started_at AT TIME ZONE 'UTC' AT TIME ZONE 'America/New_York', 'YYYYMMDD HH24MISS.MS '),
        TO_CHAR(spoke_user_game_round.started_at AT TIME ZONE 'America/New_York' - spoke_user_game_round.started_at AT TIME ZONE 'UTC', '-HH24:MI')
    ) AS CASINO_GAME_STARTTIME_EASTERN,
    CONCAT(
        TO_CHAR(spoke_user_game_round.completed_at AT TIME ZONE 'UTC' AT TIME ZONE 'America/New_York', 'YYYYMMDD HH24MISS.MS '),
        TO_CHAR(spoke_user_game_round.completed_at AT TIME ZONE 'America/New_York' - spoke_user_game_round.completed_at AT TIME ZONE 'UTC', '-HH24:MI')
    ) AS CASINO_GAME_ENDTIME_EASTERN,
    spoke_games.title AS CASINO_GAME_NAME,
    '000.000.0000' AS CASINO_GAME_VERSION,
    CASE spoke_games.supplierid
        WHEN 64 THEN 'IGT'
        WHEN 24 THEN 'NETENT'
        WHEN 13 THEN 'NYXOGS'
        WHEN 19 THEN 'EZUGI'
        WHEN 29 THEN 'EVOLUTION'
        WHEN 33 THEN 'KAMBI'
        WHEN 35 THEN 'PGS'
    END AS RGS_NAME,
    -- Coin-in: sum of all debit transactions for this round
    COALESCE((
        SELECT COALESCE(SUM(
            CASE WHEN h.changetype = 'debit' THEN h.amount END
        ), 0.0) / 100
        FROM casino_core.user_account_history h
        WHERE h."comments" = spoke_user_game_round.round_id
    ), 0.0) AS CASINO_GAME_COIN_IN,
    -- Coin-out: sum of all credit + refund transactions
    COALESCE((
        SELECT (
            COALESCE(SUM(CASE WHEN h.changetype = 'credit' THEN h.amount END), 0.0) +
            COALESCE(SUM(CASE WHEN h.changetype = 'refund' THEN h.amount END), 0.0)
        ) / 100
        FROM casino_core.user_account_history h
        WHERE h."comments" = spoke_user_game_round.round_id
    ), 0.0) AS CASINO_GAME_COIN_OUT,
    0.0 AS CASINO_FEE,
    -- Win/loss: coin_out - coin_in
    COALESCE((
        SELECT (
            COALESCE(SUM(CASE WHEN h.changetype = 'credit' THEN h.amount END), 0.0) +
            COALESCE(SUM(CASE WHEN h.changetype = 'refund' THEN h.amount END), 0.0) -
            COALESCE(SUM(CASE WHEN h.changetype = 'debit' THEN h.amount END), 0.0)
        ) / 100
        FROM casino_core.user_account_history h
        WHERE h."comments" = spoke_user_game_round.round_id
    ), 0.0) AS CASINO_GAME_WINLOSS,
    NULL AS CASINO_GAME_IMO,
    spoke_user_game_round.started_at AT TIME ZONE 'UTC' AT TIME ZONE 'America/New_York'
        AS CASINO_GAME_STARTTIME_EASTERN_INDEX
FROM casino_core.user_game_round spoke_user_game_round
JOIN casino_core.games spoke_games
    ON spoke_games.id = spoke_user_game_round.game_id
    AND spoke_games.categoryid != 8  -- exclude non-casino categories
LEFT JOIN casino_replica.temp_user_session_persistent hub_user_session
    ON spoke_user_game_round.started_at > hub_user_session.created
    AND (spoke_user_game_round.started_at < hub_user_session.invalidation_time
         OR hub_user_session.invalidation_time IS NULL);

GRANT SELECT ON "dge"."vNJDGE04CASINOGAMEWAGERS" TO dge_readonly_external;
