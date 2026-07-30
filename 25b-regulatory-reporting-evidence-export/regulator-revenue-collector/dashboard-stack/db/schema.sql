-- Time-series schema for the regulator-revenue dashboard.
--
-- Two tables:
--   collection_runs    one row per collector execution (state, run timestamp, totals)
--   report_snapshots   one row per (file × run); UPSERT by content hash so re-runs
--                      that fetch the same bytes don't bloat the table.
--
-- The dashboard API joins these to plot trends over time.

CREATE TABLE IF NOT EXISTS collection_runs (
    id              BIGSERIAL PRIMARY KEY,
    state           CHAR(2) NOT NULL,
    regulator       TEXT NOT NULL,
    source_url      TEXT NOT NULL,
    collected_at    TIMESTAMPTZ NOT NULL,
    report_count    INTEGER NOT NULL,
    success_count   INTEGER NOT NULL,
    failure_count   INTEGER NOT NULL,
    total_bytes     BIGINT  NOT NULL DEFAULT 0
);

CREATE INDEX IF NOT EXISTS idx_runs_state_collected
    ON collection_runs (state, collected_at DESC);

CREATE TABLE IF NOT EXISTS report_snapshots (
    id              BIGSERIAL PRIMARY KEY,
    run_id          BIGINT NOT NULL REFERENCES collection_runs(id) ON DELETE CASCADE,
    state           CHAR(2) NOT NULL,
    operator        TEXT NOT NULL,
    vertical        TEXT NOT NULL,
    cadence         TEXT NOT NULL,
    format          TEXT NOT NULL,
    source_url      TEXT NOT NULL,
    sha256          CHAR(64),
    size_bytes      INTEGER NOT NULL DEFAULT 0,
    fetch_error     TEXT,
    summary         JSONB NOT NULL DEFAULT '[]'::jsonb,
    retrieved_at    TIMESTAMPTZ
);

CREATE INDEX IF NOT EXISTS idx_snap_state_retrieved
    ON report_snapshots (state, retrieved_at DESC);
CREATE INDEX IF NOT EXISTS idx_snap_run_id
    ON report_snapshots (run_id);
CREATE INDEX IF NOT EXISTS idx_snap_vertical_cadence
    ON report_snapshots (state, vertical, cadence);
-- Idempotency guard: same file (by hash) for the same key once per run.
CREATE UNIQUE INDEX IF NOT EXISTS uq_snap_run_file
    ON report_snapshots (run_id, state, operator, cadence, format)
    WHERE sha256 IS NOT NULL;

-- ---------------------------------------------------------------------------
-- metric_facts: the $$$ layer.
-- One row per (state, operator, period, metric, vertical) — the canonical
-- normalised value parsed out of the source file. period is YYYY-MM.
-- value_usd_cents is the value in cents (BIGINT — handles state totals).
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS metric_facts (
    id              BIGSERIAL PRIMARY KEY,
    state           CHAR(2)      NOT NULL,
    operator        TEXT         NOT NULL,
    vertical        TEXT         NOT NULL,
    period          CHAR(7)      NOT NULL,    -- 'YYYY-MM'
    metric_name     TEXT         NOT NULL,    -- ggr | handle | hold | tax_paid | cash_won_by_patrons
    value_usd_cents BIGINT       NOT NULL,
    source_url      TEXT         NOT NULL,
    snapshot_id     BIGINT       REFERENCES report_snapshots(id) ON DELETE SET NULL,
    inserted_at     TIMESTAMPTZ  NOT NULL DEFAULT NOW()
);

-- Upsert key: same (state, operator, vertical, period, metric) — re-runs replace.
CREATE UNIQUE INDEX IF NOT EXISTS uq_fact
    ON metric_facts (state, operator, vertical, period, metric_name);
CREATE INDEX IF NOT EXISTS idx_fact_state_period
    ON metric_facts (state, period DESC);
CREATE INDEX IF NOT EXISTS idx_fact_metric_period
    ON metric_facts (metric_name, period DESC);
CREATE INDEX IF NOT EXISTS idx_fact_operator
    ON metric_facts (operator, period DESC);

