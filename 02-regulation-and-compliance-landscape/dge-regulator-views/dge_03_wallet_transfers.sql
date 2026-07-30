-- =============================================================================
-- REGULATORY REQUIREMENT: NJ DGE (Division of Gaming Enforcement)
-- Regulation:  N.J.A.C. 13:69O-1.4(c) — Internet Gaming System Standards
-- Section:     §1.4(c)3 — Wallet/funds transfer logging; §1.4(e) — Regulator access
-- Purpose:     Complete audit trail of all patron fund movements between wallet and
--              games — used by DGE to reconcile GGR and investigate disputes
-- Retention:   10 years minimum (N.J.A.C. 13:69O-1.4(l))
-- Audit Access: Continuous read-only DGE access required
-- Penalty:     License suspension or revocation (N.J.S.A. 5:12-130)
-- Last Verified: March 2026
--
-- Also applicable to:
--   PA PGCB   — 58 Pa. Code §436a.5 (financial transaction logs, 10 years)
--   MI MGCB   — MGCB-RAG-21-01 §4.6 (wallet transaction reporting)
--   Ontario AGCO — Standard 3.3 (financial transaction records)
--   MGA       — Gaming Compliance Directive §7 (transaction log completeness)
--   UKGC      — LCCP SR Code 3.4.1 + RTS 8.1 (transaction records)
--   Sweden    — Spelinspektionen regs §4 kap. (transaction audit trail)
--   Brazil    — Portaria SPA/MF No. 722/2024 (wallet file — daily SIGAP batch,
--              ~600,000 wallet files/day per SERPRO, March 2026)
--   EU 5AMLD/6AMLD — Note: 6AMLD entered into force 9 July 2024, applies from
--              10 July 2027; 5AMLD (EU 2018/843) and 4AMLD (EU 2015/849) still
--              operative; wallet transfer logs are a key AML audit record
--
-- References:
--   N.J.A.C. 13:69O: https://www.njleg.state.nj.us/TitleSearch?TitleNum=13&ChapterNum=69O
--   NJ DGE Technical Standards: https://www.nj.gov/oag/ge/docs/TechStds/InternetGaming/
--   N.J.S.A. 5:12 (Casino Control Act): https://www.njleg.state.nj.us/TitleSearch?TitleNum=5&ChapterNum=12
--   UKGC LCCP: https://www.gamblingcommission.gov.uk/licensees-and-businesses/lccp
--   UKGC AML Guidance: https://www.gamblingcommission.gov.uk/guidance/anti-money-laundering
--   MGA Gaming Act: https://www.mga.org.mt/legislation/gaming-act/
--   Spellagen (2018:1138): https://www.riksdagen.se/sv/dokument-och-lagar/dokument/svensk-forfattningssamling/spellag-20181138_sfs-2018-1138/
--   5AMLD: https://eur-lex.europa.eu/legal-content/EN/TXT/?uri=CELEX:32018L0843
--   6AMLD: https://eur-lex.europa.eu/legal-content/EN/TXT/?uri=CELEX:32018L1673
--   Portaria SPA/MF 722/2024: https://www.in.gov.br/web/dou/-/portaria-spa/mf-n-722
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
