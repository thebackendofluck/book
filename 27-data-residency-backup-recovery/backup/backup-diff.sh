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
# pgBackRest Differential Backup with Timing
# =============================================================================
# Runs a differential backup (only changes since last full).
# Logs timing, size, and encryption status.
#
# Usage: ./backup-diff.sh [--stanza casino] [--webhook URL]
# =============================================================================

set -euo pipefail

STANZA="${STANZA:-casino}"
WEBHOOK="${WEBHOOK:-}"
LOG_DIR="${LOG_DIR:-/var/log/pgbackrest}"
DATE_STAMP=$(date -u +"%Y%m%dT%H%M%SZ")
LOG_FILE="${LOG_DIR}/diff-backup-${DATE_STAMP}.log"

while [[ $# -gt 0 ]]; do
    case "$1" in
        --stanza) STANZA="$2"; shift 2 ;;
        --webhook) WEBHOOK="$2"; shift 2 ;;
        *) echo "Unknown: $1"; exit 1 ;;
    esac
done

log() { echo "[$(date -u +%H:%M:%S)] $*" | tee -a "$LOG_FILE"; }

notify() {
    local status="$1"
    local message="$2"
    if [[ -n "$WEBHOOK" ]]; then
        curl -sf -X POST "$WEBHOOK" \
            -H 'Content-Type: application/json' \
            -d "{\"status\":\"$status\",\"message\":\"[pgBackRest Diff] $message\"}" \
            >/dev/null 2>&1 || true
    fi
}

log "=== Differential Backup Started - Stanza: ${STANZA} ==="
BACKUP_START=$(date +%s)

pgbackrest --stanza="${STANZA}" --type=diff backup 2>&1 | tee -a "$LOG_FILE"

BACKUP_END=$(date +%s)
BACKUP_DURATION=$((BACKUP_END - BACKUP_START))

# Get latest diff backup info
BACKUP_INFO=$(pgbackrest --stanza="${STANZA}" info --output=json 2>/dev/null)
LATEST=$(echo "$BACKUP_INFO" | python3 -c "
import sys, json
data = json.load(sys.stdin)
for s in data:
    if s['name'] == '${STANZA}':
        backups = [b for b in s.get('backup', []) if b['type'] == 'diff']
        if backups:
            b = backups[-1]
            print(f\"label={b['label']}\")
            print(f\"db_size={b['info']['size']}\")
            print(f\"repo_size={b['info']['repository']['size']}\")
            print(f\"db_delta={b['info']['delta']}\")
            print(f\"repo_delta={b['info']['repository']['delta']}\")
" 2>/dev/null || echo "label=unknown")

log ""
log "=== Differential Backup Summary ==="
log "  Duration: ${BACKUP_DURATION} seconds"
echo "$LATEST" | while IFS='=' read -r k v; do
    log "  ${k}: ${v}"
done

notify "success" "Diff backup completed in ${BACKUP_DURATION}s"
log "=== Differential Backup Completed ==="
