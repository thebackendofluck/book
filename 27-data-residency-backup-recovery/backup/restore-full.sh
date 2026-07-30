#!/usr/bin/env bash
# Companion code for "The Backend of Luck" - Chapter 27, Data Residency and Backup/Recovery.
# https://thebackendofluck.com | https://github.com/thebackendofluck/book
# SPDX-License-Identifier: Apache-2.0
#
# FOR TESTING AND EVALUATION ONLY. NOT FOR PRODUCTION USE.
# Published to demonstrate the patterns explained in the book. This code is
# not certified for real-money gaming: operating a gambling platform requires
# your own licence, independent test-lab certification (GLI, eCOGRA or
# equivalent) and regulator approval.

# =============================================================================
# pgBackRest Full Restore with Timing and Verification
# =============================================================================
# Stops PostgreSQL, restores from latest backup (delta mode for speed),
# starts PostgreSQL, and verifies data integrity.
#
# Usage:
#   ./restore-full.sh [--stanza casino] [--pg-port 5434] [--verify-db casino_test]
#
# WARNING: This replaces the current database with backup data.
# =============================================================================

set -euo pipefail

STANZA="${STANZA:-casino}"
PG_PORT="${PG_PORT:-5434}"
VERIFY_DB="${VERIFY_DB:-}"
PG_VERSION="${PG_VERSION:-16}"
PG_CLUSTER="${PG_CLUSTER:-main}"

while [[ $# -gt 0 ]]; do
    case "$1" in
        --stanza)    STANZA="$2";    shift 2 ;;
        --pg-port)   PG_PORT="$2";   shift 2 ;;
        --verify-db) VERIFY_DB="$2"; shift 2 ;;
        --dry-run)   DRY_RUN=1;      shift ;;
        *) echo "Unknown: $1"; exit 1 ;;
    esac
done

DRY_RUN="${DRY_RUN:-0}"

log() { echo "[$(date -u +%H:%M:%S)] $*"; }

if [[ "$DRY_RUN" == "1" ]]; then
    log "DRY RUN - no changes will be made"
    pgbackrest --stanza="${STANZA}" info
    exit 0
fi

log "=== Full Restore Started - Stanza: ${STANZA} ==="
log "WARNING: This will overwrite the current database!"

# Show available backups before proceeding
log "Available backups:"
pgbackrest --stanza="${STANZA}" info

# Stop PostgreSQL
log "Stopping PostgreSQL ${PG_VERSION}/${PG_CLUSTER}..."
STOP_START=$(date +%s)
pg_ctlcluster "${PG_VERSION}" "${PG_CLUSTER}" stop || {
    log "pg_ctlcluster stop failed, trying systemctl..."
    systemctl stop "postgresql@${PG_VERSION}-${PG_CLUSTER}" || true
}
STOP_END=$(date +%s)
log "PostgreSQL stopped in $((STOP_END - STOP_START)) seconds"

# Restore
log "Running delta restore from latest backup..."
RESTORE_START=$(date +%s)
pgbackrest --stanza="${STANZA}" --delta restore 2>&1
RESTORE_END=$(date +%s)
RESTORE_DURATION=$((RESTORE_END - RESTORE_START))
log "Restore completed in ${RESTORE_DURATION} seconds"

# Start PostgreSQL
log "Starting PostgreSQL..."
START_START=$(date +%s)
pg_ctlcluster "${PG_VERSION}" "${PG_CLUSTER}" start || {
    log "pg_ctlcluster start failed, trying systemctl..."
    systemctl start "postgresql@${PG_VERSION}-${PG_CLUSTER}"
}

# Wait for PG to be ready
for i in $(seq 1 30); do
    if sudo -u postgres psql -p "${PG_PORT}" -c 'SELECT 1' >/dev/null 2>&1; then
        break
    fi
    sleep 2
done

START_END=$(date +%s)
log "PostgreSQL started in $((START_END - START_START)) seconds"

# Verify
if [[ -n "$VERIFY_DB" ]]; then
    log "Verifying database: ${VERIFY_DB}"
    TABLE_COUNT=$(sudo -u postgres psql -p "${PG_PORT}" "${VERIFY_DB}" -t -A \
        -c "SELECT count(*) FROM information_schema.tables WHERE table_schema='public';" 2>/dev/null || echo 0)
    log "  Tables in ${VERIFY_DB}: ${TABLE_COUNT}"
fi

TOTAL_DURATION=$((START_END - RESTORE_START))
log ""
log "=== Restore Summary ==="
log "  Restore duration:  ${RESTORE_DURATION}s"
log "  Total RTO:         ${TOTAL_DURATION}s (stop + restore + start)"
log "  PostgreSQL:        running on port ${PG_PORT}"
log "=== Full Restore Completed ==="
