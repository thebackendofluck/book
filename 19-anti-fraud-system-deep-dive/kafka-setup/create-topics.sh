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

# shellcheck disable=SC2181
# =============================================================================
# Kafka Topic Creation for Fraud Detection Pipeline
# =============================================================================
# Creates all topics with appropriate partitioning strategies per event type.
#
# Partitioning Strategy:
#   - Gaming events:    24 partitions (highest volume, keyed by player_id)
#   - Payment events:   18 partitions (high volume, keyed by transaction_id)
#   - Behavior events:  12 partitions (medium volume, keyed by session_id)
#   - Geolocation:       6 partitions (lower volume, keyed by player_id)
#   - KYC events:        6 partitions (low volume, keyed by player_id)
#   - Fraud scores:     24 partitions (output, keyed by player_id)
#   - Alerts:           12 partitions (output, keyed by severity)
#   - DLQ topics:        3 partitions (error handling)
#
# All topics use replication-factor=3 and min.insync.replicas=2 for durability.
#
# Usage:
#   ./create-topics.sh [--bootstrap-server kafka-1:29092]
# =============================================================================

set -euo pipefail

BOOTSTRAP_SERVER="${1:-localhost:9092}"
REPLICATION_FACTOR=3
MIN_ISR=2

log() {
    echo "[$(date '+%Y-%m-%d %H:%M:%S')] $1"
}

create_topic() {
    local topic_name="$1"
    local partitions="$2"
    local retention_ms="${3:-604800000}"  # Default 7 days
    local cleanup_policy="${4:-delete}"
    local compression="${5:-lz4}"

    log "Creating topic: ${topic_name} (partitions=${partitions}, retention=${retention_ms}ms)"

    kafka-topics --bootstrap-server "${BOOTSTRAP_SERVER}" \
        --create \
        --if-not-exists \
        --topic "${topic_name}" \
        --partitions "${partitions}" \
        --replication-factor "${REPLICATION_FACTOR}" \
        --config min.insync.replicas="${MIN_ISR}" \
        --config retention.ms="${retention_ms}" \
        --config cleanup.policy="${cleanup_policy}" \
        --config compression.type="${compression}" \
        --config segment.bytes=1073741824 \
        --config max.message.bytes=10485760

    if [ $? -eq 0 ]; then
        log "  -> Created successfully"
    else
        log "  -> ERROR creating topic ${topic_name}"
        return 1
    fi
}

log "============================================="
log "Fraud Detection Pipeline - Topic Setup"
log "Bootstrap server: ${BOOTSTRAP_SERVER}"
log "============================================="

# ---------------------------------------------------------------------------
# INPUT TOPICS - Raw events from gaming platform
# ---------------------------------------------------------------------------
log ""
log "--- Creating INPUT topics ---"

# Gaming events: bets, wins, game sessions, bonus claims
# Key: player_id (ensures all actions for one player go to same partition)
# 24 partitions: highest volume (~60% of total events)
# Typical: 50K-80K events/sec during peak hours
create_topic "fraud.input.gaming-events" 24 604800000 "delete" "lz4"

# Payment events: deposits, withdrawals, chargebacks, refunds
# Key: transaction_id (unique per transaction for exactly-once processing)
# 18 partitions: high volume, each payment needs immediate scoring
create_topic "fraud.input.payment-events" 18 2592000000 "delete" "lz4"
# 30-day retention for payment events (regulatory requirement)

# Player behavior: page views, clicks, session patterns, time on page
# Key: session_id (group all actions within a single session)
# 12 partitions: medium volume, used for behavioral profiling
create_topic "fraud.input.behavior-events" 12 259200000 "delete" "snappy"
# 3-day retention (behavioral data is processed quickly into features)

# Geolocation: IP changes, GPS coordinates, VPN detection signals
# Key: player_id (track location patterns per player)
# 6 partitions: lower volume, one update per session typically
create_topic "fraud.input.geolocation-events" 6 604800000 "delete" "lz4"

# KYC events: identity verification, document uploads, PEP checks
# Key: player_id
# 6 partitions: low volume but critical for compliance
create_topic "fraud.input.kyc-events" 6 31536000000 "delete" "gzip"
# 365-day retention for KYC (regulatory compliance)

# Device fingerprinting: browser fingerprints, device IDs, canvas hashes
# Key: player_id
create_topic "fraud.input.device-events" 12 604800000 "delete" "lz4"

# ---------------------------------------------------------------------------
# PROCESSING TOPICS - Internal pipeline stages
# ---------------------------------------------------------------------------
log ""
log "--- Creating PROCESSING topics ---"

# Enriched events after feature engineering
# Key: player_id
create_topic "fraud.processing.enriched-events" 24 86400000 "delete" "lz4"
# 1-day retention (intermediate data)

# Feature vectors ready for model scoring
# Key: player_id
create_topic "fraud.processing.feature-vectors" 24 86400000 "delete" "lz4"

# Model predictions before ensemble combination
create_topic "fraud.processing.model-predictions" 24 86400000 "delete" "lz4"

# ---------------------------------------------------------------------------
# OUTPUT TOPICS - Results and alerts
# ---------------------------------------------------------------------------
log ""
log "--- Creating OUTPUT topics ---"

# Final fraud scores after ensemble scoring
# Key: player_id
# 24 partitions: consumed by multiple downstream services
create_topic "fraud.output.fraud-scores" 24 2592000000 "delete" "lz4"
# 30-day retention for audit trail

# Fraud alerts for analyst review
# Key: severity level (CRITICAL, HIGH, MEDIUM, LOW)
# This ensures all CRITICAL alerts go to same partition for ordered processing
create_topic "fraud.output.alerts" 12 7776000000 "delete" "lz4"
# 90-day retention for alert history

# Automated response actions (block, freeze, flag)
# Key: action_type
create_topic "fraud.output.response-actions" 6 7776000000 "delete" "gzip"

# Compliance reports and SAR filings
create_topic "fraud.output.compliance-reports" 3 31536000000 "delete" "gzip"
# 365-day retention (regulatory requirement)

# ---------------------------------------------------------------------------
# COMPACTED TOPICS - State stores
# ---------------------------------------------------------------------------
log ""
log "--- Creating COMPACTED topics (state stores) ---"

# Player risk profiles (latest state per player)
# Key: player_id, compacted to keep latest profile
create_topic "fraud.state.player-profiles" 24 -1 "compact" "lz4"

# Model metadata and feature importance scores
create_topic "fraud.state.model-metadata" 3 -1 "compact" "gzip"

# Alert case status (open, investigating, resolved, false-positive)
create_topic "fraud.state.alert-cases" 12 -1 "compact" "lz4"

# ---------------------------------------------------------------------------
# DEAD LETTER QUEUES - Error handling
# ---------------------------------------------------------------------------
log ""
log "--- Creating DLQ topics ---"

# DLQ for events that fail schema validation
create_topic "fraud.dlq.schema-validation" 3 2592000000 "delete" "gzip"

# DLQ for events that fail enrichment
create_topic "fraud.dlq.enrichment-failures" 3 2592000000 "delete" "gzip"

# DLQ for events that fail model scoring
create_topic "fraud.dlq.scoring-failures" 3 2592000000 "delete" "gzip"

# DLQ for events that fail response execution
create_topic "fraud.dlq.response-failures" 3 2592000000 "delete" "gzip"

# ---------------------------------------------------------------------------
# Verification
# ---------------------------------------------------------------------------
log ""
log "============================================="
log "Topic creation complete. Listing all topics:"
log "============================================="

kafka-topics --bootstrap-server "${BOOTSTRAP_SERVER}" --list | grep "^fraud\." | sort

log ""
log "Topic details:"
kafka-topics --bootstrap-server "${BOOTSTRAP_SERVER}" --describe | grep "^Topic: fraud\." | \
    awk '{print $1, $2, $3, $4, $5, $6}'

log ""
log "Setup complete. Total topics created:"
kafka-topics --bootstrap-server "${BOOTSTRAP_SERVER}" --list | grep -c "^fraud\."
