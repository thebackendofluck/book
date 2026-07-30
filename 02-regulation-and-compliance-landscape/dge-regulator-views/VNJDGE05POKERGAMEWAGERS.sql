-- =============================================================================
-- FILE NOTE: This UPPERCASE file (VNJDGE05POKERGAMEWAGERS.sql) uses Oracle naming conventions
-- (ALL-CAPS, VNJDGE* prefix) but contains PostgreSQL-compatible SQL.
-- Both this file and its lowercase counterpart are functionally identical.
-- The UPPERCASE naming is a legacy convention from the platform's Oracle origins.
-- See the lowercase counterpart in this directory for the full regulatory header.
-- NOTE: These views are PostgreSQL only; Oracle would require different syntax
-- (e.g., no DISTINCT ON, different timezone functions, no CREATE OR REPLACE VIEW).
-- =============================================================================
-- NJ DGE View 05: POKER GAME WAGERS
-- Placeholder view for peer-to-peer poker game wager data.
-- Required by the DGE schema specification even when the operator
-- does not offer peer-to-peer poker. Returns zero rows (WHERE false).
--
-- This operator (AcmetoCasino) does not currently offer P2P poker.
-- If poker is added in future, this view must be replaced with a live
-- implementation sourcing from the poker game engine's hand history tables.
-- All columns are explicitly cast to preserve the expected output schema.

CREATE OR REPLACE VIEW dge."vNJDGE05POKERGAMEWAGERS" AS
SELECT
    NULL                    AS SKIN_NAME,
    NULL                    AS PATRON_ACCOUNT_ID,
    NULL                    AS SESSION_ID,
    NULL                    AS POKER_TABLE_ID,
    NULL                    AS POKER_HAND_ID,
    NULL                    AS POKER_TRANS_FAILURE_FLAG,
    NULL                    AS POKER_HAND_STARTTIME_SYSTEM,
    NULL                    AS POKER_HAND_ENDTIME_SYSTEM,
    NULL                    AS POKER_HAND_STARTTIME_EASTERN,
    NULL                    AS POKER_HAND_ENDTIME_EASTERN,
    NULL                    AS POKER_NAME,
    NULL                    AS POKER_VERSION,
    NULL                    AS RGS_NAME,
    CAST(NULL AS DECIMAL)   AS POKER_COIN_IN,
    CAST(NULL AS DECIMAL)   AS POKER_COIN_OUT,
    CAST(NULL AS DECIMAL)   AS POKER_WINLOSS,
    CAST(NULL AS DECIMAL)   AS POKER_RAKE_FEE,
    CAST(NULL AS DATE)      AS POKER_HAND_STARTTIME_EASTERN_INDEX
WHERE false;

GRANT SELECT ON "dge"."vNJDGE05POKERGAMEWAGERS" TO dge_readonly_external;
