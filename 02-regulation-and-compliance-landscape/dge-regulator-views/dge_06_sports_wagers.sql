-- =============================================================================
-- REGULATORY REQUIREMENT: NJ DGE (Division of Gaming Enforcement)
-- Regulation:  N.J.A.C. 13:69O-1.4(d) — Internet Gaming System Standards;
--              N.J.A.C. 13:69N (Internet Sports Wagering)
-- Section:     §1.4(d)3 — Sports wagering transaction logging
-- Purpose:     Complete audit trail for all Kambi OSP sports bets — used by DGE
--              to verify payout correctness, detect suspicious betting patterns,
--              and reconcile gross gaming revenue from sports operations
-- Retention:   10 years minimum (N.J.A.C. 13:69O-1.4(l);
--              N.J.A.C. 13:69N-1.7 for sports-specific records)
-- Audit Access: Continuous read-only DGE access required
-- Penalty:     License suspension or revocation (N.J.S.A. 5:12-130)
-- Note:        DISTINCT ON (combination_ref) ensures one row per betslip
--              (latest status). DGE sees the current/final state of each bet,
--              not intermediate status changes. If DGE requires full event history
--              per betslip, remove DISTINCT ON and expose the feed_messages table.
-- Last Verified: March 2026 — Kambi remains the OSP; view structure unchanged.
--
-- Also applicable to:
--   PA PGCB   — 58 Pa. Code §436a.6 (sports wager records, 10 years)
--   MI MGCB   — MGCB Online Sports Betting Technical Standards (2021)
--   Ontario AGCO — Standard 3.5 (sports event wagering records)
--   MGA       — B2C Sports Betting Compliance reqs (wager audit trail)
--   UKGC      — LCCP SR Code 3.4.1 (sports betting records 5 years)
--   Brazil    — Portaria SPA/MF No. 722/2024 (bet-level SIGAP reporting required
--              daily; ~500M records/day across all operators, March 2026)
--
-- References:
--   N.J.A.C. 13:69O: https://www.njleg.state.nj.us/TitleSearch?TitleNum=13&ChapterNum=69O
--   NJ DGE Technical Standards: https://www.nj.gov/oag/ge/docs/TechStds/InternetGaming/
--   N.J.S.A. 5:12 (Casino Control Act): https://www.njleg.state.nj.us/TitleSearch?TitleNum=5&ChapterNum=12
--   UKGC LCCP: https://www.gamblingcommission.gov.uk/licensees-and-businesses/lccp
--   MGA Gaming Act: https://www.mga.org.mt/legislation/gaming-act/
--   AGCO iGO Standards: https://www.agco.ca/internet-gaming/standards-and-resources
--   Portaria SPA/MF 722/2024: https://www.in.gov.br/web/dou/-/portaria-spa/mf-n-722
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
