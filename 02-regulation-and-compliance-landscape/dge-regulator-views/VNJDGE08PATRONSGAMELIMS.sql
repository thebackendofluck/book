-- =============================================================================
-- FILE NOTE: This UPPERCASE file (VNJDGE08PATRONSGAMELIMS.sql) uses Oracle naming conventions
-- (ALL-CAPS, VNJDGE* prefix) but contains PostgreSQL-compatible SQL.
-- Both this file and its lowercase counterpart are functionally identical.
-- The UPPERCASE naming is a legacy convention from the platform's Oracle origins.
-- See the lowercase counterpart in this directory for the full regulatory header.
-- NOTE: These views are PostgreSQL only; Oracle would require different syntax
-- (e.g., no DISTINCT ON, different timezone functions, no CREATE OR REPLACE VIEW).
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
