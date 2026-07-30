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
# pgBackRest Full Backup with Timing and Verification
# =============================================================================
# Runs a full backup, measures timing, checks encryption, and optionally
# sends a webhook notification.
#
# Usage:
#   ./backup-full.sh [--stanza casino] [--webhook URL]
#
# Designed to run as postgres user or via systemd (User=postgres).
# =============================================================================

set -euo pipefail

STANZA="${STANZA:-casino}"
WEBHOOK="${WEBHOOK:-}"
LOG_DIR="${LOG_DIR:-/var/log/pgbackrest}"
DATE_STAMP=$(date -u +"%Y%m%dT%H%M%SZ")
LOG_FILE="${LOG_DIR}/full-backup-${DATE_STAMP}.log"

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
    log "Status: $status - $message"
    if [[ -n "$WEBHOOK" ]]; then
        curl -sf -X POST "$WEBHOOK" \
            -H 'Content-Type: application/json' \
            -d "{\"status\":\"$status\",\"message\":\"[pgBackRest] $message\"}" \
            >/dev/null 2>&1 || true
    fi
}

# ---------------------------------------------------------------------------
log "=== Full Backup Started - Stanza: ${STANZA} ==="
BACKUP_START=$(date +%s)
BACKUP_START_ISO=$(date -u +"%Y-%m-%dT%H:%M:%SZ")

# Run backup
pgbackrest --stanza="${STANZA}" --type=full backup 2>&1 | tee -a "$LOG_FILE"

BACKUP_END=$(date +%s)
BACKUP_DURATION=$((BACKUP_END - BACKUP_START))

# Get backup size from info
BACKUP_INFO=$(pgbackrest --stanza="${STANZA}" info --output=json 2>/dev/null)
BACKUP_SIZE=$(echo "$BACKUP_INFO" | python3 -c "
import sys, json
data = json.load(sys.stdin)
for s in data:
    if s['name'] == '${STANZA}':
        backups = s.get('backup', [])
        if backups:
            b = backups[-1]
            print(b['info']['size'])
" 2>/dev/null || echo "unknown")

REPO_SIZE=$(echo "$BACKUP_INFO" | python3 -c "
import sys, json
data = json.load(sys.stdin)
for s in data:
    if s['name'] == '${STANZA}':
        backups = s.get('backup', [])
        if backups:
            b = backups[-1]
            print(b['info']['repository']['size'])
" 2>/dev/null || echo "unknown")

log ""
log "=== Backup Summary ==="
log "  Start:           ${BACKUP_START_ISO}"
log "  Duration:        ${BACKUP_DURATION} seconds"
log "  Database size:   ${BACKUP_SIZE} bytes"
log "  Repo size:       ${REPO_SIZE} bytes (compressed+encrypted)"

# Verify encryption
CIPHER=$(echo "$BACKUP_INFO" | python3 -c "
import sys, json
data = json.load(sys.stdin)
for s in data:
    if s['name'] == '${STANZA}':
        print(s.get('cipher', 'none'))
" 2>/dev/null || echo "unknown")

log "  Encryption:      ${CIPHER}"

if [[ "$CIPHER" != "aes-256-cbc" ]]; then
    log "WARNING: Encryption is not aes-256-cbc! Cipher is: ${CIPHER}"
    notify "warning" "Backup completed but cipher is ${CIPHER}, expected aes-256-cbc"
else
    log "  Encryption OK:   aes-256-cbc confirmed"
fi

notify "success" "Full backup completed in ${BACKUP_DURATION}s (${BACKUP_SIZE} bytes raw, ${REPO_SIZE} bytes in repo)"
log "=== Full Backup Completed ==="
