-- KYC System Database Schema
-- PostgreSQL initialization script

-- Enable required extensions
CREATE EXTENSION IF NOT EXISTS "uuid-ossp";
CREATE EXTENSION IF NOT EXISTS "pgcrypto";
CREATE EXTENSION IF NOT EXISTS "pg_stat_statements";

-- Create enum types
CREATE TYPE document_type AS ENUM (
    'passport',
    'driver_license',
    'national_id',
    'utility_bill',
    'bank_statement',
    'tax_document'
);

CREATE TYPE risk_level AS ENUM (
    'low',
    'medium',
    'high',
    'critical'
);

CREATE TYPE verification_status AS ENUM (
    'pending',
    'processing',
    'completed',
    'failed',
    'manual_review',
    'rejected',
    'expired'
);

CREATE TYPE compliance_check_type AS ENUM (
    'sanctions',
    'pep',
    'aml',
    'gdpr',
    'kyc',
    'fraud'
);

-- Users table
CREATE TABLE users (
    user_id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    external_id VARCHAR(255) UNIQUE NOT NULL,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    personal_data JSONB,
    kyc_level INTEGER DEFAULT 0,
    risk_score DECIMAL(3,2) DEFAULT 0.00,
    is_verified BOOLEAN DEFAULT FALSE,
    is_deleted BOOLEAN DEFAULT FALSE,
    deleted_at TIMESTAMP,
    last_verification_at TIMESTAMP,
    metadata JSONB DEFAULT '{}'::jsonb
);

-- Documents table
CREATE TABLE documents (
    document_id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    user_id UUID REFERENCES users(user_id) ON DELETE CASCADE,
    document_type document_type NOT NULL,
    file_path VARCHAR(500),
    encrypted_path VARCHAR(500),
    file_hash VARCHAR(64),
    uploaded_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    processed_at TIMESTAMP,
    expires_at TIMESTAMP,
    is_deleted BOOLEAN DEFAULT FALSE,
    deleted_at TIMESTAMP,
    deletion_method VARCHAR(50),
    metadata JSONB DEFAULT '{}'::jsonb
);

CREATE INDEX idx_user_documents ON documents (user_id);
CREATE INDEX idx_document_expiry ON documents (expires_at);

-- Verification results table
CREATE TABLE verification_results (
    verification_id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    document_id UUID REFERENCES documents(document_id) ON DELETE CASCADE,
    user_id UUID REFERENCES users(user_id) ON DELETE CASCADE,
    verification_status verification_status NOT NULL,
    risk_level risk_level NOT NULL,
    risk_score DECIMAL(3,2) NOT NULL,
    security_score DECIMAL(3,2),
    biometric_score DECIMAL(3,2),
    extracted_data JSONB,
    fraud_signals TEXT[],
    processing_time_ms INTEGER,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    completed_at TIMESTAMP,
    reviewed_by VARCHAR(255),
    review_notes TEXT,
    metadata JSONB DEFAULT '{}'::jsonb
);

CREATE INDEX idx_user_verifications ON verification_results (user_id);
CREATE INDEX idx_verification_status ON verification_results (verification_status);
CREATE INDEX idx_risk_level ON verification_results (risk_level);

-- Biometric data table (encrypted)
CREATE TABLE biometric_data (
    biometric_id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    user_id UUID REFERENCES users(user_id) ON DELETE CASCADE,
    face_encoding BYTEA,
    face_quality_score DECIMAL(3,2),
    liveness_score DECIMAL(3,2),
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    expires_at TIMESTAMP,
    is_active BOOLEAN DEFAULT TRUE,
    metadata JSONB DEFAULT '{}'::jsonb
);

CREATE INDEX idx_user_biometrics ON biometric_data (user_id);

-- Compliance checks table
CREATE TABLE compliance_checks (
    check_id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    user_id UUID REFERENCES users(user_id) ON DELETE CASCADE,
    check_type compliance_check_type NOT NULL,
    check_result JSONB NOT NULL,
    risk_score DECIMAL(3,2),
    performed_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    performed_by VARCHAR(255),
    metadata JSONB DEFAULT '{}'::jsonb
);

CREATE INDEX idx_user_compliance ON compliance_checks (user_id);
CREATE INDEX idx_check_type ON compliance_checks (check_type);
CREATE INDEX idx_check_date ON compliance_checks (performed_at);

-- Suspicious Activity Reports (SARs)
CREATE TABLE suspicious_activity_reports (
    sar_id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    report_number VARCHAR(50) UNIQUE NOT NULL,
    user_id UUID REFERENCES users(user_id),
    transaction_id VARCHAR(255),
    filing_date TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    suspicion_reason TEXT NOT NULL,
    narrative TEXT NOT NULL,
    amount DECIMAL(15,2),
    currency VARCHAR(3),
    submitted_to VARCHAR(100),
    submission_date TIMESTAMP,
    status VARCHAR(50) DEFAULT 'pending',
    metadata JSONB DEFAULT '{}'::jsonb
);

CREATE INDEX idx_sar_user ON suspicious_activity_reports (user_id);
CREATE INDEX idx_sar_date ON suspicious_activity_reports (filing_date);

-- Encryption keys table
CREATE TABLE encryption_keys (
    key_id VARCHAR(255) PRIMARY KEY,
    key_type VARCHAR(50) NOT NULL,
    algorithm VARCHAR(50) NOT NULL,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    rotated_at TIMESTAMP,
    expires_at TIMESTAMP NOT NULL,
    is_active BOOLEAN DEFAULT TRUE,
    version INTEGER DEFAULT 1,
    metadata JSONB DEFAULT '{}'::jsonb
);

CREATE INDEX idx_active_keys ON encryption_keys (is_active);
CREATE INDEX idx_key_expiry ON encryption_keys (expires_at);

-- Audit log table
CREATE TABLE audit_log (
    log_id BIGSERIAL PRIMARY KEY,
    timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    user_id UUID,
    action VARCHAR(100) NOT NULL,
    resource_type VARCHAR(50),
    resource_id VARCHAR(255),
    ip_address INET,
    user_agent TEXT,
    details JSONB
);

CREATE INDEX idx_audit_timestamp ON audit_log (timestamp);
CREATE INDEX idx_audit_user ON audit_log (user_id);
CREATE INDEX idx_audit_action ON audit_log (action);

-- Deletion audit log
CREATE TABLE deletion_audit_log (
    deletion_id BIGSERIAL PRIMARY KEY,
    timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    file_path VARCHAR(500),
    reason VARCHAR(100),
    file_size BIGINT,
    status VARCHAR(50),
    completed_at TIMESTAMP,
    error_message TEXT,
    metadata JSONB DEFAULT '{}'::jsonb
);

CREATE INDEX idx_deletion_timestamp ON deletion_audit_log (timestamp);
CREATE INDEX idx_deletion_status ON deletion_audit_log (status);

-- Transaction monitoring table
CREATE TABLE transaction_monitoring (
    monitoring_id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    user_id UUID REFERENCES users(user_id),
    transaction_id VARCHAR(255) NOT NULL,
    transaction_type VARCHAR(50),
    amount DECIMAL(15,2) NOT NULL,
    currency VARCHAR(3) NOT NULL,
    source_account VARCHAR(255),
    destination_account VARCHAR(255),
    transaction_date TIMESTAMP NOT NULL,
    risk_indicators TEXT[],
    aml_score DECIMAL(3,2),
    requires_review BOOLEAN DEFAULT FALSE,
    reviewed_at TIMESTAMP,
    reviewed_by VARCHAR(255),
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    metadata JSONB DEFAULT '{}'::jsonb
);

CREATE INDEX idx_transaction_user ON transaction_monitoring (user_id);
CREATE INDEX idx_transaction_date ON transaction_monitoring (transaction_date);
CREATE INDEX idx_transaction_review ON transaction_monitoring (requires_review);

-- GDPR requests table
CREATE TABLE gdpr_requests (
    request_id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    user_id UUID REFERENCES users(user_id),
    request_type VARCHAR(50) NOT NULL,
    requested_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    processed_at TIMESTAMP,
    status VARCHAR(50) DEFAULT 'pending',
    processor VARCHAR(255),
    result JSONB,
    metadata JSONB DEFAULT '{}'::jsonb
);

CREATE INDEX idx_gdpr_user ON gdpr_requests (user_id);
CREATE INDEX idx_gdpr_status ON gdpr_requests (status);

-- Device fingerprints table
CREATE TABLE device_fingerprints (
    fingerprint_id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    user_id UUID REFERENCES users(user_id),
    device_hash VARCHAR(64) NOT NULL,
    browser_info JSONB,
    os_info JSONB,
    hardware_info JSONB,
    first_seen TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    last_seen TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    risk_score DECIMAL(3,2),
    is_trusted BOOLEAN DEFAULT FALSE,
    metadata JSONB DEFAULT '{}'::jsonb
);

CREATE INDEX idx_device_user ON device_fingerprints (user_id);
CREATE INDEX idx_device_hash ON device_fingerprints (device_hash);

-- Session management table
CREATE TABLE user_sessions (
    session_id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    user_id UUID REFERENCES users(user_id),
    device_fingerprint_id UUID REFERENCES device_fingerprints(fingerprint_id),
    ip_address INET NOT NULL,
    user_agent TEXT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    expires_at TIMESTAMP NOT NULL,
    is_active BOOLEAN DEFAULT TRUE,
    terminated_at TIMESTAMP,
    termination_reason VARCHAR(100),
    metadata JSONB DEFAULT '{}'::jsonb
);

CREATE INDEX idx_session_user ON user_sessions (user_id);
CREATE INDEX idx_session_active ON user_sessions (is_active);
CREATE INDEX idx_session_expiry ON user_sessions (expires_at);

-- Sanctions lists cache
CREATE TABLE sanctions_cache (
    entry_id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    list_name VARCHAR(100) NOT NULL,
    entity_name VARCHAR(500) NOT NULL,
    entity_type VARCHAR(50),
    aliases TEXT[],
    date_of_birth DATE,
    countries TEXT[],
    addresses JSONB,
    identifiers JSONB,
    programs TEXT[],
    remarks TEXT,
    list_date DATE,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    is_active BOOLEAN DEFAULT TRUE
);

CREATE INDEX idx_sanctions_name ON sanctions_cache (entity_name);
CREATE INDEX idx_sanctions_list ON sanctions_cache (list_name);
CREATE INDEX idx_sanctions_active ON sanctions_cache (is_active);

-- PEP database cache
CREATE TABLE pep_cache (
    pep_id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    full_name VARCHAR(500) NOT NULL,
    date_of_birth DATE,
    countries TEXT[],
    positions JSONB,
    risk_level VARCHAR(50),
    start_date DATE,
    end_date DATE,
    is_current BOOLEAN DEFAULT TRUE,
    related_persons JSONB,
    source VARCHAR(100),
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX idx_pep_name ON pep_cache (full_name);
CREATE INDEX idx_pep_current ON pep_cache (is_current);

-- Create functions for automatic timestamp updates
CREATE OR REPLACE FUNCTION update_updated_at_column()
RETURNS TRIGGER AS $$
BEGIN
    NEW.updated_at = CURRENT_TIMESTAMP;
    RETURN NEW;
END;
$$ language 'plpgsql';

-- Add triggers for updated_at columns
CREATE TRIGGER update_users_updated_at BEFORE UPDATE ON users
    FOR EACH ROW EXECUTE FUNCTION update_updated_at_column();

CREATE TRIGGER update_sanctions_cache_updated_at BEFORE UPDATE ON sanctions_cache
    FOR EACH ROW EXECUTE FUNCTION update_updated_at_column();

CREATE TRIGGER update_pep_cache_updated_at BEFORE UPDATE ON pep_cache
    FOR EACH ROW EXECUTE FUNCTION update_updated_at_column();

-- Create function for GDPR data anonymization
CREATE OR REPLACE FUNCTION anonymize_user_data(target_user_id UUID)
RETURNS VOID AS $$
BEGIN
    -- Anonymize personal data
    UPDATE users
    SET personal_data = jsonb_build_object('anonymized', true),
        is_deleted = true,
        deleted_at = CURRENT_TIMESTAMP
    WHERE user_id = target_user_id;

    -- Delete biometric data
    DELETE FROM biometric_data WHERE user_id = target_user_id;

    -- Anonymize audit logs
    UPDATE audit_log
    SET details = jsonb_build_object('anonymized', true)
    WHERE user_id = target_user_id;

    -- Mark documents for deletion
    UPDATE documents
    SET is_deleted = true,
        deleted_at = CURRENT_TIMESTAMP
    WHERE user_id = target_user_id;
END;
$$ LANGUAGE plpgsql;

-- Create materialized view for risk analytics
CREATE MATERIALIZED VIEW risk_analytics AS
SELECT
    u.user_id,
    u.risk_score as user_risk_score,
    COUNT(DISTINCT d.document_id) as total_documents,
    COUNT(DISTINCT vr.verification_id) as total_verifications,
    AVG(vr.risk_score) as avg_verification_risk,
    COUNT(DISTINCT cc.check_id) as total_compliance_checks,
    COUNT(DISTINCT sar.sar_id) as total_sars,
    MAX(vr.created_at) as last_verification_date,
    CASE
        WHEN COUNT(DISTINCT sar.sar_id) > 0 THEN 'high'
        WHEN AVG(vr.risk_score) > 0.7 THEN 'medium'
        ELSE 'low'
    END as risk_category
FROM users u
LEFT JOIN documents d ON u.user_id = d.user_id
LEFT JOIN verification_results vr ON u.user_id = vr.user_id
LEFT JOIN compliance_checks cc ON u.user_id = cc.user_id
LEFT JOIN suspicious_activity_reports sar ON u.user_id = sar.user_id
WHERE u.is_deleted = false
GROUP BY u.user_id, u.risk_score;

-- Create index on materialized view
CREATE INDEX idx_risk_analytics_user ON risk_analytics(user_id);
CREATE INDEX idx_risk_analytics_category ON risk_analytics(risk_category);

-- Grant permissions (adjust as needed)
-- Create application role if it doesn't exist
DO $$ BEGIN
    CREATE ROLE kyc_app_user;
EXCEPTION WHEN duplicate_object THEN NULL;
END $$;
GRANT ALL PRIVILEGES ON ALL TABLES IN SCHEMA public TO kyc_app_user;
GRANT ALL PRIVILEGES ON ALL SEQUENCES IN SCHEMA public TO kyc_app_user;
GRANT EXECUTE ON ALL FUNCTIONS IN SCHEMA public TO kyc_app_user;

-- Initial data insertion for testing
INSERT INTO users (external_id, kyc_level, risk_score, is_verified)
VALUES
    ('TEST_USER_001', 1, 0.15, false),
    ('TEST_USER_002', 2, 0.45, true),
    ('TEST_USER_003', 0, 0.00, false)
ON CONFLICT (external_id) DO NOTHING;

-- Note: Partitioned tables for high-volume data (optional)
-- audit_log can be converted to a partitioned table by month in production.
-- To enable partitioning, recreate audit_log with PARTITION BY RANGE (timestamp)
-- and then create monthly child partitions as shown below (example only):
-- CREATE TABLE audit_log_2024_01 PARTITION OF audit_log
--     FOR VALUES FROM ('2024-01-01') TO ('2024-02-01');
-- CREATE TABLE audit_log_2024_02 PARTITION OF audit_log
--     FOR VALUES FROM ('2024-02-01') TO ('2024-03-01');

-- Add comments for documentation
COMMENT ON TABLE users IS 'Main user table storing KYC verification status';
COMMENT ON TABLE documents IS 'Uploaded documents for KYC verification';
COMMENT ON TABLE verification_results IS 'Results of document verification processes';
COMMENT ON TABLE biometric_data IS 'Encrypted biometric data for face matching';
COMMENT ON TABLE suspicious_activity_reports IS 'SARs filed for regulatory compliance';
COMMENT ON COLUMN users.kyc_level IS 'KYC verification level: 0=none, 1=basic, 2=enhanced, 3=full';
COMMENT ON COLUMN documents.deletion_method IS 'Method used for secure deletion: DOD_5220_22_M, GUTMANN, etc';
