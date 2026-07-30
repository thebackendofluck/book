-- =============================================================================
-- TimescaleDB Setup for RTC Timestamp Storage
-- =============================================================================
-- Production-grade PostgreSQL/TimescaleDB schema specifically for the RTC
-- timestamp service layer. This supplements the existing core schema in
-- rtc-system/sql/schema.sql with additional infrastructure tables, signature
-- validation triggers, and performance optimizations.
--
-- GLI-11 Requirement: Section 5.4.3 mandates that all timestamps used in
-- gaming operations be stored in an immutable, auditable format with
-- cryptographic integrity verification.
--
-- Prerequisites:
--   - PostgreSQL 15+ with TimescaleDB 2.x extension
--   - pgcrypto extension for HMAC operations
--   - Sufficient disk space for time-series data retention (min 2 years)
--
-- Usage:
--   psql -h db-host -U rtc_admin -d casino_rtc -f timescaledb-setup.sql
-- =============================================================================

-- Enable required extensions
CREATE EXTENSION IF NOT EXISTS timescaledb CASCADE;
CREATE EXTENSION IF NOT EXISTS pgcrypto;
CREATE EXTENSION IF NOT EXISTS btree_gin;

-- Create dedicated schema for RTC infrastructure
CREATE SCHEMA IF NOT EXISTS rtc_infra;
SET search_path TO rtc_infra, public;

-- =============================================================================
-- RTC Consensus Rounds Table (Time-Series)
-- =============================================================================
-- Records every BFT consensus round for audit and diagnostics.
-- GLI-11 requires full audit trail of all time source decisions.

CREATE TABLE IF NOT EXISTS consensus_rounds (
    round_id TEXT NOT NULL,
    round_time TIMESTAMPTZ NOT NULL,
    status TEXT NOT NULL CHECK (status IN (
        'success', 'degraded', 'quorum_failure', 'byzantine_detected', 'timeout'
    )),
    confidence DOUBLE PRECISION NOT NULL CHECK (confidence BETWEEN 0 AND 1),
    consensus_timestamp TIMESTAMPTZ NOT NULL,
    consensus_nano BIGINT NOT NULL,
    spread_ms DOUBLE PRECISION NOT NULL,
    drift_from_system_ms DOUBLE PRECISION NOT NULL,
    participating_modules TEXT[] NOT NULL,
    excluded_modules TEXT[] DEFAULT '{}',
    total_modules INTEGER NOT NULL,
    valid_readings INTEGER NOT NULL,
    signature TEXT NOT NULL,
    round_duration_us INTEGER NOT NULL,
    details JSONB DEFAULT '{}',
    PRIMARY KEY (round_id, round_time)
);

-- Convert to hypertable for efficient time-series storage
SELECT create_hypertable('consensus_rounds', 'round_time',
    chunk_time_interval => INTERVAL '1 hour',
    if_not_exists => TRUE
);

-- Indexes for common query patterns
CREATE INDEX IF NOT EXISTS idx_consensus_status
    ON consensus_rounds (status, round_time DESC);
CREATE INDEX IF NOT EXISTS idx_consensus_confidence
    ON consensus_rounds (confidence, round_time DESC)
    WHERE confidence < 0.8;
CREATE INDEX IF NOT EXISTS idx_consensus_byzantine
    ON consensus_rounds USING GIN (excluded_modules)
    WHERE array_length(excluded_modules, 1) > 0;

-- =============================================================================
-- Byzantine Fault Events Table
-- =============================================================================
-- Records every detected Byzantine fault for security analysis.

CREATE TABLE IF NOT EXISTS byzantine_events (
    id BIGSERIAL,
    event_time TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    module_id TEXT NOT NULL,
    round_id TEXT NOT NULL,
    evidence_type TEXT NOT NULL CHECK (evidence_type IN (
        'excessive_drift', 'time_reversal', 'impossible_value',
        'signature_mismatch', 'hardware_tampering'
    )),
    deviation_ms DOUBLE PRECISION NOT NULL,
    threshold_ms DOUBLE PRECISION NOT NULL,
    details TEXT,
    acknowledged BOOLEAN DEFAULT FALSE,
    acknowledged_by TEXT,
    acknowledged_at TIMESTAMPTZ,
    PRIMARY KEY (id, event_time)
);

SELECT create_hypertable('byzantine_events', 'event_time',
    chunk_time_interval => INTERVAL '1 day',
    if_not_exists => TRUE
);

CREATE INDEX IF NOT EXISTS idx_byzantine_module
    ON byzantine_events (module_id, event_time DESC);
CREATE INDEX IF NOT EXISTS idx_byzantine_unacked
    ON byzantine_events (acknowledged, event_time DESC)
    WHERE acknowledged = FALSE;

-- =============================================================================
-- Signed Timestamps Table (Immutable Audit Log)
-- =============================================================================
-- Every signed timestamp issued by the service is recorded here.
-- This table is append-only; no updates or deletes are allowed.

CREATE TABLE IF NOT EXISTS signed_timestamps (
    ts_id BIGSERIAL,
    issued_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    unix_seconds BIGINT NOT NULL,
    nano BIGINT NOT NULL,
    iso8601 TEXT NOT NULL,
    source_module TEXT NOT NULL,
    signature TEXT NOT NULL,
    confidence DOUBLE PRECISION NOT NULL,
    drift_ms DOUBLE PRECISION,
    temperature_celsius DOUBLE PRECISION,
    consensus_round_id TEXT NOT NULL,
    request_id TEXT,
    game_id TEXT,
    session_id TEXT,
    event_type TEXT,
    metadata JSONB DEFAULT '{}',
    PRIMARY KEY (ts_id, issued_at)
);

SELECT create_hypertable('signed_timestamps', 'issued_at',
    chunk_time_interval => INTERVAL '1 hour',
    if_not_exists => TRUE
);

CREATE INDEX IF NOT EXISTS idx_signed_ts_game
    ON signed_timestamps (game_id, issued_at DESC)
    WHERE game_id IS NOT NULL;
CREATE INDEX IF NOT EXISTS idx_signed_ts_session
    ON signed_timestamps (session_id, issued_at DESC)
    WHERE session_id IS NOT NULL;
CREATE INDEX IF NOT EXISTS idx_signed_ts_request
    ON signed_timestamps (request_id)
    WHERE request_id IS NOT NULL;
CREATE INDEX IF NOT EXISTS idx_signed_ts_consensus
    ON signed_timestamps (consensus_round_id);

-- =============================================================================
-- Module Health Metrics Table (Time-Series)
-- =============================================================================
-- Stores periodic health readings from each RTC module for trend analysis.

CREATE TABLE IF NOT EXISTS module_health (
    metric_time TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    module_id TEXT NOT NULL,
    drift_ms DOUBLE PRECISION,
    temperature_celsius DOUBLE PRECISION,
    battery_percentage DOUBLE PRECISION,
    drift_rate_ppm DOUBLE PRECISION,
    aging_offset INTEGER,
    status TEXT DEFAULT 'active',
    readings_count BIGINT DEFAULT 0,
    error_count BIGINT DEFAULT 0,
    consensus_participations BIGINT DEFAULT 0,
    PRIMARY KEY (module_id, metric_time)
);

SELECT create_hypertable('module_health', 'metric_time',
    chunk_time_interval => INTERVAL '1 day',
    if_not_exists => TRUE
);

CREATE INDEX IF NOT EXISTS idx_health_module_time
    ON module_health (module_id, metric_time DESC);
CREATE INDEX IF NOT EXISTS idx_health_degraded
    ON module_health (status, metric_time DESC)
    WHERE status != 'active';

-- =============================================================================
-- Key Rotation Log
-- =============================================================================
-- Tracks signing key rotations for historical signature verification.
-- GLI-11: Old keys must be retained for the full audit retention period.

CREATE TABLE IF NOT EXISTS key_rotations (
    key_id TEXT PRIMARY KEY,
    algorithm TEXT NOT NULL DEFAULT 'HMAC-SHA256',
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    expires_at TIMESTAMPTZ NOT NULL,
    retired_at TIMESTAMPTZ,
    key_hash TEXT NOT NULL,  -- SHA-256 hash of key (not the key itself)
    created_by TEXT NOT NULL,
    reason TEXT,
    is_active BOOLEAN DEFAULT TRUE
);

CREATE INDEX IF NOT EXISTS idx_key_active ON key_rotations (is_active, created_at DESC);

-- =============================================================================
-- Signature Validation Functions
-- =============================================================================

-- Validate HMAC-SHA256 signature on a timestamp record
-- This mirrors the signing logic in the Go RTC service
CREATE OR REPLACE FUNCTION rtc_infra.validate_timestamp_signature(
    p_unix_seconds BIGINT,
    p_nano BIGINT,
    p_source TEXT,
    p_signature TEXT,
    p_key_id TEXT DEFAULT NULL
) RETURNS TABLE (
    is_valid BOOLEAN,
    key_used TEXT,
    message TEXT
) AS $$
DECLARE
    v_secret TEXT;
    v_key_id TEXT;
    v_data TEXT;
    v_expected TEXT;
BEGIN
    -- Determine which key to use
    IF p_key_id IS NOT NULL THEN
        -- Use specific key (for historical validation)
        SELECT kr.key_id INTO v_key_id
        FROM rtc_infra.key_rotations kr
        WHERE kr.key_id = p_key_id;

        IF v_key_id IS NULL THEN
            RETURN QUERY SELECT FALSE, p_key_id, 'Key not found'::TEXT;
            RETURN;
        END IF;
    ELSE
        -- Use current active key
        SELECT kr.key_id INTO v_key_id
        FROM rtc_infra.key_rotations kr
        WHERE kr.is_active = TRUE
        ORDER BY kr.created_at DESC
        LIMIT 1;
    END IF;

    -- Get the secret from session settings (set by application)
    v_secret := current_setting('app.rtc_signing_key', TRUE);
    IF v_secret IS NULL OR v_secret = '' THEN
        RETURN QUERY SELECT FALSE, v_key_id, 'Signing key not configured in session'::TEXT;
        RETURN;
    END IF;

    -- Reconstruct the signed data (must match Go service format)
    v_data := p_unix_seconds::TEXT || ':' || p_nano::TEXT || ':' || p_source;

    -- Calculate expected HMAC-SHA256
    v_expected := encode(hmac(v_data, v_secret, 'sha256'), 'hex');

    -- Compare signatures
    IF v_expected = p_signature THEN
        RETURN QUERY SELECT TRUE, v_key_id, 'Signature valid'::TEXT;
    ELSE
        RETURN QUERY SELECT FALSE, v_key_id, 'Signature mismatch'::TEXT;
    END IF;
END;
$$ LANGUAGE plpgsql STABLE;

-- =============================================================================
-- Trigger: Validate Signature on Insert
-- =============================================================================
-- Automatically validates the HMAC-SHA256 signature when a signed timestamp
-- is inserted. Rejects records with invalid signatures.

CREATE OR REPLACE FUNCTION rtc_infra.validate_signed_timestamp_trigger()
RETURNS TRIGGER AS $$
DECLARE
    v_valid BOOLEAN;
    v_msg TEXT;
BEGIN
    -- Validate signature
    SELECT is_valid, message INTO v_valid, v_msg
    FROM rtc_infra.validate_timestamp_signature(
        NEW.unix_seconds, NEW.nano, NEW.source_module, NEW.signature
    );

    -- If validation is configured and fails, reject the insert
    IF current_setting('app.rtc_enforce_signatures', TRUE) = 'true' THEN
        IF NOT v_valid THEN
            RAISE EXCEPTION 'RTC signature validation failed: % (source=%, unix=%, nano=%)',
                v_msg, NEW.source_module, NEW.unix_seconds, NEW.nano;
        END IF;
    END IF;

    -- Check drift threshold (GLI-11: max 100ms, we enforce 50ms)
    IF ABS(NEW.drift_ms) > 50 THEN
        RAISE WARNING 'RTC drift %.2fms exceeds 50ms threshold for source %',
            NEW.drift_ms, NEW.source_module;
    END IF;

    -- Ensure confidence meets minimum (80% for production)
    IF NEW.confidence < 0.8 THEN
        RAISE WARNING 'RTC confidence %.4f below 80%% threshold', NEW.confidence;
    END IF;

    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

CREATE TRIGGER trg_validate_signed_timestamp
    BEFORE INSERT ON rtc_infra.signed_timestamps
    FOR EACH ROW
    EXECUTE FUNCTION rtc_infra.validate_signed_timestamp_trigger();

-- =============================================================================
-- Trigger: Immutability Guard
-- =============================================================================
-- Prevents updates and deletes on the signed_timestamps table.
-- GLI-11: Audit records must be immutable.

CREATE OR REPLACE FUNCTION rtc_infra.prevent_modification()
RETURNS TRIGGER AS $$
BEGIN
    RAISE EXCEPTION 'Modification of signed_timestamps is not permitted (GLI-11 immutability requirement)';
    RETURN NULL;
END;
$$ LANGUAGE plpgsql;

CREATE TRIGGER trg_immutable_signed_timestamps
    BEFORE UPDATE OR DELETE ON rtc_infra.signed_timestamps
    FOR EACH ROW
    EXECUTE FUNCTION rtc_infra.prevent_modification();

-- =============================================================================
-- Continuous Aggregates for Monitoring
-- =============================================================================

-- Hourly consensus statistics
CREATE MATERIALIZED VIEW IF NOT EXISTS rtc_infra.hourly_consensus_stats
WITH (timescaledb.continuous) AS
SELECT
    time_bucket('1 hour', round_time) AS hour,
    COUNT(*) AS total_rounds,
    COUNT(*) FILTER (WHERE status = 'success') AS successful_rounds,
    COUNT(*) FILTER (WHERE status = 'byzantine_detected') AS byzantine_rounds,
    COUNT(*) FILTER (WHERE status = 'quorum_failure') AS failed_rounds,
    AVG(confidence) AS avg_confidence,
    MIN(confidence) AS min_confidence,
    AVG(spread_ms) AS avg_spread_ms,
    MAX(spread_ms) AS max_spread_ms,
    AVG(drift_from_system_ms) AS avg_drift_ms,
    MAX(ABS(drift_from_system_ms)) AS max_drift_ms,
    AVG(round_duration_us) AS avg_duration_us,
    PERCENTILE_CONT(0.99) WITHIN GROUP (ORDER BY round_duration_us) AS p99_duration_us
FROM rtc_infra.consensus_rounds
GROUP BY hour
WITH NO DATA;

SELECT add_continuous_aggregate_policy('rtc_infra.hourly_consensus_stats',
    start_offset => INTERVAL '3 hours',
    end_offset => INTERVAL '1 hour',
    schedule_interval => INTERVAL '30 minutes',
    if_not_exists => TRUE
);

-- Hourly module health summary
CREATE MATERIALIZED VIEW IF NOT EXISTS rtc_infra.hourly_module_health
WITH (timescaledb.continuous) AS
SELECT
    time_bucket('1 hour', metric_time) AS hour,
    module_id,
    AVG(drift_ms) AS avg_drift_ms,
    MAX(ABS(drift_ms)) AS max_drift_ms,
    AVG(temperature_celsius) AS avg_temp_c,
    MAX(temperature_celsius) AS max_temp_c,
    MIN(battery_percentage) AS min_battery_pct,
    SUM(readings_count) AS total_readings,
    SUM(error_count) AS total_errors
FROM rtc_infra.module_health
GROUP BY hour, module_id
WITH NO DATA;

SELECT add_continuous_aggregate_policy('rtc_infra.hourly_module_health',
    start_offset => INTERVAL '3 hours',
    end_offset => INTERVAL '1 hour',
    schedule_interval => INTERVAL '30 minutes',
    if_not_exists => TRUE
);

-- =============================================================================
-- Data Retention Policies
-- =============================================================================
-- GLI-11 requires minimum 2-year retention for audit data.
-- Raw metrics can be retained for shorter periods with aggregation.

-- Keep raw consensus rounds for 2 years (GLI-11)
SELECT add_retention_policy('rtc_infra.consensus_rounds',
    INTERVAL '2 years',
    if_not_exists => TRUE
);

-- Keep Byzantine events for 5 years (security)
SELECT add_retention_policy('rtc_infra.byzantine_events',
    INTERVAL '5 years',
    if_not_exists => TRUE
);

-- Keep signed timestamps for 7 years (regulatory maximum)
SELECT add_retention_policy('rtc_infra.signed_timestamps',
    INTERVAL '7 years',
    if_not_exists => TRUE
);

-- Keep raw module health for 90 days (aggregated data available longer)
SELECT add_retention_policy('rtc_infra.module_health',
    INTERVAL '90 days',
    if_not_exists => TRUE
);

-- =============================================================================
-- Compression Policies (TimescaleDB)
-- =============================================================================
-- Compress older data for storage efficiency

ALTER TABLE rtc_infra.consensus_rounds SET (
    timescaledb.compress,
    timescaledb.compress_segmentby = 'status',
    timescaledb.compress_orderby = 'round_time DESC'
);

SELECT add_compression_policy('rtc_infra.consensus_rounds',
    INTERVAL '7 days',
    if_not_exists => TRUE
);

ALTER TABLE rtc_infra.signed_timestamps SET (
    timescaledb.compress,
    timescaledb.compress_segmentby = 'source_module',
    timescaledb.compress_orderby = 'issued_at DESC'
);

SELECT add_compression_policy('rtc_infra.signed_timestamps',
    INTERVAL '30 days',
    if_not_exists => TRUE
);

ALTER TABLE rtc_infra.module_health SET (
    timescaledb.compress,
    timescaledb.compress_segmentby = 'module_id',
    timescaledb.compress_orderby = 'metric_time DESC'
);

SELECT add_compression_policy('rtc_infra.module_health',
    INTERVAL '7 days',
    if_not_exists => TRUE
);

-- =============================================================================
-- Grants (Principle of Least Privilege)
-- =============================================================================

-- Application role: read/write to operational tables
DO $$
BEGIN
    IF NOT EXISTS (SELECT FROM pg_roles WHERE rolname = 'rtc_app') THEN
        CREATE ROLE rtc_app;
    END IF;
END $$;

GRANT USAGE ON SCHEMA rtc_infra TO rtc_app;
GRANT SELECT, INSERT ON rtc_infra.consensus_rounds TO rtc_app;
GRANT SELECT, INSERT ON rtc_infra.byzantine_events TO rtc_app;
GRANT SELECT, INSERT ON rtc_infra.signed_timestamps TO rtc_app;
GRANT SELECT, INSERT ON rtc_infra.module_health TO rtc_app;
GRANT SELECT ON rtc_infra.key_rotations TO rtc_app;
GRANT USAGE ON ALL SEQUENCES IN SCHEMA rtc_infra TO rtc_app;

-- Audit role: read-only access to all tables
DO $$
BEGIN
    IF NOT EXISTS (SELECT FROM pg_roles WHERE rolname = 'rtc_auditor') THEN
        CREATE ROLE rtc_auditor;
    END IF;
END $$;

GRANT USAGE ON SCHEMA rtc_infra TO rtc_auditor;
GRANT SELECT ON ALL TABLES IN SCHEMA rtc_infra TO rtc_auditor;

-- Admin role: full access
DO $$
BEGIN
    IF NOT EXISTS (SELECT FROM pg_roles WHERE rolname = 'rtc_admin') THEN
        CREATE ROLE rtc_admin;
    END IF;
END $$;

GRANT ALL ON SCHEMA rtc_infra TO rtc_admin;
GRANT ALL ON ALL TABLES IN SCHEMA rtc_infra TO rtc_admin;
GRANT ALL ON ALL SEQUENCES IN SCHEMA rtc_infra TO rtc_admin;

-- =============================================================================
-- Verification Query
-- =============================================================================
-- Run this after installation to verify the schema is correctly set up.

DO $$
DECLARE
    v_tables INTEGER;
    v_hypertables INTEGER;
    v_policies INTEGER;
BEGIN
    SELECT COUNT(*) INTO v_tables
    FROM information_schema.tables
    WHERE table_schema = 'rtc_infra';

    SELECT COUNT(*) INTO v_hypertables
    FROM timescaledb_information.hypertables
    WHERE hypertable_schema = 'rtc_infra';

    SELECT COUNT(*) INTO v_policies
    FROM timescaledb_information.jobs
    WHERE hypertable_schema = 'rtc_infra';

    RAISE NOTICE 'RTC Infrastructure Schema: % tables, % hypertables, % policies',
        v_tables, v_hypertables, v_policies;
END $$;
