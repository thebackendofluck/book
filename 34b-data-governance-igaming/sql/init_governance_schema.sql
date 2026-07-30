CREATE SCHEMA IF NOT EXISTS governance;

CREATE TABLE IF NOT EXISTS governance.pii_findings (
    finding_id bigserial PRIMARY KEY,
    detected_at timestamptz NOT NULL DEFAULT now(),
    source_table text NOT NULL,
    source_column text NOT NULL,
    classifier text NOT NULL,
    confidence numeric(5,4) NOT NULL,
    evidence_hash text NOT NULL
);

CREATE TABLE IF NOT EXISTS governance.erasure_audit_log (
    erasure_id uuid PRIMARY KEY,
    player_id text NOT NULL,
    requested_at timestamptz NOT NULL,
    completed_at timestamptz,
    decision text NOT NULL,
    evidence jsonb NOT NULL DEFAULT '{}'::jsonb
);

CREATE TABLE IF NOT EXISTS governance.rtp_alerts (
    alert_id bigserial PRIMARY KEY,
    game_id text NOT NULL,
    jurisdiction text NOT NULL,
    provider text NOT NULL,
    observed_rtp numeric(8,4) NOT NULL,
    expected_rtp numeric(8,4) NOT NULL,
    created_at timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS governance.sar_trigger_log (
    trigger_id bigserial PRIMARY KEY,
    player_id text NOT NULL,
    risk_score numeric(8,4) NOT NULL,
    trigger_reason text NOT NULL,
    evidence jsonb NOT NULL DEFAULT '{}'::jsonb,
    created_at timestamptz NOT NULL DEFAULT now()
);

REVOKE UPDATE, DELETE ON ALL TABLES IN SCHEMA governance FROM PUBLIC;
