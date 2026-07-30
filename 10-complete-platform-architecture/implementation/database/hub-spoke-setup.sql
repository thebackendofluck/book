-- Chapter 10 - Hub-and-Spoke Database Pattern for iGambling Platform
--
-- Architecture:
--   Hub DB (central): Shared reference data, cross-domain queries
--   Spoke DBs (per domain): Domain-owned data with full autonomy
--   Read Replicas: Per-spoke replicas for analytics and reporting
--
-- PRODUCTION DEPLOYMENT NOTE:
--   In production this script would be split across 5 separate databases
--   (casino_hub, casino_payments, casino_games, casino_players, casino_compliance),
--   each provisioned independently with logical replication between them.
--   The \c switching and CREATE SUBSCRIPTION / CREATE PUBLICATION commands
--   in a multi-DB setup require pg_partman extension and superuser privileges.
--
--   For single-instance testing (this file), all schemas are created inside
--   one database using namespaced schemas that mirror the per-DB layout.
--   Partitioning is implemented with native PostgreSQL PARTITION BY RANGE;
--   pg_partman (auto-partition management) is noted where it would be used.
--
-- Prerequisites (production):
--   PostgreSQL 16+ with wal_level = logical
--   pg_partman extension for automated partition creation
--   pgcrypto for column-level encryption
--   max_replication_slots >= 10, max_wal_senders >= 10
--
-- Single-instance test usage:
--   psql -U betbr_user -d dge_test -f hub-spoke-setup.sql

-- ============================================================
-- SETUP: drop schemas if re-running
-- ============================================================

DROP SCHEMA IF EXISTS reference_data  CASCADE;
DROP SCHEMA IF EXISTS cross_domain    CASCADE;
DROP SCHEMA IF EXISTS payments        CASCADE;
DROP SCHEMA IF EXISTS games           CASCADE;
DROP SCHEMA IF EXISTS players         CASCADE;
DROP SCHEMA IF EXISTS compliance      CASCADE;

-- ============================================================
-- SECTION 1: HUB DATABASE (Central Reference Data)
-- ============================================================
-- Production: \c casino_hub
-- Here: schema reference_data mirrors the hub database

CREATE EXTENSION IF NOT EXISTS "uuid-ossp";
CREATE EXTENSION IF NOT EXISTS "pgcrypto";
CREATE EXTENSION IF NOT EXISTS "pg_stat_statements";

CREATE SCHEMA IF NOT EXISTS reference_data;
CREATE SCHEMA IF NOT EXISTS cross_domain;

-- Jurisdictions and regulatory configuration (read by all spokes)
CREATE TABLE reference_data.jurisdictions (
    jurisdiction_id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    code VARCHAR(10) NOT NULL UNIQUE,
    name VARCHAR(200) NOT NULL,
    country_code CHAR(2) NOT NULL,
    regulatory_body VARCHAR(200) NOT NULL,
    license_required BOOLEAN DEFAULT TRUE,
    data_retention_years INT DEFAULT 5,
    tax_rate_ggr DECIMAL(5,4),
    tax_rate_nrr DECIMAL(5,4),
    aml_threshold_eur DECIMAL(12,2),
    kyc_level_required VARCHAR(20) DEFAULT 'enhanced',
    responsible_gaming_required BOOLEAN DEFAULT TRUE,
    is_active BOOLEAN DEFAULT TRUE,
    config_json JSONB DEFAULT '{}',
    created_at TIMESTAMPTZ DEFAULT NOW(),
    updated_at TIMESTAMPTZ DEFAULT NOW()
);

-- Currency configuration
CREATE TABLE reference_data.currencies (
    currency_id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    code CHAR(3) NOT NULL UNIQUE,
    name VARCHAR(100) NOT NULL,
    symbol VARCHAR(5),
    decimal_places INT DEFAULT 2,
    is_crypto BOOLEAN DEFAULT FALSE,
    min_deposit DECIMAL(12,2),
    max_deposit DECIMAL(12,2),
    min_withdrawal DECIMAL(12,2),
    max_withdrawal DECIMAL(12,2),
    is_active BOOLEAN DEFAULT TRUE,
    created_at TIMESTAMPTZ DEFAULT NOW()
);

-- Game catalog (shared across game engine and casino platform)
CREATE TABLE reference_data.game_catalog (
    game_id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    provider_id UUID NOT NULL,
    external_game_id VARCHAR(100) NOT NULL,
    name VARCHAR(200) NOT NULL,
    category VARCHAR(50) NOT NULL,
    subcategory VARCHAR(50),
    rtp_theoretical DECIMAL(6,4),
    volatility VARCHAR(20),
    max_win_multiplier DECIMAL(10,2),
    min_bet_eur DECIMAL(8,2),
    max_bet_eur DECIMAL(10,2),
    is_live BOOLEAN DEFAULT FALSE,
    mobile_compatible BOOLEAN DEFAULT TRUE,
    jurisdictions_allowed TEXT[] DEFAULT '{}',
    jurisdictions_blocked TEXT[] DEFAULT '{}',
    launch_date DATE,
    is_active BOOLEAN DEFAULT TRUE,
    metadata JSONB DEFAULT '{}',
    created_at TIMESTAMPTZ DEFAULT NOW(),
    updated_at TIMESTAMPTZ DEFAULT NOW(),
    UNIQUE(provider_id, external_game_id)
);

-- Game providers
CREATE TABLE reference_data.game_providers (
    provider_id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    code VARCHAR(50) NOT NULL UNIQUE,
    name VARCHAR(200) NOT NULL,
    api_base_url VARCHAR(500),
    integration_type VARCHAR(30) DEFAULT 'seamless',
    wallet_type VARCHAR(20) DEFAULT 'seamless',
    supported_currencies TEXT[] DEFAULT '{}',
    is_active BOOLEAN DEFAULT TRUE,
    config JSONB DEFAULT '{}',
    created_at TIMESTAMPTZ DEFAULT NOW()
);

-- PSP (Payment Service Provider) configuration
CREATE TABLE reference_data.psp_config (
    psp_id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    code VARCHAR(50) NOT NULL UNIQUE,
    name VARCHAR(200) NOT NULL,
    priority INT DEFAULT 100,
    supported_methods TEXT[] DEFAULT '{}',
    supported_currencies TEXT[] DEFAULT '{}',
    supported_jurisdictions TEXT[] DEFAULT '{}',
    min_deposit DECIMAL(12,2),
    max_deposit DECIMAL(12,2),
    fee_percent DECIMAL(5,4),
    fee_fixed DECIMAL(8,2),
    settlement_days INT DEFAULT 2,
    is_active BOOLEAN DEFAULT TRUE,
    failover_psp_id UUID REFERENCES reference_data.psp_config(psp_id),
    config JSONB DEFAULT '{}',
    created_at TIMESTAMPTZ DEFAULT NOW()
);

-- Seed hub reference data
INSERT INTO reference_data.jurisdictions
    (code, name, country_code, regulatory_body, tax_rate_ggr, aml_threshold_eur)
VALUES
    ('MGA',  'Malta Gaming Authority',        'MT', 'MGA',  0.0500, 10000.00),
    ('UKGC', 'UK Gambling Commission',        'GB', 'UKGC', 0.1500, 10000.00),
    ('CUR',  'Curaçao eGaming',              'CW', 'CUR',  0.0200, 15000.00),
    ('SGA',  'Swedish Gambling Authority',    'SE', 'SGA',  0.1800, 10000.00);

INSERT INTO reference_data.currencies (code, name, symbol, decimal_places)
VALUES
    ('EUR', 'Euro',            '€', 2),
    ('GBP', 'British Pound',   '£', 2),
    ('SEK', 'Swedish Krona',   'kr', 2),
    ('USD', 'US Dollar',       '$', 2),
    ('BTC', 'Bitcoin',         '₿', 8);

INSERT INTO reference_data.game_providers (code, name, integration_type)
VALUES
    ('EVOLUTION', 'Evolution Gaming', 'seamless'),
    ('NETENT',    'NetEnt',           'seamless'),
    ('PLAYNGO',   'Play''n GO',       'seamless');

INSERT INTO reference_data.psp_config
    (code, name, priority, supported_methods, supported_currencies, supported_jurisdictions,
     min_deposit, max_deposit, fee_percent)
VALUES
    ('stripe', 'Stripe',    10,
     ARRAY['card','bank_transfer'], ARRAY['EUR','GBP','USD'],
     ARRAY['MGA','UKGC'], 10.00, 50000.00, 0.0140),
    ('adyen',  'Adyen',     20,
     ARRAY['card','pix'],           ARRAY['EUR','GBP','SEK'],
     ARRAY['MGA','UKGC','SGA'], 5.00, 100000.00, 0.0120);

-- Cross-domain materialized view
CREATE MATERIALIZED VIEW cross_domain.jurisdiction_summary AS
SELECT
    j.code AS jurisdiction,
    j.name AS jurisdiction_name,
    j.tax_rate_ggr,
    (SELECT COUNT(*) FROM reference_data.game_catalog g
     WHERE j.code = ANY(g.jurisdictions_allowed) AND g.is_active)
    AS active_games,
    (SELECT COUNT(*) FROM reference_data.psp_config p
     WHERE j.code = ANY(p.supported_jurisdictions) AND p.is_active)
    AS active_psps
FROM reference_data.jurisdictions j
WHERE j.is_active = TRUE
WITH DATA;

CREATE UNIQUE INDEX ON cross_domain.jurisdiction_summary (jurisdiction);

-- Production logical replication (commented out — requires superuser and separate DBs):
-- CREATE PUBLICATION hub_reference_pub FOR TABLE
--     reference_data.jurisdictions,
--     reference_data.currencies,
--     reference_data.game_catalog,
--     reference_data.game_providers,
--     reference_data.psp_config;


-- ============================================================
-- SECTION 2: SPOKE - PAYMENT PROCESSING
-- ============================================================
-- Production: \c casino_payments
-- Production: CREATE EXTENSION pg_partman; (auto partition management)
-- Production: CREATE SUBSCRIPTION payment_hub_sub CONNECTION '...' PUBLICATION hub_reference_pub;
-- Here: schema payments mirrors casino_payments database

CREATE SCHEMA IF NOT EXISTS payments;

-- Transactions table — partitioned by month
-- Production: partman.create_parent() would auto-create monthly child partitions
-- Here: two representative monthly partitions cover the test period
CREATE TABLE payments.transactions (
    transaction_id UUID NOT NULL DEFAULT uuid_generate_v4(),
    player_id UUID NOT NULL,
    type VARCHAR(20) NOT NULL,
    status VARCHAR(30) NOT NULL DEFAULT 'pending',
    amount DECIMAL(14,2) NOT NULL,
    currency CHAR(3) NOT NULL,
    amount_eur DECIMAL(14,2),
    psp_code VARCHAR(50) NOT NULL,
    psp_reference VARCHAR(200),
    payment_method VARCHAR(50),
    card_last4 VARCHAR(4),
    card_brand VARCHAR(20),
    ip_address INET,
    jurisdiction_code VARCHAR(10),
    risk_score DECIMAL(5,2),
    aml_check_status VARCHAR(20) DEFAULT 'pending',
    metadata JSONB DEFAULT '{}',
    error_code VARCHAR(50),
    error_message TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    completed_at TIMESTAMPTZ,
    PRIMARY KEY (transaction_id, created_at)
) PARTITION BY RANGE (created_at);

-- Monthly partitions (production: pg_partman creates these automatically with p_premake=24)
CREATE TABLE payments.transactions_2026_02
    PARTITION OF payments.transactions
    FOR VALUES FROM ('2026-02-01') TO ('2026-03-01');

CREATE TABLE payments.transactions_2026_03
    PARTITION OF payments.transactions
    FOR VALUES FROM ('2026-03-01') TO ('2026-04-01');

-- Wallet balances
CREATE TABLE payments.wallet_balances (
    wallet_id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    player_id UUID NOT NULL,
    currency CHAR(3) NOT NULL,
    balance_real DECIMAL(14,2) NOT NULL DEFAULT 0.00,
    balance_bonus DECIMAL(14,2) NOT NULL DEFAULT 0.00,
    balance_locked DECIMAL(14,2) NOT NULL DEFAULT 0.00,
    last_deposit_at TIMESTAMPTZ,
    last_withdrawal_at TIMESTAMPTZ,
    version BIGINT NOT NULL DEFAULT 0,
    updated_at TIMESTAMPTZ DEFAULT NOW(),
    UNIQUE(player_id, currency),
    CONSTRAINT positive_balance CHECK (balance_real >= 0),
    CONSTRAINT positive_bonus CHECK (balance_bonus >= 0)
);

-- Wallet ledger (append-only audit trail) — partitioned by month
CREATE TABLE payments.wallet_ledger (
    ledger_id BIGSERIAL,
    wallet_id UUID NOT NULL REFERENCES payments.wallet_balances(wallet_id),
    transaction_id UUID,
    entry_type VARCHAR(30) NOT NULL,
    amount DECIMAL(14,2) NOT NULL,
    balance_after DECIMAL(14,2) NOT NULL,
    reference_type VARCHAR(30),
    reference_id UUID,
    description TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    PRIMARY KEY (ledger_id, created_at)
) PARTITION BY RANGE (created_at);

CREATE TABLE payments.wallet_ledger_2026_02
    PARTITION OF payments.wallet_ledger
    FOR VALUES FROM ('2026-02-01') TO ('2026-03-01');

CREATE TABLE payments.wallet_ledger_2026_03
    PARTITION OF payments.wallet_ledger
    FOR VALUES FROM ('2026-03-01') TO ('2026-04-01');

-- PSP failover tracking
CREATE TABLE payments.psp_failover_log (
    id BIGSERIAL PRIMARY KEY,
    transaction_id UUID NOT NULL,
    from_psp VARCHAR(50) NOT NULL,
    to_psp VARCHAR(50) NOT NULL,
    reason VARCHAR(100),
    attempt_number INT NOT NULL DEFAULT 1,
    created_at TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX idx_transactions_player ON payments.transactions (player_id, created_at DESC);
CREATE INDEX idx_transactions_status ON payments.transactions (status, created_at DESC);
CREATE INDEX idx_transactions_psp    ON payments.transactions (psp_code, created_at DESC);
CREATE INDEX idx_wallet_player       ON payments.wallet_balances (player_id);
CREATE INDEX idx_ledger_wallet       ON payments.wallet_ledger (wallet_id, created_at DESC);

-- Production read-replica publication (requires superuser):
-- CREATE PUBLICATION payment_analytics_pub FOR TABLE
--     payments.transactions, payments.wallet_balances, payments.wallet_ledger;


-- ============================================================
-- SECTION 3: SPOKE - GAME ENGINE
-- ============================================================
-- Production: \c casino_games
-- Production: CREATE EXTENSION pg_partman;
-- Production: CREATE SUBSCRIPTION game_hub_sub CONNECTION '...' PUBLICATION hub_reference_pub;

CREATE SCHEMA IF NOT EXISTS games;

-- Game rounds (high-volume — partitioned by day in production, by month here)
CREATE TABLE games.rounds (
    round_id UUID NOT NULL DEFAULT uuid_generate_v4(),
    session_id UUID NOT NULL,
    player_id UUID NOT NULL,
    game_id UUID NOT NULL,
    provider_id UUID NOT NULL,
    status VARCHAR(20) NOT NULL DEFAULT 'active',
    bet_amount DECIMAL(14,2) NOT NULL,
    win_amount DECIMAL(14,2) DEFAULT 0.00,
    currency CHAR(3) NOT NULL,
    rng_seed BYTEA,
    rng_result JSONB,
    game_state JSONB DEFAULT '{}',
    jurisdiction_code VARCHAR(10),
    started_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    completed_at TIMESTAMPTZ,
    PRIMARY KEY (round_id, started_at)
) PARTITION BY RANGE (started_at);

-- Production: pg_partman creates daily partitions (p_interval='daily', p_premake=90)
CREATE TABLE games.rounds_2026_02
    PARTITION OF games.rounds
    FOR VALUES FROM ('2026-02-01') TO ('2026-03-01');

CREATE TABLE games.rounds_2026_03
    PARTITION OF games.rounds
    FOR VALUES FROM ('2026-03-01') TO ('2026-04-01');

-- Player sessions
CREATE TABLE games.sessions (
    session_id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    player_id UUID NOT NULL,
    game_id UUID NOT NULL,
    status VARCHAR(20) DEFAULT 'active',
    ip_address INET,
    device_type VARCHAR(20),
    jurisdiction_code VARCHAR(10),
    total_bets DECIMAL(14,2) DEFAULT 0.00,
    total_wins DECIMAL(14,2) DEFAULT 0.00,
    round_count INT DEFAULT 0,
    started_at TIMESTAMPTZ DEFAULT NOW(),
    last_activity_at TIMESTAMPTZ DEFAULT NOW(),
    ended_at TIMESTAMPTZ
);

-- RNG audit log (immutable, regulatory requirement) — partitioned by month
CREATE TABLE games.rng_audit_log (
    audit_id BIGSERIAL,
    round_id UUID NOT NULL,
    algorithm VARCHAR(50) NOT NULL DEFAULT 'AES-256-CTR',
    seed_hash VARCHAR(128) NOT NULL,
    server_seed_encrypted BYTEA NOT NULL,
    client_seed VARCHAR(64),
    nonce BIGINT,
    result_hash VARCHAR(128) NOT NULL,
    verified BOOLEAN DEFAULT FALSE,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    PRIMARY KEY (audit_id, created_at)
) PARTITION BY RANGE (created_at);

CREATE TABLE games.rng_audit_log_2026_02
    PARTITION OF games.rng_audit_log
    FOR VALUES FROM ('2026-02-01') TO ('2026-03-01');

CREATE TABLE games.rng_audit_log_2026_03
    PARTITION OF games.rng_audit_log
    FOR VALUES FROM ('2026-03-01') TO ('2026-04-01');

CREATE INDEX idx_rounds_player   ON games.rounds (player_id, started_at DESC);
CREATE INDEX idx_rounds_game     ON games.rounds (game_id, started_at DESC);
CREATE INDEX idx_sessions_player ON games.sessions (player_id, started_at DESC);
CREATE INDEX idx_rng_round       ON games.rng_audit_log (round_id);

-- Production: CREATE PUBLICATION game_analytics_pub FOR TABLE
--     games.rounds, games.sessions, games.rng_audit_log;


-- ============================================================
-- SECTION 4: SPOKE - PLAYER MANAGEMENT
-- ============================================================
-- Production: \c casino_players
-- Production: CREATE SUBSCRIPTION player_hub_sub CONNECTION '...' PUBLICATION hub_reference_pub;

CREATE SCHEMA IF NOT EXISTS players;

-- Player accounts (PII encrypted at column level with pgcrypto in production)
CREATE TABLE players.accounts (
    player_id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    username VARCHAR(50) NOT NULL UNIQUE,
    email_hash VARCHAR(128) NOT NULL,
    email_encrypted BYTEA NOT NULL,
    phone_hash VARCHAR(128),
    phone_encrypted BYTEA,
    first_name_encrypted BYTEA,
    last_name_encrypted BYTEA,
    date_of_birth_encrypted BYTEA,
    country_code CHAR(2) NOT NULL,
    jurisdiction_code VARCHAR(10) NOT NULL,
    kyc_status VARCHAR(20) DEFAULT 'pending',
    kyc_level INT DEFAULT 0,
    aml_risk_level VARCHAR(10) DEFAULT 'low',
    account_status VARCHAR(20) DEFAULT 'active',
    self_excluded BOOLEAN DEFAULT FALSE,
    self_exclusion_until TIMESTAMPTZ,
    deposit_limit_daily DECIMAL(12,2),
    deposit_limit_weekly DECIMAL(12,2),
    deposit_limit_monthly DECIMAL(12,2),
    loss_limit_daily DECIMAL(12,2),
    session_limit_minutes INT,
    reality_check_minutes INT DEFAULT 60,
    marketing_consent BOOLEAN DEFAULT FALSE,
    terms_accepted_at TIMESTAMPTZ,
    last_login_at TIMESTAMPTZ,
    registration_ip INET,
    registration_source VARCHAR(50),
    created_at TIMESTAMPTZ DEFAULT NOW(),
    updated_at TIMESTAMPTZ DEFAULT NOW()
);

-- KYC documents
CREATE TABLE players.kyc_documents (
    document_id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    player_id UUID NOT NULL REFERENCES players.accounts(player_id),
    document_type VARCHAR(30) NOT NULL,
    status VARCHAR(20) DEFAULT 'pending',
    provider_reference VARCHAR(200),
    verification_result JSONB,
    submitted_at TIMESTAMPTZ DEFAULT NOW(),
    verified_at TIMESTAMPTZ,
    expires_at DATE,
    reviewer_notes TEXT
);

-- Player activity summary
CREATE TABLE players.activity_summary (
    player_id UUID NOT NULL REFERENCES players.accounts(player_id),
    period_date DATE NOT NULL,
    total_deposits DECIMAL(14,2) DEFAULT 0,
    total_withdrawals DECIMAL(14,2) DEFAULT 0,
    total_bets DECIMAL(14,2) DEFAULT 0,
    total_wins DECIMAL(14,2) DEFAULT 0,
    net_position DECIMAL(14,2) DEFAULT 0,
    session_count INT DEFAULT 0,
    total_session_minutes INT DEFAULT 0,
    round_count INT DEFAULT 0,
    PRIMARY KEY (player_id, period_date)
);

CREATE INDEX idx_accounts_jurisdiction ON players.accounts (jurisdiction_code);
CREATE INDEX idx_accounts_kyc          ON players.accounts (kyc_status);
CREATE INDEX idx_accounts_risk         ON players.accounts (aml_risk_level);
CREATE INDEX idx_kyc_player            ON players.kyc_documents (player_id);
CREATE INDEX idx_activity_player       ON players.activity_summary (player_id, period_date DESC);

-- Production: CREATE PUBLICATION player_analytics_pub FOR TABLE
--     players.accounts, players.kyc_documents, players.activity_summary;


-- ============================================================
-- SECTION 5: SPOKE - COMPLIANCE
-- ============================================================
-- Production: \c casino_compliance
-- Production: CREATE EXTENSION pg_partman;
-- Production: CREATE SUBSCRIPTION compliance_hub_sub CONNECTION '...' PUBLICATION hub_reference_pub;

CREATE SCHEMA IF NOT EXISTS compliance;

-- Audit trail (immutable, append-only) — partitioned by month
CREATE TABLE compliance.audit_trail (
    audit_id BIGSERIAL,
    event_type VARCHAR(50) NOT NULL,
    source_service VARCHAR(50) NOT NULL,
    source_namespace VARCHAR(50),
    player_id UUID,
    entity_type VARCHAR(50),
    entity_id UUID,
    action VARCHAR(30) NOT NULL,
    old_value JSONB,
    new_value JSONB,
    ip_address INET,
    user_agent TEXT,
    jurisdiction_code VARCHAR(10),
    correlation_id UUID,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    PRIMARY KEY (audit_id, created_at)
) PARTITION BY RANGE (created_at);

CREATE TABLE compliance.audit_trail_2026_02
    PARTITION OF compliance.audit_trail
    FOR VALUES FROM ('2026-02-01') TO ('2026-03-01');

CREATE TABLE compliance.audit_trail_2026_03
    PARTITION OF compliance.audit_trail
    FOR VALUES FROM ('2026-03-01') TO ('2026-04-01');

-- AML alerts
CREATE TABLE compliance.aml_alerts (
    alert_id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    player_id UUID NOT NULL,
    alert_type VARCHAR(50) NOT NULL,
    risk_score DECIMAL(5,2) NOT NULL,
    trigger_rule VARCHAR(100),
    details JSONB NOT NULL,
    status VARCHAR(20) DEFAULT 'open',
    assigned_to VARCHAR(100),
    resolution TEXT,
    jurisdiction_code VARCHAR(10),
    created_at TIMESTAMPTZ DEFAULT NOW(),
    resolved_at TIMESTAMPTZ
);

-- Regulatory reports
CREATE TABLE compliance.regulatory_reports (
    report_id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    jurisdiction_code VARCHAR(10) NOT NULL,
    report_type VARCHAR(50) NOT NULL,
    period_start DATE NOT NULL,
    period_end DATE NOT NULL,
    status VARCHAR(20) DEFAULT 'draft',
    report_data JSONB NOT NULL,
    file_path TEXT,
    submitted_at TIMESTAMPTZ,
    acknowledged_at TIMESTAMPTZ,
    created_at TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX idx_audit_player       ON compliance.audit_trail (player_id, created_at DESC);
CREATE INDEX idx_audit_entity       ON compliance.audit_trail (entity_type, entity_id, created_at DESC);
CREATE INDEX idx_audit_source       ON compliance.audit_trail (source_service, created_at DESC);
CREATE INDEX idx_aml_player         ON compliance.aml_alerts (player_id);
CREATE INDEX idx_aml_status         ON compliance.aml_alerts (status, created_at DESC);
CREATE INDEX idx_reports_jurisdiction ON compliance.regulatory_reports (jurisdiction_code, period_start);


-- ============================================================
-- SECTION 6: VALIDATION
-- ============================================================

-- Verify all schemas and tables were created
SELECT schemaname, tablename
FROM pg_tables
WHERE schemaname IN ('reference_data','cross_domain','payments','games','players','compliance')
ORDER BY schemaname, tablename;

-- Verify partitioned tables have child partitions
SELECT parent.relname AS parent_table,
       child.relname  AS partition,
       pg_get_expr(child.relpartbound, child.oid) AS bounds
FROM pg_inherits
JOIN pg_class parent ON pg_inherits.inhparent = parent.oid
JOIN pg_class child  ON pg_inherits.inhrelid  = child.oid
JOIN pg_namespace ns ON parent.relnamespace   = ns.oid
WHERE ns.nspname IN ('payments','games','compliance')
ORDER BY parent_table, partition;

-- Verify hub reference data
SELECT 'jurisdictions' AS table_name, COUNT(*) FROM reference_data.jurisdictions
UNION ALL
SELECT 'currencies',    COUNT(*) FROM reference_data.currencies
UNION ALL
SELECT 'game_providers',COUNT(*) FROM reference_data.game_providers
UNION ALL
SELECT 'psp_config',    COUNT(*) FROM reference_data.psp_config;

-- Verify materialized view
SELECT * FROM cross_domain.jurisdiction_summary;

-- ============================================================
-- SECTION 7: PRODUCTION REPLICATION NOTES
-- ============================================================

-- postgresql.conf (on each DB server):
--   wal_level = logical
--   max_replication_slots = 10
--   max_wal_senders = 10
--   wal_keep_size = 1GB

-- pg_hba.conf:
--   host replication replication_user 10.0.0.0/8 scram-sha-256

-- Replication user (run on hub as superuser):
--   CREATE ROLE replication_user WITH LOGIN REPLICATION PASSWORD 'CHANGE_ME';
--   GRANT USAGE ON SCHEMA reference_data TO replication_user;
--   GRANT SELECT ON ALL TABLES IN SCHEMA reference_data TO replication_user;

-- Spoke subscriptions (run on each spoke DB after hub publication is created):
--   CREATE SUBSCRIPTION payment_hub_sub
--     CONNECTION 'host=hub-db port=5432 dbname=casino_hub user=replication_user password=CHANGE_ME'
--     PUBLICATION hub_reference_pub
--     WITH (copy_data = true, create_slot = true);

-- Partition maintenance (pg_partman, run after extension installed):
--   SELECT partman.create_parent(
--       p_parent_table := 'payments.transactions',
--       p_control      := 'created_at',
--       p_type         := 'range',
--       p_interval     := 'monthly',
--       p_premake      := 24
--   );
-- (Repeat for wallet_ledger, games.rounds, games.rng_audit_log, compliance.audit_trail)

-- Analytics replica subscriptions (run on read replicas):
--   CREATE SUBSCRIPTION payment_analytics_sub
--     CONNECTION 'host=payment-db port=5432 dbname=casino_payments user=replication_user password=CHANGE_ME'
--     PUBLICATION payment_analytics_pub;
