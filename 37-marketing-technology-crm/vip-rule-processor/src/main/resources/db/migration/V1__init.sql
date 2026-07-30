-- V1__init.sql
-- VIP Rule Processor initial schema
-- Flyway migration for PostgreSQL 15
-- Chapter 37: Marketing Technology and CRM -- VIP rule processor

-- ----------------------------------------------------------------
-- rules: defines VIP tier boundaries (2D deposit x bet model)
-- ----------------------------------------------------------------
CREATE TABLE IF NOT EXISTS rules (
    id                  BIGSERIAL PRIMARY KEY,
    name                TEXT        NOT NULL,                -- e.g., 'Standard', 'ND1', 'NW2', 'POT1'
    level               INTEGER     NOT NULL,                -- tier level (1=lowest, 12=highest)
    qualifier           TEXT        NOT NULL DEFAULT 'STANDARD', -- 'STANDARD', 'ND', 'NW', 'POT'

    -- 30-day volume boundaries (amounts in cents)
    min_deposit_30d     BIGINT      NOT NULL DEFAULT 0,
    max_deposit_30d     BIGINT,                              -- NULL = no upper bound
    min_bet_30d         BIGINT      NOT NULL DEFAULT 0,
    max_bet_30d         BIGINT,                              -- NULL = no upper bound

    -- Scoring weights for v2.0 algorithm
    bet_weight          NUMERIC(4,2) NOT NULL DEFAULT 1.00,  -- multiplier for bet volume in scoring
    base_score          INTEGER     NOT NULL DEFAULT 0,

    -- Benefits metadata (stored as JSONB for flexibility)
    benefits            JSONB       NOT NULL DEFAULT '{}',

    -- Audit fields
    active              BOOLEAN     NOT NULL DEFAULT TRUE,
    created_at          TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at          TIMESTAMPTZ NOT NULL DEFAULT NOW(),

    CONSTRAINT rules_level_qualifier_unique UNIQUE (level, qualifier)
);

CREATE INDEX idx_rules_active ON rules (active) WHERE active = TRUE;
CREATE INDEX idx_rules_level ON rules (level);

-- ----------------------------------------------------------------
-- bets: player bet activity stream (partitioned by event date)
-- ----------------------------------------------------------------
CREATE TABLE IF NOT EXISTS bets (
    id              BIGSERIAL,
    user_id         BIGINT      NOT NULL,
    brand_id        INTEGER     NOT NULL DEFAULT 1,
    amount_cents    BIGINT      NOT NULL CHECK (amount_cents > 0),
    game_id         TEXT        NOT NULL,
    game_type       TEXT        NOT NULL DEFAULT 'slots',    -- 'slots', 'live', 'table', 'sports'
    round_id        TEXT        NOT NULL,
    kafka_offset    BIGINT,
    event_at        TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),

    PRIMARY KEY (id, event_at)
) PARTITION BY RANGE (event_at);

-- Create initial monthly partitions (current + 3 months forward)
CREATE TABLE bets_2026_01 PARTITION OF bets
    FOR VALUES FROM ('2026-01-01') TO ('2026-02-01');
CREATE TABLE bets_2026_02 PARTITION OF bets
    FOR VALUES FROM ('2026-02-01') TO ('2026-03-01');
CREATE TABLE bets_2026_03 PARTITION OF bets
    FOR VALUES FROM ('2026-03-01') TO ('2026-04-01');
CREATE TABLE bets_2026_04 PARTITION OF bets
    FOR VALUES FROM ('2026-04-01') TO ('2026-05-01');
CREATE TABLE bets_2026_05 PARTITION OF bets
    FOR VALUES FROM ('2026-05-01') TO ('2026-06-01');
CREATE TABLE bets_2026_06 PARTITION OF bets
    FOR VALUES FROM ('2026-06-01') TO ('2026-07-01');

CREATE INDEX idx_bets_user_event_at ON bets (user_id, event_at DESC);
CREATE INDEX idx_bets_game_type ON bets (game_type, event_at DESC);

-- ----------------------------------------------------------------
-- deposits: player deposit activity stream
-- ----------------------------------------------------------------
CREATE TABLE IF NOT EXISTS deposits (
    id              BIGSERIAL,
    user_id         BIGINT      NOT NULL,
    brand_id        INTEGER     NOT NULL DEFAULT 1,
    amount_cents    BIGINT      NOT NULL CHECK (amount_cents > 0),
    psp_id          TEXT        NOT NULL,
    transaction_id  TEXT        NOT NULL,
    kafka_offset    BIGINT,
    event_at        TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),

    PRIMARY KEY (id, event_at)
) PARTITION BY RANGE (event_at);

CREATE TABLE deposits_2026_01 PARTITION OF deposits
    FOR VALUES FROM ('2026-01-01') TO ('2026-02-01');
CREATE TABLE deposits_2026_02 PARTITION OF deposits
    FOR VALUES FROM ('2026-02-01') TO ('2026-03-01');
CREATE TABLE deposits_2026_03 PARTITION OF deposits
    FOR VALUES FROM ('2026-03-01') TO ('2026-04-01');
CREATE TABLE deposits_2026_04 PARTITION OF deposits
    FOR VALUES FROM ('2026-04-01') TO ('2026-05-01');
CREATE TABLE deposits_2026_05 PARTITION OF deposits
    FOR VALUES FROM ('2026-05-01') TO ('2026-06-01');
CREATE TABLE deposits_2026_06 PARTITION OF deposits
    FOR VALUES FROM ('2026-06-01') TO ('2026-07-01');

CREATE INDEX idx_deposits_user_event_at ON deposits (user_id, event_at DESC);

-- ----------------------------------------------------------------
-- withdrawals: player withdrawal activity stream
-- ----------------------------------------------------------------
CREATE TABLE IF NOT EXISTS withdrawals (
    id              BIGSERIAL,
    user_id         BIGINT      NOT NULL,
    brand_id        INTEGER     NOT NULL DEFAULT 1,
    amount_cents    BIGINT      NOT NULL CHECK (amount_cents > 0),
    psp_id          TEXT        NOT NULL,
    transaction_id  TEXT        NOT NULL,
    kafka_offset    BIGINT,
    event_at        TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),

    PRIMARY KEY (id, event_at)
) PARTITION BY RANGE (event_at);

CREATE TABLE withdrawals_2026_01 PARTITION OF withdrawals
    FOR VALUES FROM ('2026-01-01') TO ('2026-02-01');
CREATE TABLE withdrawals_2026_02 PARTITION OF withdrawals
    FOR VALUES FROM ('2026-02-01') TO ('2026-03-01');
CREATE TABLE withdrawals_2026_03 PARTITION OF withdrawals
    FOR VALUES FROM ('2026-03-01') TO ('2026-04-01');
CREATE TABLE withdrawals_2026_04 PARTITION OF withdrawals
    FOR VALUES FROM ('2026-04-01') TO ('2026-05-01');
CREATE TABLE withdrawals_2026_05 PARTITION OF withdrawals
    FOR VALUES FROM ('2026-05-01') TO ('2026-06-01');
CREATE TABLE withdrawals_2026_06 PARTITION OF withdrawals
    FOR VALUES FROM ('2026-06-01') TO ('2026-07-01');

CREATE INDEX idx_withdrawals_user_event_at ON withdrawals (user_id, event_at DESC);

-- ----------------------------------------------------------------
-- user_status: current VIP tier per player
-- ----------------------------------------------------------------
CREATE TABLE IF NOT EXISTS user_status (
    user_id             BIGINT      PRIMARY KEY,
    brand_id            INTEGER     NOT NULL DEFAULT 1,
    rule_id             BIGINT      REFERENCES rules(id),
    tier_name           TEXT,                               -- denormalized for fast reads
    tier_level          INTEGER     NOT NULL DEFAULT 0,
    score               NUMERIC(12,2) NOT NULL DEFAULT 0,

    -- 30-day rolling aggregates (updated by rule processor)
    deposits_30d_cents  BIGINT      NOT NULL DEFAULT 0,
    bets_30d_cents      BIGINT      NOT NULL DEFAULT 0,
    withdrawals_30d_cents BIGINT    NOT NULL DEFAULT 0,
    active_days_30d     INTEGER     NOT NULL DEFAULT 0,

    -- Responsible gambling: self-excluded players never promote
    self_excluded       BOOLEAN     NOT NULL DEFAULT FALSE,
    exclusion_end_at    TIMESTAMPTZ,

    -- Timestamps
    tier_changed_at     TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    last_evaluated_at   TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    created_at          TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at          TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX idx_user_status_rule_id ON user_status (rule_id);
CREATE INDEX idx_user_status_tier_level ON user_status (tier_level);
CREATE INDEX idx_user_status_self_excluded ON user_status (self_excluded) WHERE self_excluded = TRUE;

-- ----------------------------------------------------------------
-- scheduler: tracks last execution of batch recalculation jobs
-- ----------------------------------------------------------------
CREATE TABLE IF NOT EXISTS scheduler (
    id              BIGSERIAL   PRIMARY KEY,
    job_name        TEXT        NOT NULL,
    last_run_at     TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    next_run_at     TIMESTAMPTZ,
    status          TEXT        NOT NULL DEFAULT 'PENDING',  -- 'PENDING', 'RUNNING', 'DONE', 'FAILED'
    users_processed INTEGER     NOT NULL DEFAULT 0,
    error_message   TEXT,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX idx_scheduler_job_name ON scheduler (job_name, last_run_at DESC);

-- ----------------------------------------------------------------
-- Trigger: update updated_at on user_status
-- ----------------------------------------------------------------
CREATE OR REPLACE FUNCTION update_updated_at()
RETURNS TRIGGER AS $$
BEGIN
    NEW.updated_at = NOW();
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

CREATE TRIGGER user_status_updated_at
    BEFORE UPDATE ON user_status
    FOR EACH ROW EXECUTE FUNCTION update_updated_at();

CREATE TRIGGER rules_updated_at
    BEFORE UPDATE ON rules
    FOR EACH ROW EXECUTE FUNCTION update_updated_at();
