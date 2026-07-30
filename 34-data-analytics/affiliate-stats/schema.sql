-- Affiliate Stats -- PostgreSQL schema and evolution scripts
-- Sanitized production example from an iGaming platform
--
-- This file combines the initial schema setup with the evolution
-- scripts that Play Framework applies on startup.

-- =========================================================================
-- Initial schema creation
-- =========================================================================

DO $schema$
BEGIN
    IF NOT EXISTS (
        SELECT schema_name FROM information_schema.schemata
        WHERE schema_name = 'affiliate_stats'
    ) THEN
        CREATE SCHEMA affiliate_stats;
    END IF;
END $schema$;


-- =========================================================================
-- Evolution 1: Currency rates and system settings
-- =========================================================================

CREATE TABLE affiliate_stats.currency_fixed_rates (
    id   VARCHAR(3) PRIMARY KEY,
    rate NUMERIC(16, 2) NOT NULL
);

INSERT INTO affiliate_stats.currency_fixed_rates VALUES
    ('GBP', 1),  ('EUR', 1),  ('USD', 1),  ('AUD', 1),
    ('CAD', 1),  ('ZAR', 10), ('NOK', 10), ('SEK', 10),
    ('NZD', 1),  ('CHF', 1),  ('ARS', 100),
    ('CLP', 1000), ('PEN', 10);

CREATE TABLE affiliate_stats.settings (
    id    VARCHAR(255)  PRIMARY KEY,
    value VARCHAR(1023) NOT NULL
);


-- =========================================================================
-- Evolution 2: Kafka message tracking (offset management + error audit)
-- =========================================================================

CREATE TABLE affiliate_stats.message_offsets (
    topic VARCHAR(255) NOT NULL PRIMARY KEY,
    value BIGINT DEFAULT 0
);

CREATE TABLE affiliate_stats.message_error_audit (
    id            SERIAL PRIMARY KEY,
    topic_name    VARCHAR(255),
    message       TEXT,
    error_message VARCHAR(2000),
    creation_date DATE NOT NULL
);


-- =========================================================================
-- Evolution 3: Hourly stats (core aggregation output)
-- =========================================================================

CREATE TABLE affiliate_stats.hourly_stats (
    id        SERIAL      PRIMARY KEY,
    user_id   NUMERIC(38) NOT NULL,
    bet_count NUMERIC(1000) NOT NULL,
    start_time TIMESTAMP  NOT NULL,
    end_time   TIMESTAMP  NOT NULL
);

-- Unique constraint prevents duplicate aggregations for the same
-- user + time window (idempotent upserts)
CREATE UNIQUE INDEX unique_stats
    ON affiliate_stats.hourly_stats (user_id, start_time, end_time);
