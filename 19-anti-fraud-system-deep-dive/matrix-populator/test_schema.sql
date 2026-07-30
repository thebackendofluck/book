-- Minimal schema reproducing the production tables the populator reads/writes.
-- Used only by tests; production schema is owned by platform migrations.

CREATE SCHEMA IF NOT EXISTS platform;

CREATE TABLE platform.user_info (
    userid    INTEGER PRIMARY KEY,
    country   CHAR(2) NOT NULL
);

CREATE TABLE platform.monthly_currencies (
    currency CHAR(3) NOT NULL,
    year     INTEGER  NOT NULL,
    month    INTEGER  NOT NULL,
    rate     NUMERIC(18,6) NOT NULL,
    PRIMARY KEY (currency, year, month)
);

CREATE TABLE platform.user_payments (
    id              BIGSERIAL PRIMARY KEY,
    user_id         INTEGER NOT NULL,
    amount          BIGINT  NOT NULL,        -- cents
    currency        CHAR(3) NOT NULL,
    status          VARCHAR(20) NOT NULL,    -- SUCCEEDED|FAILED
    failure_reason  VARCHAR(64),
    date_updated    TIMESTAMP NOT NULL
);

CREATE TABLE platform.responsible_gaming_actions (
    id           BIGSERIAL PRIMARY KEY,
    user_id      INTEGER NOT NULL,
    action_type  VARCHAR(64) NOT NULL
);

CREATE TABLE platform.responsible_gaming_audit (
    id          BIGSERIAL PRIMARY KEY,
    action_id   BIGINT NOT NULL REFERENCES platform.responsible_gaming_actions(id),
    audit_type  VARCHAR(64) NOT NULL,
    "timestamp" TIMESTAMP NOT NULL
);

CREATE TABLE platform.matrix_score_type (
    id              SERIAL PRIMARY KEY,
    label           VARCHAR(128) NOT NULL,
    calculate_on    VARCHAR(64)  NOT NULL,
    metric_period   VARCHAR(32)  NOT NULL,
    condition       TEXT         NOT NULL,
    score_value     INTEGER      NOT NULL
);

CREATE TABLE platform.user_matrix_score (
    user_id        INTEGER NOT NULL,
    score_type_id  INTEGER NOT NULL,
    "timestamp"    TIMESTAMP NOT NULL,
    comments       TEXT NOT NULL,
    PRIMARY KEY (user_id, score_type_id)
);
