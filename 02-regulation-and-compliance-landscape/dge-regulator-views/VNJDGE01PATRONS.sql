-- =============================================================================
-- FILE NOTE: This UPPERCASE file (VNJDGE01PATRONS.sql) uses Oracle naming conventions
-- (ALL-CAPS, VNJDGE* prefix) but contains PostgreSQL-compatible SQL.
-- Both this file and its lowercase counterpart are functionally identical.
-- The UPPERCASE naming is a legacy convention from the platform's Oracle origins.
-- See the lowercase counterpart in this directory for the full regulatory header.
-- NOTE: These views are PostgreSQL only; Oracle would require different syntax
-- (e.g., no DISTINCT ON, different timezone functions, no CREATE OR REPLACE VIEW).
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
