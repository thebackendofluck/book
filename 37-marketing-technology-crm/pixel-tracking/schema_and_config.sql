-- Chapter 09 - Marketing Technology & CRM
-- Server-to-Server Pixel Tracking: Database Schema
--
-- Three core tables support the tracking pipeline:
--   1. brand_configuration: per-brand rules mapping events to tracking URLs
--   2. tracker_request:     incoming events persisted for audit/replay
--   3. tracker_response:    HTTP responses from tracking partners
--
-- The configuration table is the heart of the system -- it determines which
-- tracking pixels fire for which brand/event/country/jurisdiction combination.

-- -----------------------------------------------------------------------
-- Brand-specific tracking configurations
-- Each row maps a (brand, event, country, jurisdiction, referrer) tuple
-- to a tracking URL template that will be invoked server-to-server.
-- -----------------------------------------------------------------------
CREATE TABLE brand_configuration (
    id             serial PRIMARY KEY,
    brand_id       int NOT NULL,
    event_type     varchar NOT NULL,        -- 'registration', 'activation', 'deposited'
    country        varchar,                  -- optional country filter (ISO 2-letter)
    jurisdiction   varchar,                  -- optional jurisdiction filter (e.g., 'UKGC', 'MGA')
    referrer       varchar,                  -- optional affiliate referrer filter
    url            varchar NOT NULL,         -- URL template with ${placeholders}
    body           text,                     -- optional POST body template
    method         varchar,                  -- HTTP method (GET/POST), defaults to GET
    deposit_number int,                      -- optional: fire only on Nth deposit
    clicks_only    boolean NOT NULL DEFAULT FALSE,  -- require click_id to be present
    disabled       boolean NOT NULL DEFAULT FALSE,
    template_type  varchar NOT NULL DEFAULT 'simple', -- 'simple', 'commons', 'groovy'
    mock           boolean NOT NULL DEFAULT FALSE,    -- dry-run mode for validation
    timestamp      timestamp NOT NULL
);

-- -----------------------------------------------------------------------
-- Persisted tracking requests (from Kafka events or manual triggers)
-- -----------------------------------------------------------------------
CREATE TABLE tracker_request (
    id               serial PRIMARY KEY,
    user_id          bigint NOT NULL,
    external_user_id varchar,
    brand_id         int NOT NULL,
    event_type       varchar NOT NULL,
    country          varchar NOT NULL,
    jurisdiction     varchar NOT NULL,
    referrer         varchar,
    currency         varchar,
    amount           double precision,
    deposit_number   int,
    click_id         varchar,
    param1           varchar,
    param2           varchar,
    param3           varchar,
    payment_id       varchar,
    trace_id         varchar,
    timestamp        timestamp NOT NULL,
    created_on       timestamp NOT NULL
);

-- -----------------------------------------------------------------------
-- Tracking responses from third-party pixel endpoints
-- -----------------------------------------------------------------------
CREATE TABLE tracker_response (
    id            serial PRIMARY KEY,
    request_id    int NOT NULL REFERENCES tracker_request ON DELETE CASCADE,
    config_id     int NOT NULL REFERENCES brand_configuration,
    url           varchar NOT NULL,       -- resolved URL (after template substitution)
    body          text,                   -- resolved POST body
    method        varchar,
    response_code int,                    -- HTTP status code from partner
    message       text NOT NULL,          -- response body or error message
    timestamp     timestamp NOT NULL
);

-- -----------------------------------------------------------------------
-- Indexes optimized for the configuration lookup query
-- -----------------------------------------------------------------------

-- Composite index for the multi-column filter in ConfigurationRepository.fetch()
CREATE INDEX brand_configuration_filter_idx
    ON brand_configuration (brand_id, upper(event_type), upper(referrer),
                           upper(country), upper(jurisdiction))
    WHERE disabled = FALSE;

-- Request pagination by brand
CREATE INDEX ON tracker_request (brand_id, id DESC);
