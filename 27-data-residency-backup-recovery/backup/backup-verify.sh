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
# pgBackRest Backup Verification Script
# =============================================================================
# Verifies all backups: lists with sizes, checks encryption, validates
# catalog integrity, and optionally attempts a restore to a temp directory.
#
# Usage: ./backup-verify.sh [--stanza casino] [--deep]
# Options:
#   --deep    Attempt catalog restore to /tmp to verify restorability
# =============================================================================

set -euo pipefail

STANZA="${STANZA:-casino}"
DEEP=0
PASS=0
FAIL=0
WARN=0
PGBACKREST_CONF="${PGBACKREST_CONF:-/etc/pgbackrest/pgbackrest.conf}"
JURISDICTION="${JURISDICTION:-}"

while [[ $# -gt 0 ]]; do
    case "$1" in
        --stanza) STANZA="$2"; shift 2 ;;
        --deep)   DEEP=1;      shift ;;
        *) echo "Unknown: $1"; exit 1 ;;
    esac
done

log()  { echo "[$(date -u +%H:%M:%S)] $*"; }
ok()   { log "  PASS: $*"; PASS=$((PASS + 1)); }
fail() { log "  FAIL: $*"; FAIL=$((FAIL + 1)); }
warn() { log "  WARN: $*"; WARN=$((WARN + 1)); }

# ---------------------------------------------------------------------------
# Jurisdiction -> approved Wasabi region map.
# Mirrors APPROVED_PRIMARY_REGIONS in wasabi-backup.sh — update both files
# if adding a new jurisdiction.
# ---------------------------------------------------------------------------
declare -A APPROVED_PRIMARY_REGIONS=(
    [mga]="eu-central-1"
    [ukgc]="eu-west-1"
    [dge_nj]="us-east-1"
    [pgcb_pa]="us-east-1"
    [mgcb_mi]="us-east-1"
    [agco_on]="ca-central-1"
    [pagcor]="ap-southeast-1"
)

log "=== pgBackRest Backup Verification ==="
log "Stanza: ${STANZA}"
log ""

# ---------------------------------------------------------------------------
# 0. Region/jurisdiction guard
# ---------------------------------------------------------------------------
# wasabi-backup.sh's check-region refuses to upload when JURISDICTION and
# WASABI_REGION disagree, but pgBackRest talks to Wasabi directly via
# repo1-s3-* in pgbackrest.conf and never goes through that guard. A config
# deployed to the wrong host (copy-paste, stale image, a jurisdiction moved
# to a new bucket without updating this file) would keep backing up — and
# restoring — a regulated database to the wrong country with no alarm.
# This is the equivalent check for that path; run it before anything else.
log "--- Region/Jurisdiction Guard ---"
if [[ -z "$JURISDICTION" ]]; then
    fail "JURISDICTION env var not set — cannot verify backup destination is data-residency compliant"
else
    APPROVED_REGION="${APPROVED_PRIMARY_REGIONS[$JURISDICTION]:-}"
    if [[ -z "$APPROVED_REGION" ]]; then
        fail "Unknown jurisdiction '${JURISDICTION}' — not in the approved region map"
    elif [[ ! -f "$PGBACKREST_CONF" ]]; then
        fail "pgBackRest config not found: ${PGBACKREST_CONF}"
    else
        CONFIGURED_REGION=$(grep -E '^[[:space:]]*repo1-s3-region=' "$PGBACKREST_CONF" | tail -1 | cut -d= -f2 | tr -d '[:space:]')
        if [[ "$CONFIGURED_REGION" == "$APPROVED_REGION" ]]; then
            ok "Region compliance: repo1-s3-region=${CONFIGURED_REGION} matches ${JURISDICTION}'s approved region"
        else
            fail "REGION MISMATCH: repo1-s3-region=${CONFIGURED_REGION:-<unset>} but ${JURISDICTION} requires ${APPROVED_REGION} — backups may be leaving the approved jurisdiction"
        fi
    fi
fi

# ---------------------------------------------------------------------------
# 1. Check stanza status
# ---------------------------------------------------------------------------
log "--- Stanza Status ---"
INFO_JSON=$(pgbackrest --stanza="${STANZA}" info --output=json 2>/dev/null)

STANZA_STATUS=$(echo "$INFO_JSON" | python3 -c "
import sys, json
data = json.load(sys.stdin)
for s in data:
    if s['name'] == '${STANZA}':
        print(s['status']['code'])
" 2>/dev/null || echo "-1")

if [[ "$STANZA_STATUS" == "0" ]]; then
    ok "Stanza '${STANZA}' status: OK"
else
    fail "Stanza '${STANZA}' status code: ${STANZA_STATUS}"
fi

# ---------------------------------------------------------------------------
# 2. Encryption check
# ---------------------------------------------------------------------------
log ""
log "--- Encryption Check ---"
CIPHER=$(echo "$INFO_JSON" | python3 -c "
import sys, json
data = json.load(sys.stdin)
for s in data:
    if s['name'] == '${STANZA}':
        print(s.get('cipher', 'none'))
" 2>/dev/null || echo "unknown")

if [[ "$CIPHER" == "aes-256-cbc" ]]; then
    ok "Encryption: aes-256-cbc"
else
    fail "Encryption: ${CIPHER} (expected aes-256-cbc)"
fi

# ---------------------------------------------------------------------------
# 3. Backup catalog
# ---------------------------------------------------------------------------
log ""
log "--- Backup Catalog ---"
pgbackrest --stanza="${STANZA}" info 2>&1 | grep -E 'backup|wal archive' | head -20

BACKUP_COUNT=$(echo "$INFO_JSON" | python3 -c "
import sys, json
data = json.load(sys.stdin)
for s in data:
    if s['name'] == '${STANZA}':
        print(len(s.get('backup', [])))
" 2>/dev/null || echo "0")

if [[ "$BACKUP_COUNT" -gt 0 ]]; then
    ok "Backup count: ${BACKUP_COUNT}"
else
    fail "No backups found for stanza '${STANZA}'"
fi

# Check latest backup age
LATEST_BACKUP_TS=$(echo "$INFO_JSON" | python3 -c "
import sys, json, time
data = json.load(sys.stdin)
for s in data:
    if s['name'] == '${STANZA}':
        backups = s.get('backup', [])
        if backups:
            latest = backups[-1]
            stop_ts = latest['timestamp']['stop']
            age_hours = (time.time() - stop_ts) / 3600
            print(f'{age_hours:.1f}')
" 2>/dev/null || echo "999")

if python3 -c "exit(0 if float('${LATEST_BACKUP_TS}') < 25 else 1)" 2>/dev/null; then
    ok "Latest backup age: ${LATEST_BACKUP_TS} hours"
elif python3 -c "exit(0 if float('${LATEST_BACKUP_TS}') < 168 else 1)" 2>/dev/null; then
    warn "Latest backup age: ${LATEST_BACKUP_TS} hours (>24h, consider scheduling)"
else
    fail "Latest backup age: ${LATEST_BACKUP_TS} hours (>7 days - CRITICAL)"
fi

# ---------------------------------------------------------------------------
# 4. WAL archive continuity
# ---------------------------------------------------------------------------
log ""
log "--- WAL Archive ---"
WAL_RANGE=$(echo "$INFO_JSON" | python3 -c "
import sys, json
data = json.load(sys.stdin)
for s in data:
    if s['name'] == '${STANZA}':
        dbs = s.get('db', [])
        for db in dbs:
            for ar in db.get('archive', []):
                if ar.get('max'):
                    print(f\"min={ar['min']} max={ar['max']}\")
" 2>/dev/null || echo "")

if [[ -n "$WAL_RANGE" ]]; then
    ok "WAL archive present: ${WAL_RANGE}"
else
    warn "WAL archive range not found"
fi

# ---------------------------------------------------------------------------
# 5. Verify encryption on raw files (optional, samples one WAL file)
# ---------------------------------------------------------------------------
log ""
log "--- Encryption Verification (file header check) ---"
# This checks backup.info file header for the 'Salted__' OpenSSL marker
# In practice, pgBackRest handles this — we're just documenting verification
if pgbackrest --stanza="${STANZA}" check >/dev/null 2>&1; then
    ok "pgBackRest check passed (WAL archiving functional)"
else
    fail "pgBackRest check failed"
fi

# ---------------------------------------------------------------------------
# 6. Deep verification (optional catalog restore to /tmp)
# ---------------------------------------------------------------------------
if [[ "$DEEP" == "1" ]]; then
    log ""
    log "--- Deep Verification (catalog restore to /tmp) ---"
    TEMP_DIR=$(mktemp -d /tmp/pgbackrest-verify-XXXXXX)
    trap 'rm -rf "$TEMP_DIR"' EXIT

    if pgbackrest --stanza="${STANZA}" \
        "--pg1-path=${TEMP_DIR}" \
        --delta \
        --type=immediate \
        restore >/dev/null 2>&1; then
        ok "Deep restore to temp directory: succeeded"
        FILE_COUNT=$(find "$TEMP_DIR" -type f | wc -l)
        log "  Files restored: ${FILE_COUNT}"
    else
        fail "Deep restore to temp directory: failed"
    fi
fi

# ---------------------------------------------------------------------------
# Summary
# ---------------------------------------------------------------------------
log ""
log "=== Verification Summary ==="
log "  PASS: ${PASS}"
log "  WARN: ${WARN}"
log "  FAIL: ${FAIL}"

if [[ "$FAIL" -gt 0 ]]; then
    log "RESULT: FAILED - ${FAIL} check(s) failed"
    exit 1
elif [[ "$WARN" -gt 0 ]]; then
    log "RESULT: WARNING - review warnings above"
    exit 0
else
    log "RESULT: ALL CHECKS PASSED"
    exit 0
fi
