/* Commercial performance report -- daily/weekly/monthly/quarterly/yearly comparisons
 *
 * TESTABLE WRAPPER: stub schemas, tables, and sample data are created below
 * so this script runs stand-alone against any PostgreSQL 16+ instance.
 *
 * Production tables referenced:
 *   PLATFORM_STATS.DAILY_PLAYER_GAME_STATS  — per-player per-game daily betting aggregates
 *   GAMEGATEWAY.monthly_currencies          — FX rates (currency/year/month → EUR rate)
 *   GAMEGATEWAY.user_DEPOSITINFO            — player deposit eligibility flag
 *   GAMEGATEWAY.USERS                       — player account with AFFILIATEID
 *   GAMEGATEWAY.BRANDS                      — brand master (id/name)
 *   GAMEGATEWAY.user_info                   — player country + testaccount flag
 *   analytics.licensees                     — brand → licensee mapping
 */

-- ============================================================
-- STUB SCHEMAS AND TABLES
-- ============================================================

CREATE SCHEMA IF NOT EXISTS analytics_dw;
CREATE SCHEMA IF NOT EXISTS casino_core;
CREATE SCHEMA IF NOT EXISTS analytics;

CREATE TABLE IF NOT EXISTS analytics_dw.daily_player_game_stats (
    user_id        BIGINT       NOT NULL,
    on_date        TIMESTAMPTZ  NOT NULL,
    currency       CHAR(3)      NOT NULL,
    cash_stake     DECIMAL(14,2) NOT NULL DEFAULT 0,
    bonus_stake    DECIMAL(14,2) NOT NULL DEFAULT 0,
    cash_refunds   DECIMAL(14,2) NOT NULL DEFAULT 0,
    bonus_refunds  DECIMAL(14,2) NOT NULL DEFAULT 0,
    cash_return    DECIMAL(14,2) NOT NULL DEFAULT 0,
    bonus_return   DECIMAL(14,2) NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS casino_core.monthly_currencies (
    currency  CHAR(3)  NOT NULL,
    year      INT      NOT NULL,
    month     INT      NOT NULL,
    rate      DECIMAL(12,6) NOT NULL DEFAULT 1.0,
    PRIMARY KEY (currency, year, month)
);

CREATE TABLE IF NOT EXISTS casino_core.user_depositinfo (
    userid BIGINT PRIMARY KEY
);

CREATE TABLE IF NOT EXISTS casino_core.users (
    id          BIGINT PRIMARY KEY,
    affiliateid BIGINT NOT NULL
);

CREATE TABLE IF NOT EXISTS casino_core.brands (
    id   BIGINT PRIMARY KEY,
    name VARCHAR(100) NOT NULL
);

CREATE TABLE IF NOT EXISTS casino_core.user_info (
    userid      BIGINT PRIMARY KEY,
    country     CHAR(2),
    testaccount BOOLEAN DEFAULT FALSE
);

CREATE TABLE IF NOT EXISTS analytics.licensees (
    brand     VARCHAR(100) PRIMARY KEY,
    licensees VARCHAR(200)
);

-- ============================================================
-- SAMPLE DATA
-- ============================================================

-- Truncate stubs for idempotent re-runs (CASCADE handles FK ordering)
TRUNCATE analytics_dw.daily_player_game_stats,
         casino_core.monthly_currencies,
         casino_core.user_depositinfo,
         casino_core.user_info,
         casino_core.users,
         casino_core.brands,
         analytics.licensees
CASCADE;

-- Brands: brand_a/b/c are B2C; others B2B
INSERT INTO casino_core.brands (id, name) VALUES
    (1,  'brand_a'),
    (2,  'brand_b'),
    (3,  'brand_c'),
    (4,  'partner_x'),
    (5,  'partner_y');

-- Players (affiliateid 38,52,75,82,100 are excluded from report)
INSERT INTO casino_core.users (id, affiliateid) VALUES
    (101, 1),   -- brand_a / B2C
    (102, 2),   -- brand_b / B2C
    (103, 3),   -- brand_c / B2C
    (104, 4),   -- partner_x / B2B
    (105, 38);  -- excluded brand

INSERT INTO casino_core.user_info (userid, country, testaccount) VALUES
    (101, 'GB', FALSE),
    (102, 'SE', FALSE),
    (103, 'DE', FALSE),
    (104, 'NL', FALSE),
    (105, 'GB', FALSE);

INSERT INTO casino_core.user_depositinfo (userid) VALUES
    (101),(102),(103),(104),(105);

-- FX rates (EUR base: GBP 0.86, SEK 11.5, EUR 1.0)
INSERT INTO casino_core.monthly_currencies (currency, year, month, rate) VALUES
    ('GBP', EXTRACT(YEAR FROM CURRENT_DATE)::INT, EXTRACT(MONTH FROM CURRENT_DATE)::INT, 0.860000),
    ('SEK', EXTRACT(YEAR FROM CURRENT_DATE)::INT, EXTRACT(MONTH FROM CURRENT_DATE)::INT, 11.500000),
    ('EUR', EXTRACT(YEAR FROM CURRENT_DATE)::INT, EXTRACT(MONTH FROM CURRENT_DATE)::INT, 1.000000),
    -- Prior year same months (for YoY date windows)
    ('GBP', EXTRACT(YEAR FROM CURRENT_DATE - INTERVAL '1 year')::INT, EXTRACT(MONTH FROM CURRENT_DATE)::INT, 0.870000),
    ('SEK', EXTRACT(YEAR FROM CURRENT_DATE - INTERVAL '1 year')::INT, EXTRACT(MONTH FROM CURRENT_DATE)::INT, 11.300000),
    ('EUR', EXTRACT(YEAR FROM CURRENT_DATE - INTERVAL '1 year')::INT, EXTRACT(MONTH FROM CURRENT_DATE)::INT, 1.000000);

-- Licensee mapping
INSERT INTO analytics.licensees (brand, licensees) VALUES
    ('brand_a', 'LicenseeAlpha'),
    ('brand_b', 'LicenseeBeta'),
    ('partner_x', 'LicenseeGamma');

-- Betting data spanning yesterday and last 28 days
INSERT INTO analytics_dw.daily_player_game_stats
    (user_id, on_date, currency, cash_stake, bonus_stake, cash_refunds, bonus_refunds, cash_return, bonus_return)
VALUES
    -- Yesterday
    (101, CURRENT_DATE - INTERVAL '1 day', 'GBP', 100.00, 10.00, 0.00, 0.00, 80.00,  8.00),
    (102, CURRENT_DATE - INTERVAL '1 day', 'SEK', 500.00, 50.00, 5.00, 0.00, 400.00, 40.00),
    (103, CURRENT_DATE - INTERVAL '1 day', 'EUR', 200.00, 20.00, 0.00, 0.00, 150.00, 15.00),
    (104, CURRENT_DATE - INTERVAL '1 day', 'EUR', 300.00,  0.00, 0.00, 0.00, 250.00,  0.00),
    -- 10 days ago (falls within L28D window)
    (101, CURRENT_DATE - INTERVAL '10 days', 'GBP', 120.00, 12.00, 0.00, 0.00, 90.00,  9.00),
    (102, CURRENT_DATE - INTERVAL '10 days', 'SEK', 600.00, 60.00, 6.00, 0.00, 480.00, 48.00),
    -- 30 days ago (outside L28D window, within year window)
    (101, CURRENT_DATE - INTERVAL '30 days', 'GBP', 80.00, 8.00, 0.00, 0.00, 60.00, 6.00),
    -- Excluded brand (should not appear in results)
    (105, CURRENT_DATE - INTERVAL '1 day', 'GBP', 999.00, 0.00, 0.00, 0.00, 500.00, 0.00);

-- ============================================================
-- REPORT QUERY (unchanged from production version)
-- ============================================================

with var as
(
select

/* DAILY DATES */

date_trunc('day',CURRENT_DATE - interval '1' day) yest_start_date,
date_trunc('day',CURRENT_DATE) - interval '1' second yest_end_date,
date_trunc('day',CURRENT_DATE - interval '28' day) L28D_start_date,
date_trunc('day',CURRENT_DATE) - interval '1' second L28D_end_date,

/* MONTH DATES */

date_trunc('month',CURRENT_DATE - interval '1' second - interval '1' year) M_prev_start_date,
date_trunc('day',CURRENT_DATE - interval '1' year) - interval '1' second  M_prev_end_date,
date_trunc('month',CURRENT_DATE - interval '1' second) M_curr_start_date,
date_trunc('day',CURRENT_DATE) - interval '1' second  M_curr_end_date,

/* QUARTER DATES */

date_trunc('quarter',CURRENT_DATE - interval '1' second - interval '1' year) Q_prev_start_date,
date_trunc('day',CURRENT_DATE - interval '1' year) - interval '1' second  Q_prev_end_date,
date_trunc('quarter',CURRENT_DATE - interval '1' second) Q_curr_start_date,
date_trunc('day',CURRENT_DATE) - interval '1' second  Q_curr_end_date,

/* YEAR DATES */

date_trunc('year',CURRENT_DATE - interval '1' second - interval '1' year) Y_prev_start_date,
date_trunc('day',CURRENT_DATE - interval '1' year) - interval '1' second  Y_prev_end_date,
date_trunc('year',CURRENT_DATE - interval '1' second) Y_curr_start_date,
date_trunc('day',CURRENT_DATE) - interval '1' second  Y_curr_end_date

)
(
select
    case when b.name in ('brand_a', 'brand_b', 'brand_c') then 'B2C' else 'B2B' end business,
    b.name Brand,
    coalesce(l.licensees, 'unknown') licensees,
 CASE
     WHEN ui.country in ('GB','NL','SE','FI','NO','DE','CA','NZ') THEN ui.country
     ELSE  'other ROW'
 END country,

COALESCE(round(SUM(case when dpg.on_date between (select yest_start_date from var) and (select yest_end_date from var) then (dpg.CASH_STAKE + dpg.BONUS_STAKE) - (CASH_REFUNDS + BONUS_REFUNDS) else 0 end/cf.RATE)::numeric,2),0) YEST_TOTAL_STAKES,
COALESCE(round(SUM(case when dpg.on_date between (select L28D_start_date from var) and (select L28D_end_date from var) then (dpg.CASH_STAKE + dpg.BONUS_STAKE) - (CASH_REFUNDS + BONUS_REFUNDS) else 0 end/cf.RATE)::numeric,2),0) L28D_TOTAL_STAKES,

COALESCE(round(SUM(case when dpg.on_date between (select yest_start_date from var) and (select yest_end_date from var) then (dpg.CASH_STAKE + dpg.BONUS_STAKE) - (CASH_REFUNDS + BONUS_REFUNDS) - (CASH_RETURN+BONUS_RETURN) else 0 end/cf.RATE)::numeric,2),0) YEST_TOTAL_GGR,
COALESCE(round(SUM(case when dpg.on_date between (select L28D_start_date from var) and (select L28D_end_date from var) then (dpg.CASH_STAKE + dpg.BONUS_STAKE) - (CASH_REFUNDS + BONUS_REFUNDS) - (CASH_RETURN+BONUS_RETURN) else 0 end/cf.RATE)::numeric,2),0) L28D_TOTAL_GGR,

MAX((select yest_end_date from var)) update_date

from analytics_dw.DAILY_PLAYER_GAME_STATS dpg

         -- LEFT JOIN so a missing FX rate (new currency, unloaded month)
         -- surfaces as NULL instead of silently dropping the player's
         -- entire stake/GGR from the totals (under-reported revenue).
         left join casino_core.monthly_currencies cf ON cf.currency = dpg.currency AND cf.year = EXTRACT(YEAR FROM dpg.ON_DATE) AND cf.month = EXTRACT(MONTH FROM dpg.ON_DATE)
         inner join casino_core.user_DEPOSITINFO dep on dep.USERID = dpg.user_ID
         inner join casino_core.USERS u   on u.id = dpg.user_ID
         inner join casino_core.BRANDS b on u.AFFILIATEID = b.ID
         inner join (select userid, country, testaccount from casino_core.user_info) ui on u.id = ui.USERID  and ui.testaccount is false
         left join analytics.licensees l on l.brand = b.name

where       dpg.on_date between (select Y_prev_start_date from var) and (select Y_curr_end_date from var)
            and u.affiliateid NOT IN (38, 52, 75, 82, 100)

group by
    business,
    b.name,
    licensees,
     CASE
     WHEN ui.country in ('GB','NL','SE','FI','NO','DE','CA','NZ') THEN ui.country
     ELSE  'other ROW'
    end);
