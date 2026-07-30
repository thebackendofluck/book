-- =============================================================================
-- REGULATORY REQUIREMENT: NJ DGE (Division of Gaming Enforcement)
-- Regulation:  N.J.A.C. 13:69O-1.4(d) — Internet Gaming System Standards
-- Section:     §1.4(d)2 — Peer-to-peer game (poker) transaction logging
-- Purpose:     Peer-to-peer poker game wager audit view — REQUIRED by DGE schema
--              specification even when the operator does not offer poker.
--              This placeholder (WHERE false) satisfies the structural requirement.
-- Retention:   10 years minimum when live (N.J.A.C. 13:69O-1.4(l))
-- Audit Access: Continuous read-only DGE access required (even empty views)
-- Penalty:     License suspension or revocation (N.J.S.A. 5:12-130)
-- ACTION REQUIRED: If poker is enabled in future, replace WHERE false with a live
--              query sourcing from the poker engine's hand history tables. All NULL
--              columns must be populated with real data.
-- Last Verified: March 2026 — AcmetoCasino does not offer P2P poker; view remains
--              a placeholder. Verified with DGE compliance officer Q1 2026.
--
-- Also applicable to (when poker is live):
--   PA PGCB   — 58 Pa. Code §436a.6 (game round records including poker)
--   MI MGCB   — MGCB-RAG-21-01 §4.7
--   Ontario AGCO — Standard 3.4
--
-- References:
--   N.J.A.C. 13:69O: https://www.njleg.state.nj.us/TitleSearch?TitleNum=13&ChapterNum=69O
--   NJ DGE Technical Standards: https://www.nj.gov/oag/ge/docs/TechStds/InternetGaming/
--   N.J.S.A. 5:12 (Casino Control Act): https://www.njleg.state.nj.us/TitleSearch?TitleNum=5&ChapterNum=12
--   AGCO iGO Standards: https://www.agco.ca/internet-gaming/standards-and-resources
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
