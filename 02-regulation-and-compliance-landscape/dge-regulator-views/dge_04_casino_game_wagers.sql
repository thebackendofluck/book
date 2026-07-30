-- =============================================================================
-- REGULATORY REQUIREMENT: NJ DGE (Division of Gaming Enforcement)
-- Regulation:  N.J.A.C. 13:69O-1.4(d) — Internet Gaming System Standards
-- Section:     §1.4(d)1 — Casino game transaction logging; §1.4(e) — Regulator access
-- Purpose:     Per-round casino wager audit trail with coin-in/coin-out for GGR
--              reconciliation, RTP verification, and dispute resolution by DGE
-- Retention:   10 years minimum (N.J.A.C. 13:69O-1.4(l))
-- Audit Access: Continuous read-only DGE access required
-- Penalty:     License suspension or revocation (N.J.S.A. 5:12-130)
-- Note:        CASINO_GAME_VERSION = '000.000.0000' — game version not currently
--              captured in this schema. RGS_NAME covers Suppliers 13/19/24/29/33/35/64
--              only; extend the CASE when new suppliers are onboarded.
-- Last Verified: March 2026
--
-- Also applicable to:
--   PA PGCB   — 58 Pa. Code §436a.6 (game round records, 10 years)
--   MI MGCB   — MGCB-RAG-21-01 §4.7 (casino game wager logging)
--   Ontario AGCO — Standard 3.4 (game event records)
--   MGA       — Gaming Compliance Directive §7 (game round completeness)
--   UKGC      — LCCP SR Code 3.4.1 + RTS 8.1 (game round records)
--   Sweden    — Spelinspektionen regs (game event logging, GGR verification)
--   Curacao CGA/LOK — Technical Standards (game transaction records, KYC now
--              mandatory under new LOK regime, effective Dec 2024)
--
-- References:
--   N.J.A.C. 13:69O: https://www.njleg.state.nj.us/TitleSearch?TitleNum=13&ChapterNum=69O
--   NJ DGE Technical Standards: https://www.nj.gov/oag/ge/docs/TechStds/InternetGaming/
--   N.J.S.A. 5:12 (Casino Control Act): https://www.njleg.state.nj.us/TitleSearch?TitleNum=5&ChapterNum=12
--   UKGC LCCP: https://www.gamblingcommission.gov.uk/licensees-and-businesses/lccp
--   UKGC Remote Technical Standards: https://www.gamblingcommission.gov.uk/standards/remote-technical-standards
--   MGA Gaming Act: https://www.mga.org.mt/legislation/gaming-act/
--   CGA (Curacao Gaming Authority): https://curacaogamingauthority.com/
--   LOK (Landsverordening op de Kansspelen): https://curacaogamingauthority.com/legislation/
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
