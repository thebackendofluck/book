-- Complete database schema for RTC implementation
-- Requires PostgreSQL 15+ with TimescaleDB extension
-- Note: timescaledb.continuous aggregates require TimescaleDB.
--       On standard PostgreSQL, hourly_transaction_stats is created
--       as a regular materialized view instead.

-- Enable required extensions
CREATE EXTENSION IF NOT EXISTS timescaledb CASCADE;
CREATE EXTENSION IF NOT EXISTS pgcrypto;
CREATE EXTENSION IF NOT EXISTS btree_gin;
CREATE EXTENSION IF NOT EXISTS btree_gist;

-- Create schema
CREATE SCHEMA IF NOT EXISTS rtc_system;
SET search_path TO rtc_system, public;

-- =====================================================
-- Core RTC Tables
-- =====================================================

-- Main timestamp storage with hypertable partitioning
CREATE TABLE casino_timestamps (
    id BIGSERIAL,
    event_type VARCHAR(50) NOT NULL,
    event_id UUID NOT NULL DEFAULT gen_random_uuid(),
    system_time TIMESTAMP WITH TIME ZONE NOT NULL,
    rtc_time TIMESTAMP WITH TIME ZONE NOT NULL,
    rtc_nano BIGINT NOT NULL,
    rtc_signature VARCHAR(128) NOT NULL,
    rtc_source VARCHAR(50) NOT NULL,
    drift_ms DOUBLE PRECISION,
    confidence DOUBLE PRECISION,
    temperature DOUBLE PRECISION,
    battery_level DOUBLE PRECISION,
    metadata JSONB,
    created_at TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
    PRIMARY KEY (id, rtc_time)
);

-- Convert to TimescaleDB hypertable
SELECT create_hypertable('casino_timestamps', 'rtc_time',
    chunk_time_interval => INTERVAL '1 day',
    if_not_exists => TRUE);

-- Create indexes
CREATE INDEX idx_event_type ON casino_timestamps (event_type, rtc_time DESC);
CREATE INDEX idx_event_id ON casino_timestamps (event_id);
CREATE INDEX idx_drift ON casino_timestamps (drift_ms) WHERE ABS(drift_ms) > 10;
CREATE INDEX idx_metadata ON casino_timestamps USING GIN (metadata);
CREATE INDEX idx_rtc_source ON casino_timestamps (rtc_source, rtc_time DESC);

-- =====================================================
-- Financial Transaction Tables
-- =====================================================

CREATE TABLE financial_transactions (
    transaction_id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id BIGINT NOT NULL,
    transaction_type VARCHAR(20) NOT NULL CHECK (transaction_type IN
        ('deposit', 'withdrawal', 'bet', 'win', 'refund', 'bonus', 'fee')),
    amount DECIMAL(18,8) NOT NULL CHECK (amount >= 0),
    currency VARCHAR(3) NOT NULL,
    game_id UUID,
    round_id UUID,
    session_id UUID,

    -- RTC fields
    system_timestamp TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT NOW(),
    rtc_timestamp TIMESTAMP WITH TIME ZONE NOT NULL,
    rtc_nano BIGINT NOT NULL,
    rtc_signature VARCHAR(128) NOT NULL,
    rtc_confidence DOUBLE PRECISION NOT NULL,

    -- Transaction details
    status VARCHAR(20) NOT NULL DEFAULT 'pending' CHECK (status IN
        ('pending', 'processing', 'completed', 'failed', 'reversed')),
    processor VARCHAR(50),
    processor_response JSONB,

    -- Compliance fields
    jurisdiction VARCHAR(50),
    tax_amount DECIMAL(18,8) DEFAULT 0,

    -- Audit fields
    created_at TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
    updated_at TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
    created_by VARCHAR(100),
    updated_by VARCHAR(100)
);

CREATE INDEX idx_user_transactions ON financial_transactions (user_id, rtc_timestamp DESC);
CREATE INDEX idx_status ON financial_transactions (status) WHERE status != 'completed';
CREATE INDEX idx_game_transactions ON financial_transactions (game_id, rtc_timestamp DESC) WHERE game_id IS NOT NULL;
CREATE INDEX idx_round_transactions ON financial_transactions (round_id) WHERE round_id IS NOT NULL;

-- =====================================================
-- Game Round Tables
-- =====================================================

CREATE TABLE game_rounds (
    round_id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    game_id VARCHAR(50) NOT NULL,
    game_version VARCHAR(20) NOT NULL,
    user_id BIGINT NOT NULL,
    session_id UUID NOT NULL,

    -- Betting information
    bet_amount DECIMAL(18,8) NOT NULL CHECK (bet_amount > 0),
    win_amount DECIMAL(18,8) DEFAULT 0 CHECK (win_amount >= 0),
    currency VARCHAR(3) NOT NULL,
    bet_details JSONB,

    -- RTC timing
    round_start_rtc TIMESTAMP WITH TIME ZONE NOT NULL,
    round_start_nano BIGINT NOT NULL,
    round_end_rtc TIMESTAMP WITH TIME ZONE,
    round_end_nano BIGINT,

    -- RNG information
    rng_seed VARCHAR(256) NOT NULL,
    rng_timestamp_rtc TIMESTAMP WITH TIME ZONE NOT NULL,
    rng_algorithm VARCHAR(50) DEFAULT 'MT19937',

    -- Game state
    game_state JSONB NOT NULL,
    result JSONB,
    rtc_signatures JSONB NOT NULL,

    -- Status tracking
    status VARCHAR(20) NOT NULL DEFAULT 'active' CHECK (status IN
        ('active', 'completed', 'cancelled', 'disputed')),

    -- Performance metrics
    client_latency_ms INTEGER,
    server_processing_ms INTEGER
);

CREATE INDEX idx_user_rounds ON game_rounds (user_id, round_start_rtc DESC);
CREATE INDEX idx_game_rounds ON game_rounds (game_id, round_start_rtc DESC);
CREATE INDEX idx_session_rounds ON game_rounds (session_id, round_start_rtc DESC);
CREATE INDEX idx_active_rounds ON game_rounds (status) WHERE status = 'active';

-- =====================================================
-- Progressive Jackpot Tables
-- =====================================================

CREATE TABLE progressive_jackpots (
    jackpot_id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    jackpot_name VARCHAR(100) NOT NULL UNIQUE,
    jackpot_type VARCHAR(50) NOT NULL CHECK (jackpot_type IN
        ('standalone', 'local', 'wide-area', 'mystery')),

    -- Value tracking
    seed_value DECIMAL(18,8) NOT NULL,
    current_value DECIMAL(18,8) NOT NULL CHECK (current_value >= seed_value),
    max_value DECIMAL(18,8),
    currency VARCHAR(3) NOT NULL,

    -- Contribution settings
    contribution_percentage DECIMAL(5,4) NOT NULL CHECK (contribution_percentage BETWEEN 0 AND 1),
    min_bet DECIMAL(18,8) NOT NULL,
    qualifying_games TEXT[],

    -- Winner information
    last_won_rtc TIMESTAMP WITH TIME ZONE,
    last_won_nano BIGINT,
    winner_user_id BIGINT,
    winner_round_id UUID,
    win_amount DECIMAL(18,8),

    -- RTC tracking
    rtc_timestamp TIMESTAMP WITH TIME ZONE NOT NULL,
    rtc_nano BIGINT NOT NULL,
    rtc_signature VARCHAR(128) NOT NULL,

    -- Status
    status VARCHAR(20) DEFAULT 'active' CHECK (status IN
        ('active', 'suspended', 'won', 'expired')),

    created_at TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
    updated_at TIMESTAMP WITH TIME ZONE DEFAULT NOW()
);

-- Jackpot contribution tracking
CREATE TABLE jackpot_contributions (
    contribution_id BIGSERIAL PRIMARY KEY,
    jackpot_id UUID NOT NULL REFERENCES progressive_jackpots(jackpot_id),
    round_id UUID NOT NULL REFERENCES game_rounds(round_id),
    user_id BIGINT NOT NULL,
    contribution_amount DECIMAL(18,8) NOT NULL,
    rtc_timestamp TIMESTAMP WITH TIME ZONE NOT NULL,
    rtc_nano BIGINT NOT NULL
);

CREATE INDEX idx_jackpot_contributions ON jackpot_contributions (jackpot_id, rtc_timestamp DESC);
CREATE INDEX idx_user_contributions ON jackpot_contributions (user_id, rtc_timestamp DESC);

-- =====================================================
-- Audit and Compliance Tables
-- =====================================================

CREATE TABLE audit_log (
    log_id BIGSERIAL,
    event_type VARCHAR(100) NOT NULL,
    entity_type VARCHAR(50) NOT NULL,
    entity_id VARCHAR(100) NOT NULL,
    user_id BIGINT,
    action VARCHAR(50) NOT NULL,

    -- Change tracking
    old_value JSONB,
    new_value JSONB,
    change_details JSONB,

    -- RTC fields
    rtc_timestamp TIMESTAMP WITH TIME ZONE NOT NULL,
    rtc_nano BIGINT NOT NULL,
    rtc_signature VARCHAR(128) NOT NULL,

    -- Hash chain for immutability
    previous_hash VARCHAR(64),
    current_hash VARCHAR(64) NOT NULL,

    -- Context
    ip_address INET,
    user_agent TEXT,
    session_id UUID,
    request_id UUID,

    created_at TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
    PRIMARY KEY (log_id, rtc_timestamp)
) PARTITION BY RANGE (rtc_timestamp);

-- Create monthly partitions for audit log
DO $$
DECLARE
    start_date DATE := DATE_TRUNC('month', CURRENT_DATE);
    end_date DATE;
    partition_name TEXT;
BEGIN
    FOR i IN 0..23 LOOP
        end_date := start_date + INTERVAL '1 month';
        partition_name := 'audit_log_' || TO_CHAR(start_date, 'YYYY_MM');

        EXECUTE format('CREATE TABLE IF NOT EXISTS %I PARTITION OF audit_log
            FOR VALUES FROM (%L) TO (%L)',
            partition_name, start_date, end_date);

        start_date := end_date;
    END LOOP;
END $$;

CREATE INDEX idx_audit_event ON audit_log (event_type, rtc_timestamp DESC);
CREATE INDEX idx_audit_entity ON audit_log (entity_type, entity_id);
CREATE INDEX idx_audit_user ON audit_log (user_id, rtc_timestamp DESC) WHERE user_id IS NOT NULL;
CREATE INDEX idx_audit_hash ON audit_log (current_hash);

-- =====================================================
-- Compliance and Regulatory Tables
-- =====================================================

CREATE TABLE compliance_checks (
    check_id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    check_type VARCHAR(100) NOT NULL,
    user_id BIGINT NOT NULL,

    -- Check details
    jurisdiction VARCHAR(50) NOT NULL,
    rule_violated VARCHAR(200),
    severity VARCHAR(20) CHECK (severity IN ('info', 'warning', 'critical', 'block')),

    -- RTC timestamp
    rtc_timestamp TIMESTAMP WITH TIME ZONE NOT NULL,
    rtc_nano BIGINT NOT NULL,
    rtc_signature VARCHAR(128) NOT NULL,

    -- Results
    passed BOOLEAN NOT NULL,
    details JSONB,
    action_taken VARCHAR(100),

    created_at TIMESTAMP WITH TIME ZONE DEFAULT NOW()
);

CREATE INDEX idx_compliance_user ON compliance_checks (user_id, rtc_timestamp DESC);
CREATE INDEX idx_compliance_type ON compliance_checks (check_type, rtc_timestamp DESC);
CREATE INDEX idx_compliance_failed ON compliance_checks (passed, severity) WHERE passed = FALSE;

-- Self-exclusion tracking
CREATE TABLE self_exclusions (
    exclusion_id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id BIGINT NOT NULL,
    exclusion_type VARCHAR(50) NOT NULL CHECK (exclusion_type IN
        ('cooling_off', 'self_exclusion', 'timeout', 'permanent')),

    -- Time boundaries with RTC
    start_rtc TIMESTAMP WITH TIME ZONE NOT NULL,
    start_nano BIGINT NOT NULL,
    end_rtc TIMESTAMP WITH TIME ZONE NOT NULL,
    end_nano BIGINT NOT NULL,

    -- Signatures for legal proof
    start_signature VARCHAR(128) NOT NULL,
    end_signature VARCHAR(128) NOT NULL,

    -- Details
    reason TEXT,
    jurisdiction VARCHAR(50),
    status VARCHAR(20) DEFAULT 'active' CHECK (status IN
        ('pending', 'active', 'expired', 'revoked')),

    created_at TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
    created_by VARCHAR(100)
);

CREATE INDEX idx_exclusion_user ON self_exclusions (user_id, status);
CREATE INDEX idx_exclusion_active ON self_exclusions (end_rtc) WHERE status = 'active';

-- =====================================================
-- RTC Hardware Management Tables
-- =====================================================

CREATE TABLE rtc_devices (
    device_id VARCHAR(50) PRIMARY KEY,
    device_type VARCHAR(50) NOT NULL,
    serial_number VARCHAR(100) UNIQUE,

    -- Hardware details
    firmware_version VARCHAR(50),
    hardware_version VARCHAR(50),
    manufacturer VARCHAR(100),

    -- Location
    data_center VARCHAR(50),
    rack_location VARCHAR(50),

    -- Status
    status VARCHAR(20) DEFAULT 'active' CHECK (status IN
        ('active', 'degraded', 'failed', 'maintenance', 'quarantined')),

    -- Metrics
    current_drift_ms DOUBLE PRECISION,
    temperature_celsius DOUBLE PRECISION,
    battery_percentage DOUBLE PRECISION,

    -- Timestamps
    installed_at TIMESTAMP WITH TIME ZONE,
    last_maintenance TIMESTAMP WITH TIME ZONE,
    last_sync TIMESTAMP WITH TIME ZONE,

    created_at TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
    updated_at TIMESTAMP WITH TIME ZONE DEFAULT NOW()
);

-- RTC synchronization log
CREATE TABLE rtc_sync_log (
    sync_id BIGSERIAL PRIMARY KEY,
    device_id VARCHAR(50) REFERENCES rtc_devices(device_id),
    sync_source VARCHAR(100) NOT NULL,

    -- Synchronization details
    sync_timestamp TIMESTAMP WITH TIME ZONE NOT NULL,
    offset_ms DOUBLE PRECISION NOT NULL,
    drift_rate_ppm DOUBLE PRECISION,

    -- Status
    success BOOLEAN NOT NULL,
    error_message TEXT,

    created_at TIMESTAMP WITH TIME ZONE DEFAULT NOW()
);

CREATE INDEX idx_sync_device ON rtc_sync_log (device_id, sync_timestamp DESC);
CREATE INDEX idx_sync_failures ON rtc_sync_log (success, sync_timestamp DESC) WHERE success = FALSE;

-- =====================================================
-- Functions and Procedures
-- =====================================================

-- Function to validate RTC signature
CREATE OR REPLACE FUNCTION validate_rtc_signature(
    p_timestamp TIMESTAMP WITH TIME ZONE,
    p_nano BIGINT,
    p_signature VARCHAR(128),
    p_source VARCHAR(50)
) RETURNS BOOLEAN AS $$
DECLARE
    expected_signature VARCHAR(128);
    secret_key TEXT;
BEGIN
    -- Get secret key for the RTC source
    SELECT current_setting('app.rtc_secret_' || p_source, true) INTO secret_key;

    IF secret_key IS NULL THEN
        secret_key := current_setting('app.rtc_secret_default');
    END IF;

    -- Calculate expected signature
    expected_signature := encode(
        hmac(
            p_timestamp::TEXT || ':' || p_nano::TEXT || ':' || p_source,
            secret_key,
            'sha256'
        ),
        'hex'
    );

    RETURN p_signature = expected_signature;
END;
$$ LANGUAGE plpgsql IMMUTABLE;

-- Function to calculate hash chain
CREATE OR REPLACE FUNCTION calculate_hash_chain(
    p_data JSONB,
    p_previous_hash VARCHAR(64)
) RETURNS VARCHAR(64) AS $$
BEGIN
    RETURN encode(
        digest(
            COALESCE(p_previous_hash, '') || p_data::TEXT,
            'sha256'
        ),
        'hex'
    );
END;
$$ LANGUAGE plpgsql IMMUTABLE;

-- Trigger for automatic RTC validation
CREATE OR REPLACE FUNCTION validate_rtc_timestamp()
RETURNS TRIGGER AS $$
DECLARE
    max_drift_ms CONSTANT DOUBLE PRECISION := 1000.0;
    time_diff_ms DOUBLE PRECISION;
BEGIN
    -- Validate signature if present
    IF TG_TABLE_NAME IN ('financial_transactions', 'audit_log') THEN
        IF NOT validate_rtc_signature(
            NEW.rtc_timestamp,
            NEW.rtc_nano,
            NEW.rtc_signature,
            COALESCE(NEW.rtc_source, 'primary')
        ) THEN
            RAISE EXCEPTION 'Invalid RTC signature for %', TG_TABLE_NAME;
        END IF;
    END IF;

    -- Calculate drift
    time_diff_ms := ABS(
        EXTRACT(EPOCH FROM (NEW.rtc_timestamp - CURRENT_TIMESTAMP)) * 1000
    );

    -- Store drift if column exists
    IF TG_TABLE_NAME = 'casino_timestamps' THEN
        NEW.drift_ms := time_diff_ms;
    END IF;

    -- Reject excessive drift for critical tables
    IF TG_TABLE_NAME IN ('financial_transactions', 'progressive_jackpots') THEN
        IF time_diff_ms > max_drift_ms THEN
            RAISE EXCEPTION 'RTC timestamp drift exceeds threshold: % ms', time_diff_ms;
        END IF;
    END IF;

    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

-- Apply RTC validation triggers
CREATE TRIGGER validate_transaction_rtc
    BEFORE INSERT ON financial_transactions
    FOR EACH ROW
    EXECUTE FUNCTION validate_rtc_timestamp();

CREATE TRIGGER validate_audit_rtc
    BEFORE INSERT ON audit_log
    FOR EACH ROW
    EXECUTE FUNCTION validate_rtc_timestamp();

CREATE TRIGGER validate_timestamp_rtc
    BEFORE INSERT ON casino_timestamps
    FOR EACH ROW
    EXECUTE FUNCTION validate_rtc_timestamp();

-- Trigger for audit log hash chain
CREATE OR REPLACE FUNCTION maintain_audit_hash_chain()
RETURNS TRIGGER AS $$
DECLARE
    last_hash VARCHAR(64);
BEGIN
    -- Get the last hash
    SELECT current_hash INTO last_hash
    FROM audit_log
    WHERE log_id < NEW.log_id
    ORDER BY log_id DESC
    LIMIT 1;

    -- Set previous hash
    NEW.previous_hash := last_hash;

    -- Calculate current hash
    NEW.current_hash := calculate_hash_chain(
        to_jsonb(NEW) - 'current_hash' - 'previous_hash',
        last_hash
    );

    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

CREATE TRIGGER audit_hash_chain
    BEFORE INSERT ON audit_log
    FOR EACH ROW
    EXECUTE FUNCTION maintain_audit_hash_chain();

-- =====================================================
-- Materialized Views for Analytics
-- =====================================================

-- Hourly transaction statistics
-- Note: On TimescaleDB, use WITH (timescaledb.continuous) for real-time refresh.
-- On standard PostgreSQL, this is a regular materialized view.
CREATE MATERIALIZED VIEW hourly_transaction_stats AS
SELECT
    date_trunc('hour', rtc_timestamp) AS hour,
    transaction_type,
    currency,
    COUNT(*) as transaction_count,
    SUM(amount) as total_amount,
    AVG(amount) as avg_amount,
    MAX(amount) as max_amount,
    MIN(amount) as min_amount,
    PERCENTILE_CONT(0.5) WITHIN GROUP (ORDER BY amount) as median_amount,
    PERCENTILE_CONT(0.95) WITHIN GROUP (ORDER BY amount) as p95_amount,
    AVG(EXTRACT(EPOCH FROM (updated_at - created_at))) as avg_processing_time_seconds
FROM financial_transactions
WHERE status = 'completed'
GROUP BY hour, transaction_type, currency
WITH NO DATA;

-- Game performance statistics
CREATE MATERIALIZED VIEW game_performance_stats AS
SELECT
    game_id,
    DATE(round_start_rtc) as play_date,
    COUNT(DISTINCT user_id) as unique_players,
    COUNT(*) as total_rounds,
    SUM(bet_amount) as total_wagered,
    SUM(win_amount) as total_won,
    SUM(bet_amount - win_amount) as gross_revenue,
    (SUM(win_amount) / NULLIF(SUM(bet_amount), 0)) * 100 as rtp_percentage,
    AVG(server_processing_ms) as avg_processing_time
FROM game_rounds
WHERE status = 'completed'
GROUP BY game_id, DATE(round_start_rtc);

CREATE INDEX idx_game_perf_date ON game_performance_stats (play_date DESC);

-- =====================================================
-- Row Level Security Policies
-- =====================================================

-- Enable RLS
ALTER TABLE financial_transactions ENABLE ROW LEVEL SECURITY;
ALTER TABLE game_rounds ENABLE ROW LEVEL SECURITY;
ALTER TABLE audit_log ENABLE ROW LEVEL SECURITY;

-- Policy for financial transactions
CREATE POLICY user_transactions ON financial_transactions
    FOR SELECT
    USING (user_id = current_setting('app.current_user_id')::BIGINT
           OR current_setting('app.user_role') = 'admin');

-- Policy for game rounds
CREATE POLICY user_rounds ON game_rounds
    FOR SELECT
    USING (user_id = current_setting('app.current_user_id')::BIGINT
           OR current_setting('app.user_role') IN ('admin', 'support'));

-- Policy for audit log (read-only for compliance)
CREATE POLICY audit_read_only ON audit_log
    FOR SELECT
    USING (current_setting('app.user_role') IN ('admin', 'auditor', 'compliance'));

-- =====================================================
-- Performance Optimization
-- =====================================================

-- Create statistics for query optimization
CREATE STATISTICS stat_transaction_user_type (dependencies)
    ON user_id, transaction_type FROM financial_transactions;

CREATE STATISTICS stat_round_game_user (dependencies)
    ON game_id, user_id FROM game_rounds;

-- Partial indexes for common queries
-- Note: partial indexes on time windows should use a static date or be maintained
-- via scheduled recreation. The expression below uses a fixed lookback for illustration.
CREATE INDEX idx_recent_transactions
    ON financial_transactions (rtc_timestamp DESC);

CREATE INDEX idx_pending_transactions
    ON financial_transactions (created_at)
    WHERE status = 'pending';

CREATE INDEX idx_high_value_transactions
    ON financial_transactions (amount, rtc_timestamp DESC)
    WHERE amount > 1000;

-- BRIN indexes for time-series data
CREATE INDEX idx_brin_timestamp
    ON casino_timestamps USING BRIN (rtc_time);

CREATE INDEX idx_brin_audit
    ON audit_log USING BRIN (rtc_timestamp);
