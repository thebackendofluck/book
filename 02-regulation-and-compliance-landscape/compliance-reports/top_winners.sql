-- =============================================================================
-- REGULATORY REQUIREMENT: AML / Source of Funds / Responsible Gaming
-- Regulation:  UKGC LCCP SR Code 12.1.1 — financial crime prevention;
--              UKGC LCCP Ordinary Code 2.1.1 — customer interaction triggers;
--              UK MLR 2017 Reg. 33 — enhanced due diligence triggers;
--              MGA AML/CFT Implementing Procedures Part I §10 — EDD thresholds;
--              FATF Recommendation 10 — Customer Due Diligence;
--              EU 5AMLD (2018/843) / 4AMLD (2015/849) — EDD for high-risk customers;
--              NJ N.J.A.C. 13:69O BSA CIP — suspicious activity triggers
-- Purpose:     Identifies top-winning players by net cash for AML Source of Funds
--              (SoF) and Source of Wealth (SoW) investigations. A player winning
--              consistently large sums requires enhanced due diligence to verify
--              that funds are not proceeds of crime and winnings are legitimate.
--              Also used for responsible gaming risk profiling (UKGC LCCP).
-- Thresholds:  UKGC 2026: customer interaction triggers reviewed — prior guidance
--              referenced £125/£500 thresholds; UKGC Feb 2025 LCCP changes focus
--              on deposit limit prompts and financial limit reviews (effective
--              31 Oct 2025). SoF checks are now risk-based rather than fixed-
--              threshold; use this report to identify high-risk players.
--              MGA: EDD required for players with net losses/wins above operator-
--              defined risk thresholds (typically €2,000–€5,000/month).
-- Frequency:   Run quarterly minimum; monthly for high-volume operations
-- Retention:   5 years (MLR 2017 Reg. 40; 5AMLD Art. 40)
-- Penalty:     UKGC: regulatory action, licence review, fines;
--              UK MLR 2017: criminal prosecution for failure to apply EDD;
--              GDPR / UK GDPR: Art. 83 penalties if PII mishandled in reports
-- Note:        Query currently hard-coded to jurisdiction_id = 'ukgc' and a fixed
--              date range. Parameterise before production use. The LIMIT 100 caps
--              the result — ensure all high-risk players above your SoF threshold
--              are captured (the top 100 by net loss is a reasonable starting point).
-- Last Verified: March 2026
--
-- Applicable jurisdictions:
--   UKGC      — LCCP SR Code 12.1.1 (primary); Feb 2025 LCCP changes apply from
--              31 Oct 2025 (deposit limit prompts + fund protection disclosure)
--   MGA       — AML/CFT Implementing Procedures Part I §10
--   Sweden    — Lag (2017:630) §4 kap. (AML EDD requirements)
--   Netherlands KSA — Wwft (AML Act) §8 (EDD for high-risk customers)
--   Ontario AGCO — FINTRAC PCMLTFA (Canadian AML threshold reporting)
--   NJ DGE    — BSA SAR filing triggers (31 CFR §1021.320)
--   MGA/EU    — Note: 6AMLD enters into force 10 July 2027 — EDD obligations
--              for gambling sector will be tightened further; plan now.
--
-- References:
--   UKGC LCCP: https://www.gamblingcommission.gov.uk/licensees-and-businesses/lccp
--   UKGC AML Guidance: https://www.gamblingcommission.gov.uk/guidance/anti-money-laundering
--   N.J.A.C. 13:69O: https://www.njleg.state.nj.us/TitleSearch?TitleNum=13&ChapterNum=69O
--   MGA Player Protection Directive: https://www.mga.org.mt/legislation/subsidiary-legislation/
--   Spellagen (2018:1138): https://www.riksdagen.se/sv/dokument-och-lagar/dokument/svensk-forfattningssamling/spellag-20181138_sfs-2018-1138/
--   KSA (Kansspelautoriteit): https://kansspelautoriteit.nl/
--   AGCO iGO Standards: https://www.agco.ca/internet-gaming/standards-and-resources
--   5AMLD: https://eur-lex.europa.eu/legal-content/EN/TXT/?uri=CELEX:32018L0843
--   6AMLD: https://eur-lex.europa.eu/legal-content/EN/TXT/?uri=CELEX:32018L1673
--   FATF Recommendations: https://www.fatf-gafi.org/en/recommendations.html
--   GDPR Art. 83 (Penalties): https://gdpr-info.eu/art-83-gdpr/
-- =============================================================================
-- Compliance Reports: Top Winners by Jurisdiction
-- Identifies the most profitable players within a regulatory jurisdiction
-- for a given reporting period. Required for AML compliance and
-- responsible gaming oversight.
--
-- The net_cash formula captures true player profitability:
--   Net Cash = (Cash Stakes - Cash Returns/Refunds)
--              - Bonus Conversions
--              - (Credit Adjustments - Debit Adjustments)
--
-- Usage: Run quarterly (or as required by the regulator).
-- Parameters: date range and jurisdiction_id (e.g. 'ukgc', 'mga').

WITH data AS (
    SELECT
        u.global_id                                 AS globalId,
        COUNT(DISTINCT(u.affiliateid))              AS brandCount,
        (SUM(s.cash_stake) - SUM((s.cash_return + s.cash_refund)))
            - SUM(s.bonus_to_cash_account)
            - (SUM(s.cash_adj_cr) - SUM(s.cash_adj_dr)) AS net_cash
    FROM casino_core.users u
    JOIN analytics_dw.daily_player_stats s
        ON u.id = s.user_id
        AND s.on_date BETWEEN
            TO_TIMESTAMP('01-09-2025 00:00:00.000000', 'dd-mm-yyyy hh24:mi:ss.US')
            AND TO_TIMESTAMP('30-11-2025 23:59:59.999999', 'dd-mm-yyyy hh24:mi:ss.US')
    LEFT OUTER JOIN analytics_dw.daily_player_revenue r
        ON r.user_id = s.user_id AND r.on_date = s.on_date
    JOIN casino_core.user_info ui
        ON ui.userid = u.id AND ui.testaccount = false
    INNER JOIN casino_core.countries c
        ON ui.country = c.country AND c.jurisdiction_id = 'ukgc'
    GROUP BY u.global_id
    ORDER BY net_cash
    LIMIT 100
)
SELECT
    d.*,
    (SUM(dps.cash_stake) - SUM((dps.cash_return + dps.cash_refund)))
        - SUM(dps.bonus_to_cash_account)
        - (SUM(dps.cash_adj_cr) - SUM(dps.cash_adj_dr))    AS local_net_cash,
    u.id                                            AS userid,
    b.name                                          AS brand,
    u.name                                          AS username,
    ui.created                                      AS registration_date
FROM data d
LEFT JOIN casino_core.users u ON d.globalId = u.global_id
JOIN casino_core.user_info ui ON ui.userid = u.id
JOIN casino_core.brands b     ON b.id = u.affiliateid
JOIN analytics_dw.daily_player_stats dps
    ON u.id = dps.user_id
    AND dps.on_date BETWEEN
        TO_TIMESTAMP('01-09-2025 00:00:00.000000', 'dd-mm-yyyy hh24:mi:ss.US')
        AND TO_TIMESTAMP('30-11-2025 23:59:59.999999', 'dd-mm-yyyy hh24:mi:ss.US')
LEFT OUTER JOIN analytics_dw.daily_player_revenue r
    ON r.user_id = dps.user_id AND r.on_date = dps.on_date
GROUP BY u.id, b.name, u.name, ui.created, d.net_cash, d.brandCount, d.globalId
ORDER BY d.net_cash ASC, d.globalId;
