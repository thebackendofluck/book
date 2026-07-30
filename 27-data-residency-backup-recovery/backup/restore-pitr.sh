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
# pgBackRest Point-in-Time Recovery (PITR)
# =============================================================================
# Restores PostgreSQL to a specific point in time, enabling recovery from
# accidental data deletion, corruption, or logical errors.
#
# Usage:
#   ./restore-pitr.sh --target "2026-04-03 22:00:00+02" [options]
#
# Options:
#   --target TIMESTAMP   Recovery target (required). Format: "YYYY-MM-DD HH:MM:SS+TZ"
#   --stanza STANZA      pgBackRest stanza (default: casino)
#   --pg-port PORT       PostgreSQL port (default: 5434)
#   --pg-version VER     PostgreSQL major version (default: 16)
#   --pg-cluster NAME    Cluster name (default: main)
#   --verify-db DB       Database to verify after recovery
#   --dry-run            Show what would happen without doing it
#
# Example (recover to 5 minutes ago):
#   TARGET=$(date -d '5 minutes ago' '+%Y-%m-%d %H:%M:%S%z')
#   ./restore-pitr.sh --target "$TARGET" --verify-db casino_test
# =============================================================================

set -euo pipefail

STANZA="${STANZA:-casino}"
PG_PORT="${PG_PORT:-5434}"
PG_VERSION="${PG_VERSION:-16}"
PG_CLUSTER="${PG_CLUSTER:-main}"
VERIFY_DB="${VERIFY_DB:-}"
TARGET_TIME=""
DRY_RUN=0

while [[ $# -gt 0 ]]; do
    case "$1" in
        --target)      TARGET_TIME="$2"; shift 2 ;;
        --stanza)      STANZA="$2";      shift 2 ;;
        --pg-port)     PG_PORT="$2";     shift 2 ;;
        --pg-version)  PG_VERSION="$2";  shift 2 ;;
        --pg-cluster)  PG_CLUSTER="$2";  shift 2 ;;
        --verify-db)   VERIFY_DB="$2";   shift 2 ;;
        --dry-run)     DRY_RUN=1;        shift ;;
        *) echo "Unknown: $1"; exit 1 ;;
    esac
done

if [[ -z "$TARGET_TIME" ]]; then
    echo "ERROR: --target is required"
    echo "Usage: $0 --target '2026-04-03 22:00:00+02'"
    exit 1
fi

log() { echo "[$(date -u +%H:%M:%S)] $*"; }

log "=== PITR Restore ==="
log "Target time:  ${TARGET_TIME}"
log "Stanza:       ${STANZA}"
log "PG cluster:   ${PG_VERSION}/${PG_CLUSTER} on port ${PG_PORT}"

# Show available backup range
log ""
log "Available backup range:"
pgbackrest --stanza="${STANZA}" info 2>&1 | grep -E 'wal archive|full backup|diff backup' || true

if [[ "$DRY_RUN" == "1" ]]; then
    log "DRY RUN - exiting without changes"
    exit 0
fi

# Confirmation
read -r -p "Proceed with PITR to '${TARGET_TIME}'? Type YES to confirm: " CONFIRM
if [[ "$CONFIRM" != "YES" ]]; then
    log "Aborted"
    exit 1
fi

# Stop PostgreSQL
log "Stopping PostgreSQL..."
TOTAL_START=$(date +%s)
pg_ctlcluster "${PG_VERSION}" "${PG_CLUSTER}" stop 2>&1 || \
    systemctl stop "postgresql@${PG_VERSION}-${PG_CLUSTER}" || true
sleep 2

# Run PITR restore
log "Running PITR delta restore to: ${TARGET_TIME}"
RESTORE_START=$(date +%s)

pgbackrest \
    --stanza="${STANZA}" \
    --delta \
    --type=time \
    "--target=${TARGET_TIME}" \
    --target-action=promote \
    restore 2>&1

RESTORE_END=$(date +%s)
log "Restore completed in $((RESTORE_END - RESTORE_START)) seconds"

# Start PostgreSQL
log "Starting PostgreSQL..."
pg_ctlcluster "${PG_VERSION}" "${PG_CLUSTER}" start 2>&1 || \
    systemctl start "postgresql@${PG_VERSION}-${PG_CLUSTER}"

# Wait for PG ready
for i in $(seq 1 30); do
    if sudo -u postgres psql -p "${PG_PORT}" -c 'SELECT 1' >/dev/null 2>&1; then
        log "PostgreSQL accepting connections"
        break
    fi
    sleep 2
done

TOTAL_END=$(date +%s)

# Verify
if [[ -n "$VERIFY_DB" ]]; then
    log "Verifying ${VERIFY_DB}..."
    sudo -u postgres psql -p "${PG_PORT}" "${VERIFY_DB}" \
        -c "SELECT schemaname, tablename, n_live_tup FROM pg_stat_user_tables ORDER BY n_live_tup DESC LIMIT 5;" \
        2>/dev/null || log "Verification query failed (database may not exist at this point)"
fi

log ""
log "=== PITR Restore Summary ==="
log "  Target time:    ${TARGET_TIME}"
log "  Restore time:   $((RESTORE_END - RESTORE_START))s"
log "  Total RTO:      $((TOTAL_END - TOTAL_START))s"
log ""
log "NOTE: Database is in read-write mode after target-action=promote."
log "Verify data, then optionally run a new full backup to reset the backup chain."
log "=== PITR Restore Completed ==="
