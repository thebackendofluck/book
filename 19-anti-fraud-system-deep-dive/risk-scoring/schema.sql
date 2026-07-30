-- schema.sql
-- Database schema for the multi-matrix risk scoring system.
--
-- Supports six risk matrices (RG, CIR, CRA, VIP, AFF, RGMX) with
-- configurable scoring rules, risk levels, and full audit trails.
--
-- Sanitized production schema from a real iGaming risk matrix system.

CREATE SCHEMA IF NOT EXISTS risk_matrix;
CREATE SCHEMA IF NOT EXISTS risk_alerting;

-- ============================================================
-- Matrix Configuration Tables
-- ============================================================

-- Defines the risk matrices available in the system
CREATE TABLE risk_matrix.score_matrix (
    id    VARCHAR(10) PRIMARY KEY,
    title VARCHAR(100) NOT NULL
);

-- Seed the standard gambling risk matrices
INSERT INTO risk_matrix.score_matrix (id, title) VALUES
    ('rg',   'Responsible Gambling'),
    ('cir',  'Customer Intelligence Response'),
    ('cra',  'Customer Risk Assessment'),
    ('vip',  'VIP Alert'),
    ('aff',  'Affordability'),
    ('rgmx', 'Real-time Gambling Matrix');

-- Risk levels with score thresholds per matrix
-- Example: RG Level 1 (Yellow) = score 8-19
CREATE TABLE risk_matrix.matrix_level (
    score_matrix_id VARCHAR(10) REFERENCES risk_matrix.score_matrix(id),
    level_number    INT NOT NULL,
    label           VARCHAR(50) NOT NULL,
    colour          VARCHAR(20) NOT NULL,
    min_score       INT NOT NULL,
    max_score       INT NOT NULL,
    rg_message      VARCHAR(50),
    PRIMARY KEY (score_matrix_id, level_number)
);

-- Example level configuration for the RG matrix
INSERT INTO risk_matrix.matrix_level (score_matrix_id, level_number, label, colour, min_score, max_score, rg_message) VALUES
    ('rg', 1, 'Level 1 - Monitor',   'yellow', 8,  19, 'rg1'),
    ('rg', 2, 'Level 2 - Interact',  'orange', 20, 39, 'rg2'),
    ('rg', 3, 'Level 3 - Intervene', 'red',    40, 99, 'rg3');

-- Configurable scoring rules evaluated by the MatrixScorer engine
-- Each rule specifies: event trigger, time window, Groovy condition, score value
CREATE TABLE risk_matrix.matrix_score_type (
    score_matrix_id            VARCHAR(10) REFERENCES risk_matrix.score_matrix(id),
    id                         SERIAL PRIMARY KEY,
    label                      VARCHAR(200) NOT NULL,
    calculate_on               VARCHAR(50) NOT NULL,    -- event type trigger
    metric_period              INTERVAL NOT NULL,        -- time window for metrics
    condition                  TEXT NOT NULL,             -- Groovy expression
    score_value                INT NOT NULL,              -- points added on match
    gid_based                  BOOLEAN DEFAULT FALSE,     -- use global ID
    resettable                 BOOLEAN DEFAULT FALSE,     -- can be reset by admin
    rg_score_type              VARCHAR(10),               -- RG2/RG3 classification
    group_id                   INT,                       -- grouping for CRA/AFF
    jurisdiction_id            VARCHAR(10),               -- jurisdiction filter
    propagate_globally         BOOLEAN DEFAULT FALSE,     -- propagate across accounts
    flag_condition             TEXT,                       -- platform flag condition
    triggered_interaction_type VARCHAR(50),                -- auto-create interaction
    triggered_alert_type       VARCHAR(50),                -- auto-send alert
    bespoke_interaction        TEXT                        -- custom interaction config
);

-- Example scoring rules
INSERT INTO risk_matrix.matrix_score_type
    (score_matrix_id, label, calculate_on, metric_period, condition, score_value, resettable) VALUES
    ('rg', '3 payment options created within 7 days',
     'deposit-confirmed', '7 days', 'paymentOptionsCreated >= 3', 3, TRUE),
    ('rg', 'Total deposits exceed 7500 GBP in 90 days',
     'daily-stats', '90 days', 'depositTotal >= 7500', 5, TRUE),
    ('rg', 'Player under 25 with 12+ active periods and twilight play',
     'daily-stats', '30 days', 'activePeriods > 12 AND age < 25 AND twilightPeriods > 0', 4, TRUE),
    ('cir', '20+ declined deposits in 24 hours',
     'deposit-declined', '1 day', 'depositsDeclined >= 20', 10, FALSE),
    ('cra', 'Net losses in top 50th percentile',
     'daily-stats', '365 days', 'netLossesTop50 == true', 8, FALSE),
    ('aff', 'Cash hold exceeds 5000 GBP in 30 days',
     'daily-stats', '30 days', 'cashHold >= 5000', 6, FALSE);

-- Links matrices to jurisdictions (UKGC, MGA, etc.)
CREATE TABLE risk_matrix.score_matrix_jurisdiction (
    score_matrix_id VARCHAR(10) REFERENCES risk_matrix.score_matrix(id),
    jurisdiction_id VARCHAR(10) NOT NULL,
    PRIMARY KEY (score_matrix_id, jurisdiction_id)
);

INSERT INTO risk_matrix.score_matrix_jurisdiction (score_matrix_id, jurisdiction_id) VALUES
    ('rg',   'ukgc'),
    ('rg',   'mga'),
    ('cir',  'ukgc'),
    ('cra',  'ukgc'),
    ('aff',  'ukgc'),
    ('rgmx', 'mga');

-- ============================================================
-- User Data Tables
-- ============================================================

CREATE TABLE risk_matrix.user_details (
    user_id      BIGINT PRIMARY KEY,
    global_id    BIGINT NOT NULL,
    dob          DATE,
    country      VARCHAR(5),
    currency     VARCHAR(5) NOT NULL,
    jurisdiction VARCHAR(10) NOT NULL
);

-- Aggregated daily statistics per user (from data pipeline)
CREATE TABLE risk_matrix.user_daily_stats (
    user_id                          BIGINT NOT NULL,
    on_date                          TIMESTAMP NOT NULL,
    active_periods                   INT DEFAULT 0,
    cash_hold                        DOUBLE PRECISION DEFAULT 0,
    deposit_total                    DOUBLE PRECISION DEFAULT 0,
    deposit_count                    INT DEFAULT 0,
    deposit_top_50                   BOOLEAN,
    net_losses_top_50                BOOLEAN,
    net_deposits                     DOUBLE PRECISION DEFAULT 0,
    twilight_periods                 INT DEFAULT 0,
    longest_active_periods_in_session INT DEFAULT 0,
    longest_daily_session_in_hours   INT DEFAULT 0,
    churn                            DOUBLE PRECISION,
    yearly_losses                    DOUBLE PRECISION DEFAULT 0,
    PRIMARY KEY (user_id, on_date)
);

-- Raw user events for event-based scoring
CREATE TABLE risk_matrix.user_event (
    user_id    BIGINT NOT NULL,
    event_type VARCHAR(50) NOT NULL,
    event_time TIMESTAMP NOT NULL,
    value      TEXT
);

CREATE INDEX idx_user_event_lookup ON risk_matrix.user_event (user_id, event_type, event_time);

-- ============================================================
-- Scoring Results Tables
-- ============================================================

-- Individual score entries per user per rule
CREATE TABLE risk_matrix.user_matrix_score (
    user_id       BIGINT NOT NULL,
    score_type_id INT REFERENCES risk_matrix.matrix_score_type(id),
    timestamp     TIMESTAMP NOT NULL DEFAULT NOW(),
    comments      TEXT,
    last_audit_id BIGINT,
    last_update   TIMESTAMP,
    active        BOOLEAN DEFAULT TRUE,
    disabled      BOOLEAN DEFAULT FALSE,
    PRIMARY KEY (user_id, score_type_id)
);

-- Score change audit trail
CREATE TABLE risk_matrix.user_matrix_score_audit (
    id            BIGSERIAL PRIMARY KEY,
    user_id       BIGINT NOT NULL,
    score_type_id INT NOT NULL,
    timestamp     TIMESTAMP NOT NULL DEFAULT NOW(),
    comments      TEXT,
    admin_user_id INT DEFAULT -1
);

-- Current risk levels per user per matrix
CREATE TABLE risk_matrix.user_matrix_level (
    user_id         BIGINT NOT NULL,
    score_matrix_id VARCHAR(10) REFERENCES risk_matrix.score_matrix(id),
    level_number    INT NOT NULL,
    timestamp       TIMESTAMP NOT NULL DEFAULT NOW(),
    level_delta     INT NOT NULL,
    admin_user_id   INT,
    active          BOOLEAN DEFAULT TRUE,
    last_audit_id   BIGINT
);

CREATE INDEX idx_user_matrix_level_lookup
    ON risk_matrix.user_matrix_level (user_id, score_matrix_id, timestamp);

-- Administrative action audit trail
CREATE TABLE risk_matrix.user_matrix_audit (
    id            BIGSERIAL PRIMARY KEY,
    user_id       BIGINT NOT NULL,
    timestamp     TIMESTAMP NOT NULL DEFAULT NOW(),
    action        VARCHAR(50) NOT NULL,
    admin_user_id INT NOT NULL,
    comments      TEXT
);

-- ============================================================
-- Messaging Tables (Transactional Outbox Pattern)
-- ============================================================

CREATE TABLE risk_matrix.event_outbox (
    id         BIGSERIAL PRIMARY KEY,
    topic      VARCHAR(100) NOT NULL,
    key        VARCHAR(100),
    payload    TEXT NOT NULL,
    created_at TIMESTAMP NOT NULL DEFAULT NOW(),
    sent_at    TIMESTAMP
);

CREATE TABLE risk_matrix.message_topic_offset (
    topic      VARCHAR(100) PRIMARY KEY,
    partition  INT NOT NULL,
    offset_val BIGINT NOT NULL
);

-- ============================================================
-- Risk Alerting Tables (for the Kafka Streams alerting system)
-- ============================================================

CREATE TABLE risk_alerting.alert_descriptions (
    alert_name  VARCHAR(100) PRIMARY KEY,
    description TEXT NOT NULL,
    active      BOOLEAN DEFAULT TRUE,
    priority    VARCHAR(5) DEFAULT 'P5'   -- P1 through P5
);

-- Seed alert type descriptions
INSERT INTO risk_alerting.alert_descriptions (alert_name, description, active, priority) VALUES
    ('TotalAmountOfDepositsIn24Hours', 'Total deposits exceed threshold in 24h window', TRUE, 'P3'),
    ('Last5DepositsDeclined', 'Last 5 consecutive deposits were declined', TRUE, 'P3'),
    ('HighDepositor', 'First-time deposit >= $5000', TRUE, 'P2'),
    ('Last3CardDepositsDeclined', 'Last 3 card deposits declined', TRUE, 'P4'),
    ('SharedPaymentMethodsByTwoUsers', 'Two users sharing same payment method', TRUE, 'P2'),
    ('DeletingPaymentAccountsPerWeek', 'Multiple payment accounts deleted in a week', TRUE, 'P3'),
    ('FiveUniqueInstrumentsIn20MinutesDeclined', '5 unique instruments declined in 20 min', TRUE, 'P2'),
    ('FirstDepositDeclined', 'Very first deposit attempt declined', TRUE, 'P4'),
    ('TotalDepositsIn3Days', 'Total deposits in 3 days exceed threshold', TRUE, 'P3'),
    ('DepositMethodsAbuseWithin1Hour', 'Deposit method abuse within 1 hour', TRUE, 'P2'),
    ('TotalWithdrawalExceeded9000In72Hours', 'Total withdrawals > 9000 in 72 hours', TRUE, 'P2');

CREATE TABLE risk_alerting.alerts (
    id         UUID PRIMARY KEY,
    alert_name VARCHAR(100) NOT NULL,
    message    TEXT NOT NULL,
    details    TEXT,
    user_id    VARCHAR(50) NOT NULL,
    priority   VARCHAR(5) DEFAULT 'P5',
    status     VARCHAR(20) DEFAULT 'NEW',  -- NEW, IN_PROGRESS, RESOLVED, DISMISSED
    agent_id   VARCHAR(50),
    comment    TEXT,
    updated    TIMESTAMP NOT NULL DEFAULT NOW(),
    created    TIMESTAMP NOT NULL DEFAULT NOW()
);

CREATE INDEX idx_alerts_user_status ON risk_alerting.alerts (user_id, status, alert_name);

CREATE TABLE risk_alerting.alert_history (
    alert_id UUID REFERENCES risk_alerting.alerts(id),
    user_id  VARCHAR(50) NOT NULL,
    status   VARCHAR(20) NOT NULL,
    agent_id VARCHAR(50),
    comment  TEXT,
    created  TIMESTAMP NOT NULL DEFAULT NOW()
);
