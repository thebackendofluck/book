#!/usr/bin/env bash
# Companion code for "The Backend of Luck" - Chapter 19, Anti-Fraud System Deep Dive.
# https://thebackendofluck.com | https://github.com/thebackendofluck/book
# SPDX-License-Identifier: Apache-2.0
#
# FOR TESTING AND EVALUATION ONLY. NOT FOR PRODUCTION USE.
# Published to demonstrate the patterns explained in the book. This code is
# not certified for real-money gaming: operating a gambling platform requires
# your own licence, independent test-lab certification (GLI, eCOGRA or
# equivalent) and regulator approval.

# fraud-event-pusher.sh — Push the last 60 seconds of wallet events to the Fraud API.
#
# Runs as a cron job on the production server (203.0.113.1) every 60 seconds.
# Design constraint: zero Kafka dependency on the production machine.  One SQL
# query, one HTTPS POST, one Redis write.  If ops-host is unreachable the cron
# exits cleanly; the fraud pipeline catches up when connectivity is restored.
#
# Data flow:
#   PostgreSQL wallet_events (last 60s, max 1000 rows)
#     → HTTPS POST to Fraud API (203.0.113.2:443 → pfSense NAT → ops-host:8180)
#       → Fraud API returns stats (event_count, alert_count, mean_risk_score)
#         → Redis key fraud:status (read by live dashboard every 30s)
#
# TLS: The Fraud API presents a certificate signed by the internal OpenBao CA.
# This script pins that CA via CURL_CA_BUNDLE so validation is not bypassed.
#
# Installation (crontab -e on 203.0.113.1):
#   * * * * * /opt/casino/bin/fraud-event-pusher.sh >> /var/log/fraud-pusher.log 2>&1
#
# Environment / configuration:
#   Override via environment or edit the defaults below.
#   Never commit credentials — source from /etc/casino/fraud-pusher.env.
#
# Reference: Chapter 19 — Anti-Fraud System Deep Dive / Level 1 Cron + HTTPS
set -euo pipefail

# ---------------------------------------------------------------------------
# Configuration — override via environment or /etc/casino/fraud-pusher.env
# ---------------------------------------------------------------------------

CONF_FILE="${FRAUD_PUSHER_CONF:-/etc/casino/fraud-pusher.env}"
# shellcheck source=/dev/null
[[ -f "$CONF_FILE" ]] && source "$CONF_FILE"

# PostgreSQL connection
PG_HOST="${PG_HOST:-127.0.0.1}"
PG_PORT="${PG_PORT:-5432}"
PG_DB="${PG_DB:-casino_production}"
PG_USER="${PG_USER:-casino_readonly}"
PG_PASS="${PG_PASS:-}"              # Set via PGPASSWORD or .pgpass

# Fraud API endpoint (pfSense NAT: 203.0.113.2:443 → ops-host:nginx:8180)
FRAUD_API_URL="${FRAUD_API_URL:-https://203.0.113.2/api/v1/events/batch}"
FRAUD_API_KEY="${FRAUD_API_KEY:-}"  # Bearer token for Fraud API authentication
FRAUD_CA_BUNDLE="${FRAUD_CA_BUNDLE:-/etc/casino/tls/ops-host-internal-ca.crt}"

# Redis (on 127.0.0.1)
REDIS_HOST="${REDIS_HOST:-127.0.0.1}"
REDIS_PORT="${REDIS_PORT:-6381}"
REDIS_KEY="${REDIS_KEY:-fraud:status}"
REDIS_PASS="${REDIS_PASS:-}"        # Empty means no auth

# Query window and batch limit
LOOKBACK_SECONDS="${LOOKBACK_SECONDS:-60}"
MAX_ROWS="${MAX_ROWS:-1000}"

# Timeouts (seconds)
CURL_CONNECT_TIMEOUT="${CURL_CONNECT_TIMEOUT:-5}"
CURL_MAX_TIME="${CURL_MAX_TIME:-20}"

# Logging
LOG_LEVEL="${LOG_LEVEL:-info}"      # debug | info | warn | error
SCRIPT_NAME="fraud-event-pusher"

# ---------------------------------------------------------------------------
# Logging helpers
# ---------------------------------------------------------------------------

_ts() { date -u +"%Y-%m-%dT%H:%M:%SZ"; }

log_info()  { echo "$(_ts) [$SCRIPT_NAME] INFO  $*"; }
log_warn()  { echo "$(_ts) [$SCRIPT_NAME] WARN  $*" >&2; }
log_error() { echo "$(_ts) [$SCRIPT_NAME] ERROR $*" >&2; }
log_debug() { [[ "${LOG_LEVEL}" == "debug" ]] && echo "$(_ts) [$SCRIPT_NAME] DEBUG $*" || true; }

# ---------------------------------------------------------------------------
# Dependency check
# ---------------------------------------------------------------------------

_require() {
    local cmd="$1"
    if ! command -v "$cmd" &>/dev/null; then
        log_error "Required command not found: $cmd"
        exit 1
    fi
}

_require psql
_require curl
_require jq
_require redis-cli

# ---------------------------------------------------------------------------
# Step 1: Query wallet_events for the last LOOKBACK_SECONDS
# ---------------------------------------------------------------------------

log_info "Starting push cycle (lookback=${LOOKBACK_SECONDS}s, max_rows=${MAX_ROWS})"

# Build the SQL query.  The created_at index (idx_wallet_events_created_at)
# makes this a fast index scan even on 2.9M+ row tables.
SQL=$(cat <<'SQL_EOF'
SELECT json_agg(row_to_json(e)) AS batch
FROM (
    SELECT
        id::text            AS event_id,
        player_id::text     AS player_id,
        event_type          AS event_type,
        amount              AS amount,
        currency            AS currency,
        status              AS status,
        ip_address          AS ip_address,
        device_fingerprint  AS device_fingerprint,
        session_id          AS session_id,
        created_at          AS created_at,
        metadata            AS metadata
    FROM wallet_events
    WHERE created_at >= NOW() - (CURRENT_SETTING('fraud.lookback_seconds')::int * INTERVAL '1 second')
    ORDER BY created_at ASC
    LIMIT CURRENT_SETTING('fraud.max_rows')::int
) e
SQL_EOF
)

log_debug "Executing SQL query against ${PG_DB}@${PG_HOST}:${PG_PORT}"

export PGPASSWORD="$PG_PASS"
BATCH_JSON=$(psql \
    --host="$PG_HOST" \
    --port="$PG_PORT" \
    --username="$PG_USER" \
    --dbname="$PG_DB" \
    --tuples-only \
    --no-align \
    --command="SET LOCAL fraud.lookback_seconds = ${LOOKBACK_SECONDS}; SET LOCAL fraud.max_rows = ${MAX_ROWS}; ${SQL}" \
    2>/dev/null)

# psql returns NULL (literal string) when no rows match
if [[ -z "$BATCH_JSON" || "$BATCH_JSON" == "NULL" ]]; then
    log_info "No wallet events in the last ${LOOKBACK_SECONDS}s — nothing to push"
    exit 0
fi

EVENT_COUNT=$(echo "$BATCH_JSON" | jq 'length' 2>/dev/null || echo 0)
log_info "Fetched ${EVENT_COUNT} event(s) from wallet_events"

# ---------------------------------------------------------------------------
# Step 2: POST the batch to the Fraud API
# ---------------------------------------------------------------------------

# Build the request payload with metadata
PUSH_TIMESTAMP=$(_ts)
REQUEST_PAYLOAD=$(jq -nc \
    --argjson events "$BATCH_JSON" \
    --arg source "production" \
    --arg pushed_at "$PUSH_TIMESTAMP" \
    --argjson lookback "$LOOKBACK_SECONDS" \
    '{
        source:    $source,
        pushed_at: $pushed_at,
        lookback_seconds: $lookback,
        events:    $events
    }')

# Determine CA bundle option — if the cert file exists, pin it; otherwise fall
# back to system CA bundle (logs a warning).
CURL_CA_OPT=()
if [[ -f "$FRAUD_CA_BUNDLE" ]]; then
    CURL_CA_OPT=(--cacert "$FRAUD_CA_BUNDLE")
    log_debug "Using pinned CA bundle: $FRAUD_CA_BUNDLE"
else
    log_warn "CA bundle not found at ${FRAUD_CA_BUNDLE} — using system CA store"
fi

AUTH_HEADER=()
if [[ -n "$FRAUD_API_KEY" ]]; then
    AUTH_HEADER=(--header "Authorization: Bearer ${FRAUD_API_KEY}")
fi

log_debug "POST ${FRAUD_API_URL}"

HTTP_RESPONSE=$(curl \
    --silent \
    --show-error \
    --connect-timeout "$CURL_CONNECT_TIMEOUT" \
    --max-time "$CURL_MAX_TIME" \
    --request POST \
    --header "Content-Type: application/json" \
    --header "X-Pusher-Version: 1.0" \
    "${AUTH_HEADER[@]}" \
    "${CURL_CA_OPT[@]}" \
    --write-out "\n%{http_code}" \
    --data "$REQUEST_PAYLOAD" \
    "$FRAUD_API_URL" \
    2>&1) || {
    log_error "curl failed — Fraud API unreachable (${FRAUD_API_URL}). Skipping Redis update."
    exit 0   # Non-fatal: level 1 pipeline is best-effort
}

# Split HTTP body and status code
HTTP_BODY=$(echo "$HTTP_RESPONSE" | head -n -1)
HTTP_STATUS=$(echo "$HTTP_RESPONSE" | tail -n 1)

log_debug "Fraud API HTTP status: ${HTTP_STATUS}"

if [[ "$HTTP_STATUS" != "200" && "$HTTP_STATUS" != "201" ]]; then
    log_warn "Fraud API returned HTTP ${HTTP_STATUS}: ${HTTP_BODY}"
    exit 0   # Non-fatal
fi

# ---------------------------------------------------------------------------
# Step 3: Parse the stats response and write to Redis
# ---------------------------------------------------------------------------

# Expected response schema from the Fraud API:
# {
#   "event_count":    <int>,
#   "alert_count":    <int>,
#   "mean_risk_score": <float>,
#   "processing_ms":  <int>
# }

STATS_EVENT_COUNT=$(echo "$HTTP_BODY" | jq -r '.event_count      // 0' 2>/dev/null || echo 0)
STATS_ALERT_COUNT=$(echo "$HTTP_BODY" | jq -r '.alert_count      // 0' 2>/dev/null || echo 0)
STATS_MEAN_RISK=$(echo "$HTTP_BODY"   | jq -r '.mean_risk_score  // 0' 2>/dev/null || echo 0)
STATS_PROC_MS=$(echo "$HTTP_BODY"     | jq -r '.processing_ms    // 0' 2>/dev/null || echo 0)

log_info "Fraud API stats: events=${STATS_EVENT_COUNT} alerts=${STATS_ALERT_COUNT} mean_risk=${STATS_MEAN_RISK} proc_ms=${STATS_PROC_MS}"

# Build the Redis value — JSON, read by the live dashboard every 30 seconds
REDIS_VALUE=$(jq -nc \
    --argjson event_count    "$STATS_EVENT_COUNT" \
    --argjson alert_count    "$STATS_ALERT_COUNT" \
    --argjson mean_risk      "$STATS_MEAN_RISK" \
    --argjson processing_ms  "$STATS_PROC_MS" \
    --arg     pushed_at      "$PUSH_TIMESTAMP" \
    --argjson batch_size     "$EVENT_COUNT" \
    '{
        event_count:    $event_count,
        alert_count:    $alert_count,
        mean_risk_score: $mean_risk,
        processing_ms:  $processing_ms,
        pushed_at:      $pushed_at,
        batch_size:     $batch_size
    }')

# Determine redis-cli auth flag
REDIS_AUTH_OPT=()
if [[ -n "$REDIS_PASS" ]]; then
    REDIS_AUTH_OPT=(-a "$REDIS_PASS")
fi

# SET with 120-second TTL — dashboard shows "stale" if key expires
REDIS_RESULT=$(redis-cli \
    -h "$REDIS_HOST" \
    -p "$REDIS_PORT" \
    "${REDIS_AUTH_OPT[@]}" \
    SET "$REDIS_KEY" "$REDIS_VALUE" EX 120 \
    2>&1) || {
    log_warn "redis-cli SET failed: ${REDIS_RESULT} — dashboard will show stale stats"
    exit 0
}

log_debug "Redis SET ${REDIS_KEY}: ${REDIS_RESULT}"
log_info "Push cycle complete: ${EVENT_COUNT} events sent, ${STATS_ALERT_COUNT} alerts generated"
