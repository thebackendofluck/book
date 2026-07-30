-- =============================================================================
-- REGULATORY REQUIREMENT: Multi-jurisdiction — Dormant Account Obligations
-- Regulation:  UKGC LCCP SR Code 3.8.1 — dormant account policy mandatory;
--              MGA Player Account Management Directive Art. 11 — inactive accounts;
--              NJ N.J.A.C. 13:69O-1.3(d) — patron account inactivity procedures;
--              Sweden Spellagen §8 kap. 3 § — dormant balance handling;
--              PA PGCB 58 Pa. Code §436a.9 — dormant account procedures
-- Purpose:     Identifies players inactive for 6 months and 18 months who still
--              hold positive real-money balances. Required for:
--                (1) Mandatory outreach to dormant players (all major jurisdictions)
--                (2) Balance escheating to state/regulator after extended inactivity
--                    (e.g., NJ: 3 years inactive → unclaimed property filing)
--                (3) UKGC: accounts dormant >2 years must be flagged; balances
--                    >£10 must be attempted to be returned to player
--              Run monthly minimum; jurisdiction-specific timelines apply.
-- Retention:   7-10 years depending on jurisdiction (dormancy audit trail)
-- Penalty:     UKGC: regulatory action for failure to maintain dormant account
--              policy; MGA: directive violation; NJ: CCA §76 unclaimed property law
-- Note:        6-month threshold triggers enhanced monitoring;
--              18-month threshold triggers mandatory contact attempt procedures in
--              most regulated markets. Adjust thresholds per jurisdiction.
-- Last Verified: March 2026
--
-- Applicable jurisdictions:
--   UKGC      — LCCP SR Code 3.8.1 (dormant account policy; ≥2 year threshold)
--   MGA       — Player Account Mgmt Directive §11 (inactive account handling)
--   NJ DGE    — N.J.A.C. 13:69O-1.3(d) (patron inactivity; state escheat law)
--   PA PGCB   — 58 Pa. Code §436a.9
--   MI MGCB   — MGCB-RAG-21-01 §3.8
--   Ontario AGCO — iGaming Standards (dormant account policy required)
--   Sweden    — Spellagen §8 kap. 3 § (inactive balance handling)
--   Brazil    — Portaria SPA/MF No. 722/2024 (player account status reporting)
--
-- References:
--   UKGC LCCP: https://www.gamblingcommission.gov.uk/licensees-and-businesses/lccp
--   UKGC Remote Technical Standards: https://www.gamblingcommission.gov.uk/standards/remote-technical-standards
--   MGA Player Protection Directive: https://www.mga.org.mt/legislation/subsidiary-legislation/
--   MGA Gaming Act: https://www.mga.org.mt/legislation/gaming-act/
--   N.J.A.C. 13:69O: https://www.njleg.state.nj.us/TitleSearch?TitleNum=13&ChapterNum=69O
--   N.J.S.A. 5:12 (Casino Control Act): https://www.njleg.state.nj.us/TitleSearch?TitleNum=5&ChapterNum=12
--   Spellagen (2018:1138): https://www.riksdagen.se/sv/dokument-och-lagar/dokument/svensk-forfattningssamling/spellag-20181138_sfs-2018-1138/
--   Spelinspektionen: https://www.spelinspektionen.se/en/
--   AGCO iGO Standards: https://www.agco.ca/internet-gaming/standards-and-resources
--   Portaria SPA/MF 722/2024: https://www.in.gov.br/web/dou/-/portaria-spa/mf-n-722
-- =============================================================================
-- Compliance Reports: Dormant Accounts with Balance
-- Identifies players who have not been active for specified periods
-- but still maintain positive account balances.
-- Required by UKGC and most regulated jurisdictions.
--
-- Usage: Run monthly to identify 6-month and 18-month dormant accounts.
-- The operator must then follow jurisdiction-specific procedures for
-- contacting dormant account holders and (eventually) escheating funds.

-- 6-month dormant accounts
WITH active_players AS (
    SELECT dps.user_id AS id
    FROM analytics_dw.daily_player_stats dps
    WHERE on_date BETWEEN NOW() - INTERVAL '6 MONTHS' AND NOW()
)
SELECT
    COUNT(ua.userid)        AS no_accounts,
    SUM(ua.balance) / 100   AS balance,
    ua.currency,
    u.global_id,
    MAX(ui.lastlogin)       AS lastlogin
FROM casino_core.user_accounts ua
JOIN casino_core.users u        ON ua.userid = u.id
JOIN casino_core.user_info ui   ON ui.userid = u.id
JOIN casino_core.countries c    ON ui.country = c.country
WHERE ua.userid NOT IN (SELECT id FROM active_players)
  AND ua.typeid = 1            -- real money accounts only
  AND ua.balance > 0           -- must have positive balance
  AND ui.testaccount = false   -- exclude test/QA accounts
GROUP BY u.global_id, ua.currency
ORDER BY MAX(ui.lastlogin) DESC, u.global_id;


-- 18-month dormant accounts
-- Note: In many jurisdictions, 18 months triggers enhanced obligations
-- such as mandatory player contact attempts and potential fund escheating.
WITH active_players AS (
    SELECT dps.user_id AS id
    FROM analytics_dw.daily_player_stats dps
    WHERE on_date BETWEEN NOW() - INTERVAL '18 MONTHS' AND NOW()
)
SELECT
    COUNT(ua.userid)        AS no_accounts,
    SUM(ua.balance) / 100   AS balance,
    ua.currency,
    u.global_id,
    MAX(ui.lastlogin)       AS lastlogin
FROM casino_core.user_accounts ua
JOIN casino_core.users u        ON ua.userid = u.id
JOIN casino_core.user_info ui   ON ui.userid = u.id
JOIN casino_core.countries c    ON ui.country = c.country
WHERE ua.userid NOT IN (SELECT id FROM active_players)
  AND ua.typeid = 1
  AND ua.balance > 0
  AND ui.testaccount = false
GROUP BY u.global_id, ua.currency
ORDER BY MAX(ui.lastlogin) DESC, u.global_id;
