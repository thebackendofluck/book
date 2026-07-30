-- =============================================================================
-- Migration 001: Initial RTC Timestamp Storage Schema
-- =============================================================================
-- Creates the foundational tables for RTC timestamp storage and validation.
-- This migration is idempotent and can be re-run safely.
--
-- Migration Tool: Compatible with Flyway, golang-migrate, or manual execution
-- Version: V001
-- Description: Initial RTC infrastructure schema
--
-- GLI-11: This migration establishes the immutable audit trail required by
-- Section 5.4.3 for all timestamped gaming operations.
-- =============================================================================

-- Migration metadata tracking
CREATE TABLE IF NOT EXISTS rtc_migrations (
    version INTEGER PRIMARY KEY,
    description TEXT NOT NULL,
    applied_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    applied_by TEXT NOT NULL DEFAULT current_user,
    checksum TEXT NOT NULL,
    execution_time_ms INTEGER
);

-- Check if this migration has already been applied
DO $$
BEGIN
    IF EXISTS (SELECT 1 FROM rtc_migrations WHERE version = 1) THEN
        RAISE NOTICE 'Migration V001 already applied, skipping';
        RETURN;
    END IF;

    RAISE NOTICE 'Applying Migration V001: Initial RTC infrastructure schema';
END $$;

-- =============================================================================
-- Begin Migration
-- =============================================================================
BEGIN;

-- Enable extensions
CREATE EXTENSION IF NOT EXISTS timescaledb CASCADE;
CREATE EXTENSION IF NOT EXISTS pgcrypto;
CREATE EXTENSION IF NOT EXISTS "uuid-ossp";

-- Create schema
CREATE SCHEMA IF NOT EXISTS rtc;

-- ---------------------------------------------------------------------------
-- Table: rtc.timestamp_requests
-- ---------------------------------------------------------------------------
-- Tracks every timestamp request for audit and performance analysis.
CREATE TABLE IF NOT EXISTS rtc.timestamp_requests (
    request_id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    received_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    client_id TEXT NOT NULL,
    client_ip INET,
    game_id TEXT,
    session_id UUID,
    event_type TEXT DEFAULT 'unspecified',
    precision TEXT DEFAULT 'nanoseconds',
    require_consensus BOOLEAN DEFAULT TRUE,
    response_time_us INTEGER,
    status TEXT DEFAULT 'success' CHECK (status IN (
        'success', 'error', 'timeout', 'rejected'
    )),
    error_message TEXT,
    metadata JSONB DEFAULT '{}'
);

-- Note: not a hypertable because we need UUID primary key
-- Partition by date range instead
CREATE INDEX IF NOT EXISTS idx_requests_time
    ON rtc.timestamp_requests (received_at DESC);
CREATE INDEX IF NOT EXISTS idx_requests_game
    ON rtc.timestamp_requests (game_id, received_at DESC)
    WHERE game_id IS NOT NULL;
CREATE INDEX IF NOT EXISTS idx_requests_session
    ON rtc.timestamp_requests (session_id, received_at DESC)
    WHERE session_id IS NOT NULL;
CREATE INDEX IF NOT EXISTS idx_requests_errors
    ON rtc.timestamp_requests (status, received_at DESC)
    WHERE status != 'success';

-- ---------------------------------------------------------------------------
-- Table: rtc.drift_measurements
-- ---------------------------------------------------------------------------
-- Stores periodic drift measurements for trend analysis and alerting.
CREATE TABLE IF NOT EXISTS rtc.drift_measurements (
    measured_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    module_id TEXT NOT NULL,
    drift_ms DOUBLE PRECISION NOT NULL,
    drift_direction TEXT CHECK (drift_direction IN ('ahead', 'behind', 'sync')),
    reference_source TEXT NOT NULL DEFAULT 'system_clock',
    temperature_c DOUBLE PRECISION,
    battery_pct DOUBLE PRECISION,
    correction_applied BOOLEAN DEFAULT FALSE,
    correction_ms DOUBLE PRECISION DEFAULT 0,
    PRIMARY KEY (module_id, measured_at)
);

SELECT create_hypertable('rtc.drift_measurements', 'measured_at',
    chunk_time_interval => INTERVAL '6 hours',
    if_not_exists => TRUE
);

CREATE INDEX IF NOT EXISTS idx_drift_module
    ON rtc.drift_measurements (module_id, measured_at DESC);
CREATE INDEX IF NOT EXISTS idx_drift_excessive
    ON rtc.drift_measurements (drift_ms, measured_at DESC)
    WHERE ABS(drift_ms) > 10;

-- ---------------------------------------------------------------------------
-- Table: rtc.calibration_records
-- ---------------------------------------------------------------------------
-- Records calibration events for regulatory compliance.
-- GLI-11: Calibration history must be maintained for the life of the device.
CREATE TABLE IF NOT EXISTS rtc.calibration_records (
    calibration_id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    module_id TEXT NOT NULL,
    calibration_date TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    calibration_type TEXT NOT NULL CHECK (calibration_type IN (
        'initial', 'scheduled', 'corrective', 'post_maintenance', 'pre_certification'
    )),
    drift_before_ms DOUBLE PRECISION NOT NULL,
    drift_after_ms DOUBLE PRECISION NOT NULL,
    aging_offset_before INTEGER,
    aging_offset_after INTEGER,
    temperature_c DOUBLE PRECISION,
    reference_source TEXT NOT NULL,
    technician_id TEXT,
    technician_name TEXT,
    notes TEXT,
    certificate_number TEXT,
    passed BOOLEAN NOT NULL DEFAULT TRUE,
    next_calibration_due TIMESTAMPTZ
);

CREATE INDEX IF NOT EXISTS idx_calibration_module
    ON rtc.calibration_records (module_id, calibration_date DESC);
CREATE INDEX IF NOT EXISTS idx_calibration_due
    ON rtc.calibration_records (next_calibration_due)
    WHERE next_calibration_due IS NOT NULL;

-- ---------------------------------------------------------------------------
-- Table: rtc.failover_events
-- ---------------------------------------------------------------------------
-- Records time source failover events.
CREATE TABLE IF NOT EXISTS rtc.failover_events (
    event_id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    event_time TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    previous_source TEXT NOT NULL,
    new_source TEXT NOT NULL,
    reason TEXT NOT NULL,
    drift_at_failover_ms DOUBLE PRECISION,
    auto_recovered BOOLEAN DEFAULT FALSE,
    recovery_time TIMESTAMPTZ,
    duration_seconds DOUBLE PRECISION,
    impact_assessment TEXT,
    metadata JSONB DEFAULT '{}'
);

CREATE INDEX IF NOT EXISTS idx_failover_time
    ON rtc.failover_events (event_time DESC);
CREATE INDEX IF NOT EXISTS idx_failover_unrecovered
    ON rtc.failover_events (auto_recovered, event_time DESC)
    WHERE auto_recovered = FALSE;

-- ---------------------------------------------------------------------------
-- Table: rtc.blockchain_anchors
-- ---------------------------------------------------------------------------
-- Records blockchain timestamp anchoring for non-repudiation.
CREATE TABLE IF NOT EXISTS rtc.blockchain_anchors (
    anchor_id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    anchored_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    chain TEXT NOT NULL DEFAULT 'polygon',
    transaction_hash TEXT NOT NULL UNIQUE,
    block_number BIGINT,
    block_hash TEXT,
    contract_address TEXT NOT NULL,
    timestamp_hash TEXT NOT NULL,
    timestamp_count INTEGER NOT NULL DEFAULT 1,
    gas_used BIGINT,
    gas_price_gwei DOUBLE PRECISION,
    status TEXT DEFAULT 'pending' CHECK (status IN (
        'pending', 'confirmed', 'failed', 'orphaned'
    )),
    confirmations INTEGER DEFAULT 0,
    metadata JSONB DEFAULT '{}'
);

CREATE INDEX IF NOT EXISTS idx_anchor_time
    ON rtc.blockchain_anchors (anchored_at DESC);
CREATE INDEX IF NOT EXISTS idx_anchor_chain
    ON rtc.blockchain_anchors (chain, anchored_at DESC);
CREATE INDEX IF NOT EXISTS idx_anchor_status
    ON rtc.blockchain_anchors (status)
    WHERE status != 'confirmed';

-- ---------------------------------------------------------------------------
-- Utility Functions
-- ---------------------------------------------------------------------------

-- Function: Calculate drift trend over a time window
CREATE OR REPLACE FUNCTION rtc.calculate_drift_trend(
    p_module_id TEXT,
    p_window INTERVAL DEFAULT '1 hour'
) RETURNS TABLE (
    avg_drift_ms DOUBLE PRECISION,
    max_drift_ms DOUBLE PRECISION,
    min_drift_ms DOUBLE PRECISION,
    stddev_drift_ms DOUBLE PRECISION,
    measurement_count BIGINT,
    trend_direction TEXT
) AS $$
BEGIN
    RETURN QUERY
    WITH measurements AS (
        SELECT drift_ms, measured_at
        FROM rtc.drift_measurements
        WHERE module_id = p_module_id
          AND measured_at > NOW() - p_window
        ORDER BY measured_at
    ),
    trend AS (
        SELECT
            AVG(drift_ms) AS avg_d,
            MAX(ABS(drift_ms)) AS max_d,
            MIN(ABS(drift_ms)) AS min_d,
            STDDEV(drift_ms) AS std_d,
            COUNT(*) AS cnt,
            CASE
                WHEN regr_slope(drift_ms, EXTRACT(EPOCH FROM measured_at)) > 0.001 THEN 'increasing'
                WHEN regr_slope(drift_ms, EXTRACT(EPOCH FROM measured_at)) < -0.001 THEN 'decreasing'
                ELSE 'stable'
            END AS direction
        FROM measurements
    )
    SELECT avg_d, max_d, min_d, std_d, cnt, direction FROM trend;
END;
$$ LANGUAGE plpgsql STABLE;

-- ---------------------------------------------------------------------------
-- Record Migration
-- ---------------------------------------------------------------------------
INSERT INTO rtc_migrations (version, description, checksum, execution_time_ms)
VALUES (
    1,
    'Initial RTC infrastructure schema',
    encode(digest('V001-initial-schema', 'sha256'), 'hex'),
    0
);

COMMIT;

-- =============================================================================
-- Post-Migration Verification
-- =============================================================================
DO $$
DECLARE
    v_table_count INTEGER;
BEGIN
    SELECT COUNT(*) INTO v_table_count
    FROM information_schema.tables
    WHERE table_schema = 'rtc';

    RAISE NOTICE 'Migration V001 complete: % tables created in rtc schema', v_table_count;
END $$;
