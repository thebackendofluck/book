-- =============================================================================
-- REGULATORY REQUIREMENT: NJ DGE (Division of Gaming Enforcement) + Responsible Gaming
-- Regulation:  N.J.A.C. 13:69O-1.4(f) — Internet Gaming System Standards;
--              N.J.A.C. 13:69C-8.4 — Responsible Gaming requirements;
--              PRN 2025-130 (proposed N.J.A.C. 13:69O-1.2A) — Mandatory RG Lead
-- Section:     §1.4(f) — Player gaming limits; §8.4 — Deposit/loss/time limit logging
-- Purpose:     Audit trail of all patron-set gaming limits (deposit, spend, wager,
--              loss, and time). DGE uses this to verify responsible gaming tools are
--              functioning correctly and limits are honoured in real time.
-- Retention:   10 years minimum (N.J.A.C. 13:69O-1.4(l))
-- Audit Access: Continuous read-only DGE access required
-- Penalty:     License suspension or revocation (N.J.S.A. 5:12-130);
--              Failure to honour limits = immediate regulatory intervention
-- Update 2026: PRN 2025-130 (published NJ Register Vol.57 No.18) proposes a
--              three-tier player intervention system with mandatory RG Lead contact.
--              This view is foundational data for Phase 1 and Phase 2 triggers.
-- Last Verified: March 2026
--
-- Also applicable to:
--   PA PGCB   — 58 Pa. Code §436a.8 (responsible gaming limit records)
--   MI MGCB   — MGCB-RAG-21-01 §5 (mandatory limit tools)
--   Ontario AGCO — Standard 2.10/2.11 (updated June 2025: data-driven monitoring
--              and timely intervention for at-risk players now mandatory)
--   MGA       — Player Protection Directive 2.0 (Directive 2/2018 V3, Jan 2023):
--              deposit limits, loss limits, session limits all mandatory;
--              stricter limits effective immediately; relaxed limits: 24h delay
--   UKGC      — LCCP SR Code 3.5.3 + RTS 12: deposit limit prompt mandatory
--              before first deposit (effective 31 October 2025); reminder every
--              6 months; financial limit set before first deposit required
--   Sweden    — Spellagen §6 kap. 4 § (mandatory deposit/loss/time limits);
--              credit gambling ban from 1 April 2026 affects limit types
--   Netherlands KSA — Wet Koa (Koa Act) Art. 4.3: deposit limits mandatory;
--              CRUKS check required before setting limits
--   Brazil    — Portaria SPA/MF No. 722/2024 (responsible gaming tools required;
--              SIGAP National Register of Prohibited Persons must be checked
--              before accepting any wager or deposit, from 2026)
--
-- References:
--   N.J.A.C. 13:69O: https://www.njleg.state.nj.us/TitleSearch?TitleNum=13&ChapterNum=69O
--   NJ DGE Technical Standards: https://www.nj.gov/oag/ge/docs/TechStds/InternetGaming/
--   N.J.S.A. 5:12 (Casino Control Act): https://www.njleg.state.nj.us/TitleSearch?TitleNum=5&ChapterNum=12
--   NJ PRN 2025-130 (RG Lead): https://www.nj.gov/oag/ge/docs/PRN/PRN2025-130.pdf
--   UKGC LCCP: https://www.gamblingcommission.gov.uk/licensees-and-businesses/lccp
--   UKGC Remote Technical Standards: https://www.gamblingcommission.gov.uk/standards/remote-technical-standards
--   MGA Player Protection Directive: https://www.mga.org.mt/legislation/subsidiary-legislation/
--   Spellagen (2018:1138): https://www.riksdagen.se/sv/dokument-och-lagar/dokument/svensk-forfattningssamling/spellag-20181138_sfs-2018-1138/
--   KSA (Kansspelautoriteit): https://kansspelautoriteit.nl/
--   CRUKS: https://kansspelautoriteit.nl/cruks/
--   AGCO iGO Standards: https://www.agco.ca/internet-gaming/standards-and-resources
--   Portaria SPA/MF 722/2024: https://www.in.gov.br/web/dou/-/portaria-spa/mf-n-722
-- =============================================================================
-- NJ DGE View 08: PATRON GAMING LIMITS
-- Complete log of gaming limit changes: deposit, time, spend, wager, and loss limits.
-- Uses window functions (LEAD) to calculate the effective period of each limit setting.
-- Converts internal time units (seconds, minutes) to the DGE-required days format.
--
-- Source: casino_core.user_limit_change (filtered to status = 'APPLIED' only)
-- CTE pattern: applied_user_limits calculates start/end time for each active limit
--   using LEAD() over (user_id, limit_type_id) ordered by effective start time.
--   End time defaults to '99991231' (far future) for the currently active limit.
--
-- Internal time unit conversions:
--   - Login/session time limits: stored in seconds -> / 86400 for days
--   - Session duration limits: stored in minutes -> / 1440 for days
--   - Monetary limits: stored in currency units, used as-is
--
-- new_value format: '<period>:<amount>' (e.g. 'daily:50000', 'weekly:3600')
--   split_part(new_value, ':', 1) extracts the period (daily/weekly/monthly)
--   split_part(new_value, ':', 2) extracts the raw numeric amount
-- Supported by indexes: idx_ulc_requested, idx_ulc_status

CREATE OR REPLACE VIEW dge."vNJDGE08PATRONSGAMELIMS" AS
WITH applied_user_limits AS (
    SELECT
        user_id,
        requested,
        coalesce(cooloff_end_time, requested) AS start_time,
        coalesce(
            lead(coalesce(cooloff_end_time, requested))
                OVER (PARTITION BY user_id, limit_type_id ORDER BY coalesce(cooloff_end_time, requested) ASC),
            '99991231 000000.00 +00:00'
        ) AS end_time,
        limit_type_id,
        new_value
    FROM casino_core.user_limit_change
    WHERE status = 'APPLIED'
)
SELECT
    'ACMETOCASINO'            AS skin_name,
    user_id::varchar(100)   AS patron_account_id,
    to_char(requested AT TIME ZONE 'utc', 'YYYYMMDD HH24MISS.MS OF:00')
                            AS limit_transacttime_system,
    concat(
        to_char(requested AT TIME ZONE 'UTC' AT TIME ZONE 'America/New_York', 'YYYYMMDD HH24MISS.MS '),
        to_char(requested AT TIME ZONE 'America/New_York' - requested AT TIME ZONE 'UTC', '-HH24:MI')
    )                       AS limit_transacttime_eastern,
    to_char(requested AT TIME ZONE 'utc', 'YYYYMMDD HH24MISS.MS OF:00')
                            AS limit_transact_stime_system,
    concat(
        to_char(start_time AT TIME ZONE 'UTC' AT TIME ZONE 'America/New_York', 'YYYYMMDD HH24MISS.MS '),
        to_char(start_time AT TIME ZONE 'America/New_York' - start_time AT TIME ZONE 'UTC', '-HH24:MI')
    )                       AS limit_transact_stime_eastern,
    to_char(end_time AT TIME ZONE 'utc', 'YYYYMMDD HH24MISS.MS OF:00')
                            AS limit_transact_etime_system,
    concat(
        to_char(end_time AT TIME ZONE 'UTC' AT TIME ZONE 'America/New_York', 'YYYYMMDD HH24MISS.MS '),
        to_char(end_time AT TIME ZONE 'America/New_York' - end_time AT TIME ZONE 'UTC', '-HH24:MI')
    )                       AS limit_transact_etime_eastern,
    CASE
        WHEN limit_type_id IN ('DAILYDEPOSIT', 'WEEKLYDEPOSIT', 'MONTHLYDEPOSIT', 'DEPOSIT', 'HARDDEPOSIT') THEN 'DEPOSIT'
        WHEN limit_type_id IN ('DAILYLOGGEDIN', 'WEEKLYLOGGEDIN', 'MONTHLYLOGGEDIN', 'SESSIONDURATION', 'RCPERIOD') THEN 'TIME'
        WHEN limit_type_id IN ('DAILYWAGER', 'WEEKLYWAGER', 'MONTHLYWAGER') THEN 'SPEND'
        WHEN limit_type_id IN ('SINGLEWAGER') THEN 'WAGER'
        WHEN limit_type_id IN ('LOSS') THEN 'LOSS'
        WHEN limit_type_id IN ('WITHDRAWAL', 'WITHDRAWALREVERSAL', 'TWILIGHTDEPOSIT') THEN 'UNKNOWN'
        ELSE 'UNKNOWN'
    END                     AS limit_type,
    CASE WHEN split_part(new_value, ':', 1) IN ('daily', 'weekly', 'monthly')
        THEN upper(split_part(new_value, ':', 1))
        ELSE NULL
    END                     AS limit_period,
    CASE
        -- Time-based limits stored in seconds -> convert to days
        WHEN limit_type_id IN ('DAILYLOGGEDIN', 'WEEKLYLOGGEDIN', 'MONTHLYLOGGEDIN', 'RCPERIOD')
             AND split_part(new_value, ':', 2) != ''
        THEN (split_part(new_value, ':', 2)::numeric / 86400)::numeric(24, 2)
        -- Session duration stored in minutes -> convert to days
        WHEN limit_type_id = 'SESSIONDURATION'
             AND split_part(new_value, ':', 2) != ''
        THEN (split_part(new_value, ':', 2)::numeric / 1440)::numeric(24, 2)
        -- Monetary limits: use value directly
        WHEN split_part(new_value, ':', 2) != ''
        THEN split_part(new_value, ':', 2)::numeric(24, 2)
        ELSE 0.00
    END                     AS limit_amount,
    requested AT TIME ZONE 'UTC' AT TIME ZONE 'America/New_York'
                            AS limit_transacttime_eastern_index
FROM applied_user_limits;

GRANT SELECT ON "dge"."vNJDGE08PATRONSGAMELIMS" TO dge_readonly_external;
