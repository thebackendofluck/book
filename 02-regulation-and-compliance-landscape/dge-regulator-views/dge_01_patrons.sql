-- =============================================================================
-- REGULATORY REQUIREMENT: NJ DGE (Division of Gaming Enforcement)
-- Regulation:  N.J.A.C. 13:69O-1.4 — Internet or Mobile Gaming System Standards
--              and Operational Controls
-- Section:     §1.4(a)1 — Patron account data; §1.4(e) — Regulator data access
-- Purpose:     Real-time audit view for DGE inspectors — patron registration,
--              demographics, exclusion status, and account balances per gaming date
-- Retention:   10 years minimum (N.J.S.A. 5:12-76; N.J.A.C. 13:69O-1.4(l))
-- Audit Access: DGE must have read-only access to this view at all times via the
--              dge_readonly_external role (see GRANT at end of file)
-- Penalty:     License suspension or revocation for failure to provide access
--              (N.J.A.C. 13:69C-8.1; N.J.S.A. 5:12-130)
-- Last Verified: March 2026 — N.J.A.C. 13:69O has not been substantively amended
--              since 2023; PRN 2025-130 adds RG Lead requirement but does not
--              change patron data view structure.
--
-- Also applicable to:
--   PA PGCB   — 58 Pa. Code §§ 436a.1-7 (patron account records, 10-year retention)
--   MI MGCB   — MGCB-RAG-21-01 §4.1 (patron registration data, regulator access)
--   Ontario AGCO — Registrar's Standards for Internet Gaming, Standard 3.1
--                  (iGO Act 2024 came into force May 2025; iGO is now independent Crown agency)
--   MGA       — Player Account Management Directive §3 (patron data completeness)
--   UKGC      — LCCP SR Code 3.4.1 (customer account records)
--   Sweden    — Spellagen (SFS 2018:1138) §6 kap. 5 § (player record-keeping)
--   Brazil    — Portaria SPA/MF No. 722/2024 Art. 10 (player registration in SIGAP)
--
-- References:
--   N.J.A.C. 13:69O: https://www.njleg.state.nj.us/TitleSearch?TitleNum=13&ChapterNum=69O
--   NJ DGE Technical Standards: https://www.nj.gov/oag/ge/docs/TechStds/InternetGaming/
--   N.J.S.A. 5:12 (Casino Control Act): https://www.njleg.state.nj.us/TitleSearch?TitleNum=5&ChapterNum=12
--   NJ PRN 2025-130 (RG Lead): https://www.nj.gov/oag/ge/docs/PRN/PRN2025-130.pdf
--   UKGC LCCP: https://www.gamblingcommission.gov.uk/licensees-and-businesses/lccp
--   MGA Player Protection Directive: https://www.mga.org.mt/legislation/subsidiary-legislation/
--   Spellagen (2018:1138): https://www.riksdagen.se/sv/dokument-och-lagar/dokument/svensk-forfattningssamling/spellag-20181138_sfs-2018-1138/
--   AGCO iGO Standards: https://www.agco.ca/internet-gaming/standards-and-resources
--   Portaria SPA/MF 722/2024: https://www.in.gov.br/web/dou/-/portaria-spa/mf-n-722
-- =============================================================================
-- NJ DGE View 01: PATRONS
-- Contains a unique list of patrons with their last-updated summary
-- information: demographics, balances, exclusions, and account status.
--
-- Driven by two event sources:
--   1. Balance changes (from analytics_dw.daily_player_balances)
--   2. Lock status changes (from casino_replica.user_lock_audit)
-- This ensures a row appears only when something actually changed for that gaming date.
--
-- Key design choices:
--   - patron_gaming_date is in Eastern Time (NJ's local timezone)
--   - Amounts are stored in cents; divided by 100 for display
--   - exclusion_flag and account_status are derived from active locks at day-end
--   - Lock lifecycle (started/maybe_finished) uses AT TIME ZONE 'America/New_York'
--     to align with the Eastern gaming date boundary
-- Supported by indexes: idx_dpb_to_date, idx_dpb_userid_from_to_date,
--                        idx_ula_lock_id, idx_ula_timestamp

CREATE OR REPLACE VIEW dge."vNJDGE01PATRONS" AS
SELECT
    'ACMETOCASINO'                                AS skin_name,
    c.user_id::varchar(100)                     AS patron_account_id,
    to_char(patron_gaming_date, 'YYYYMMDD')     AS patron_gaming_date,
    upper(ui.uniform_patron_identifier)         AS dupi,
    to_char(ui.created, 'YYYYMMDD HH24MISS.MS OF:00')
                                                AS signuptime_system,
    concat(
        to_char(ui.created AT TIME ZONE 'UTC' AT TIME ZONE 'America/New_York', 'YYYYMMDD HH24MISS.MS '),
        to_char(ui.created AT TIME ZONE 'America/New_York' - ui.created AT TIME ZONE 'UTC', '-HH24:MI')
    )                                           AS signuptime_eastern,
    u.name                                      AS patron_user_name,
    to_char(ui.dob, 'YYYYMMDD')                 AS dob,
    CASE
        WHEN upper(ui.gender) = 'M' THEN 'M'
        WHEN upper(ui.gender) = 'F' THEN 'F'
        WHEN ui.gender IS NOT NULL  THEN 'O'
        ELSE NULL
    END                                         AS gender,
    right(ui.registration_jurisdiction_id, 2)   AS registration_us_state,
    ui.postalcode                               AS registration_us_zipcode,
    NULL                                        AS registration_non_us_state,
    'USA'                                       AS registration_country,
    coalesce(cb.cash_balance, 0) / 100          AS wallet_cash_balance,
    coalesce(cb.bonus_balance, 0) / 100         AS wallet_non_cash_balance,
    CASE WHEN array_length(array_agg(lock_type_id), 1) = 0
              OR bool_and(lock_type_id IS NULL)
        THEN 'N'
        ELSE 'Y'
    END                                         AS exclusion_flag,
    min(translate(lock_type_id, '_', ''))        AS exclusion_description,
    CASE WHEN ui.testaccount IS TRUE THEN 'TEST' ELSE 'REAL' END
                                                AS account_type,
    CASE
        WHEN array_length(array_agg(lock_type_id), 1) = 0
             OR bool_and(lock_type_id IS NULL)
        THEN 'OPEN'
        WHEN array_agg(lock_type_id::text) && array[
            'AWAITING_KYC', 'MATCHED_AWAITING_KYC'
        ] THEN 'PENDING'
        WHEN array_agg(lock_type_id::text) && array[
            'DUPLICATE', 'BANNED_FRAUD',
            'REG_BLOCKED_BANNED_FRAUD', 'MATCHED_BANNED_FRAUD'
        ] THEN 'CLOSED'
        WHEN array_agg(lock_type_id::text) && array[
            'SELF_EXCLUDE', 'REG_BLOCKED_SELF_EXCLUDE', 'MATCHED_SELF_EXCLUDE',
            'OPERATOR_SE', 'REG_BLOCKED_OPERATOR_SE', 'MATCHED_OPERATOR_SE',
            'TEMPORARY', 'REG_BLOCKED_TEMPORARY', 'MATCHED_TEMPORARY',
            'RG3', 'REG_BLOCKED_RG3', 'MATCHED_RG3'
        ] THEN 'SUSPENDED'
        WHEN array_agg(lock_type_id::text) && array[
            'DORMANT_USER'
        ] THEN 'DORMANT'
        ELSE 'UNKNOWN'
    END                                         AS account_status,
    patron_gaming_date                          AS patron_gaming_date_index
FROM
    (
        -- Source 1: Balance change events
        SELECT user_id, to_date AS patron_gaming_date
        FROM (
            SELECT
                user_id, to_date, cash_balance, bonus_balance,
                coalesce(lag(cash_balance)  OVER (PARTITION BY user_id ORDER BY to_date ASC), -1) AS old_cash_balance,
                coalesce(lag(bonus_balance) OVER (PARTITION BY user_id ORDER BY to_date ASC), -1) AS old_bonus_balance
            FROM analytics_dw.daily_player_balances
            WHERE to_date IS NOT NULL
        ) AS balances_with_previous_day_info
        WHERE (cash_balance != old_cash_balance OR bonus_balance != old_bonus_balance)

        UNION

        -- Source 2: Lock status change events
        SELECT
            l.user_id,
            date_trunc('day', a.timestamp AT TIME ZONE 'utc' AT TIME ZONE 'America/New_York') AS patron_gaming_date
        FROM casino_replica.user_lock_audit a
        JOIN casino_replica.user_lock l ON a.lock_id = l.id
        WHERE a.timestamp IS NOT NULL
        GROUP BY l.user_id, date_trunc('day', a.timestamp AT TIME ZONE 'utc' AT TIME ZONE 'America/New_York')
    ) c
    JOIN casino_core.users u                           ON c.user_id = u.id
    JOIN casino_replica.user_info ui      ON u.id = ui.userid
    LEFT OUTER JOIN analytics_dw.daily_player_balances cb
        ON cb.user_id = c.user_id
        AND cb.from_date <= c.patron_gaming_date
        AND (cb.to_date IS NULL OR cb.to_date >= c.patron_gaming_date)
    LEFT OUTER JOIN (
        -- Lock lifecycle: start/end times per lock
        SELECT
            lock.user_id, lock.id AS lock_id, lock.lock_type_id,
            bool_or(audit.activity IN ('cancel', 'End triggered', 'Reactivation completed', 'Reactivated'))
                AS is_completed,
            min(audit.timestamp) AS started,
            CASE
                WHEN bool_or(audit.activity IN ('cancel', 'End triggered', 'Reactivation completed', 'Reactivated'))
                THEN max(audit.timestamp)
                ELSE NULL
            END AS maybe_finished
        FROM casino_replica.user_lock lock
        JOIN casino_replica.user_lock_audit audit ON lock.id = audit.lock_id
        GROUP BY lock.user_id, lock.id, lock.lock_type_id
    ) lt
        ON lt.user_id = c.user_id
        AND started AT TIME ZONE 'America/New_York' <= c.patron_gaming_date + INTERVAL '1 day'
        AND (maybe_finished IS NULL
             OR maybe_finished AT TIME ZONE 'America/New_York' >= c.patron_gaming_date + INTERVAL '1 day')
GROUP BY
    c.user_id, c.patron_gaming_date, ui.uniform_patron_identifier, ui.created,
    u.name, ui.dob, ui.gender, ui.registration_jurisdiction_id, ui.postalcode,
    ui.testaccount, cb.cash_balance, cb.bonus_balance;

GRANT SELECT ON "dge"."vNJDGE01PATRONS" TO dge_readonly_external;
