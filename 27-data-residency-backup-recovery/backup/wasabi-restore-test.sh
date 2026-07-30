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
# Wasabi DR Restore Verification
# =============================================================================
# Validates that a Wasabi backup archive can be successfully downloaded,
# decrypted, and restored.  Run monthly as part of the mandatory DR drill.
# Timing results are measured and compared against jurisdiction RTO targets.
#
# This script does NOT touch production databases.  It restores to a staging
# PostgreSQL instance and runs a suite of validation queries.
#
# Usage:
#   ./wasabi-restore-test.sh full-drill [<object-key>]
#   ./wasabi-restore-test.sh download-only <object-key>
#   ./wasabi-restore-test.sh list-available
#   ./wasabi-restore-test.sh rto-check
#
# Required environment variables (same as wasabi-backup.sh):
#   JURISDICTION, WASABI_ACCESS_KEY, WASABI_SECRET_KEY,
#   WASABI_REGION, WASABI_ENDPOINT, WASABI_BUCKET,
#   ENCRYPTION_KEY_FILE
#
# Additional for full-drill:
#   STAGING_PGHOST      PostgreSQL staging host
#   STAGING_PGPORT      PostgreSQL staging port (default 5432)
#   STAGING_PGUSER      PostgreSQL staging user
#   STAGING_PGDATABASE  PostgreSQL staging database
# =============================================================================

set -euo pipefail

# ---------------------------------------------------------------------------
# RTO targets per jurisdiction (minutes) — must match Chapter 27 Table
# ---------------------------------------------------------------------------
declare -A RTO_TARGETS_MINUTES=(
    [dge_nj]=15
    [agco_on]=30
    [ukgc]=60
    [pgcb_pa]=60
    [mga]=60
    [mgcb_mi]=60
    [pagcor]=120
)

# ---------------------------------------------------------------------------
# Load environment
# ---------------------------------------------------------------------------
JURISDICTION="${JURISDICTION:-}"
WASABI_REGION="${WASABI_REGION:-}"
WASABI_ENDPOINT="${WASABI_ENDPOINT:-}"
WASABI_BUCKET="${WASABI_BUCKET:-}"
WASABI_ACCESS_KEY="${WASABI_ACCESS_KEY:-}"
WASABI_SECRET_KEY="${WASABI_SECRET_KEY:-}"
ENCRYPTION_KEY_FILE="${ENCRYPTION_KEY_FILE:-/etc/igaming/backup-encryption.key}"

STAGING_PGHOST="${STAGING_PGHOST:-localhost}"
STAGING_PGPORT="${STAGING_PGPORT:-5432}"
STAGING_PGUSER="${STAGING_PGUSER:-igaming_restore}"
STAGING_PGDATABASE="${STAGING_PGDATABASE:-igaming_restore_test}"

LOG_DIR="${LOG_DIR:-/var/log/igaming/wasabi-restore}"
RESTORE_WORK_DIR="${RESTORE_WORK_DIR:-/tmp/wasabi-restore-test}"
mkdir -p "$LOG_DIR" "$RESTORE_WORK_DIR"

DATE_STAMP=$(date -u +"%Y%m%dT%H%M%SZ")
LOG_FILE="${LOG_DIR}/restore_test_${JURISDICTION}_${DATE_STAMP}.log"
RESULT_FILE="${LOG_DIR}/restore_result_${JURISDICTION}_${DATE_STAMP}.json"

# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------
log() {
    local level="$1"
    shift
    local ts
    ts=$(date -u +"%Y-%m-%dT%H:%M:%SZ")
    echo "[$ts] [$level] [$JURISDICTION] $*" | tee -a "$LOG_FILE"
}

log_info()  { log "INFO"  "$@"; }
log_warn()  { log "WARN"  "$@"; }
log_error() { log "ERROR" "$@"; }

ALERT_WEBHOOK="${ALERT_WEBHOOK:-}"
ALERT_EMAIL="${ALERT_EMAIL:-dba@acme-casino.com}"

send_alert() {
    local severity="$1"
    local message="$2"
    log_warn "ALERT [$severity]: $message"
    if [[ -n "$ALERT_WEBHOOK" ]]; then
        curl -s -X POST "$ALERT_WEBHOOK" \
            -H "Content-Type: application/json" \
            -d "{\"severity\":\"$severity\",\"message\":\"[Wasabi Restore Test] [$JURISDICTION] $message\"}" \
            >/dev/null 2>&1 || true
    fi
    if [[ -n "$ALERT_EMAIL" ]] && command -v mail &>/dev/null; then
        echo "$message" | mail -s "[Restore Test][$severity][$JURISDICTION]" "$ALERT_EMAIL" || true
    fi
}

# ---------------------------------------------------------------------------
# AWS CLI helper for Wasabi
# ---------------------------------------------------------------------------
aws_wasabi() {
    AWS_ACCESS_KEY_ID="$WASABI_ACCESS_KEY" \
    AWS_SECRET_ACCESS_KEY="$WASABI_SECRET_KEY" \
    aws --endpoint-url "$WASABI_ENDPOINT" \
        --region "$WASABI_REGION" \
        "$@"
}

# ---------------------------------------------------------------------------
# List available backup objects for this jurisdiction
# ---------------------------------------------------------------------------
cmd_list_available() {
    log_info "Available backups for $JURISDICTION in s3://$WASABI_BUCKET"
    aws_wasabi s3 ls "s3://${WASABI_BUCKET}/${JURISDICTION}/" --recursive \
        | sort -k1,2 \
        | tail -20
}

# ---------------------------------------------------------------------------
# Find the most recent backup object in the bucket
# ---------------------------------------------------------------------------
find_latest_backup() {
    aws_wasabi s3 ls "s3://${WASABI_BUCKET}/${JURISDICTION}/" --recursive \
        | grep '\.enc$' \
        | sort -k1,2 \
        | tail -1 \
        | awk '{print $4}'
}

# ---------------------------------------------------------------------------
# Step 1: Download encrypted archive from Wasabi
# ---------------------------------------------------------------------------
step_download() {
    local object_key="$1"
    local local_path
    local_path="${RESTORE_WORK_DIR}/$(basename "$object_key")"

    log_info "STEP 1/4: Download from Wasabi"
    log_info "  Object: s3://$WASABI_BUCKET/$object_key"
    log_info "  Target: $local_path"

    local t0
    t0=$(date +%s)

    aws_wasabi s3 cp "s3://${WASABI_BUCKET}/${object_key}" "$local_path" \
        2>> "$LOG_FILE"

    local t1
    t1=$(date +%s)
    local elapsed=$((t1 - t0))

    local size
    size=$(stat -c%s "$local_path" 2>/dev/null || stat -f%z "$local_path" 2>/dev/null || echo 0)
    log_info "  Download complete: ${size} bytes in ${elapsed}s"

    # Verify SHA256 against stored metadata
    local expected_sha256
    expected_sha256=$(aws_wasabi s3api head-object \
        --bucket "$WASABI_BUCKET" \
        --key "$object_key" \
        --query 'Metadata.sha256' \
        --output text 2>/dev/null || echo "")

    local actual_sha256
    actual_sha256=$(sha256sum "$local_path" | awk '{print $1}')

    if [[ -n "$expected_sha256" && "$expected_sha256" != "None" ]]; then
        if [[ "$actual_sha256" == "$expected_sha256" ]]; then
            log_info "  Integrity: PASS (SHA256 matches metadata)"
        else
            log_error "  Integrity: FAIL — SHA256 mismatch"
            log_error "    Expected: $expected_sha256"
            log_error "    Actual:   $actual_sha256"
            send_alert "critical" "Downloaded backup $object_key has mismatched SHA256"
            return 1
        fi
    else
        log_warn "  No SHA256 metadata — integrity check skipped"
    fi

    echo "$local_path"
}

# ---------------------------------------------------------------------------
# Step 2: Decrypt the archive
# ---------------------------------------------------------------------------
step_decrypt() {
    local encrypted_file="$1"
    local decrypted_file="${encrypted_file%.enc}.zst"

    log_info "STEP 2/4: Decrypt"
    log_info "  Input:  $encrypted_file"
    log_info "  Output: $decrypted_file"

    if [[ ! -f "$ENCRYPTION_KEY_FILE" ]]; then
        log_error "Encryption key not found: $ENCRYPTION_KEY_FILE"
        return 1
    fi

    local t0
    t0=$(date +%s)

    openssl enc -d -aes-256-cbc \
        -pass file:"$ENCRYPTION_KEY_FILE" \
        -pbkdf2 \
        -in "$encrypted_file" \
        -out "$decrypted_file" \
        2>> "$LOG_FILE"

    local t1
    t1=$(date +%s)
    local elapsed=$((t1 - t0))

    local size
    size=$(stat -c%s "$decrypted_file" 2>/dev/null || stat -f%z "$decrypted_file" 2>/dev/null || echo 0)
    log_info "  Decryption complete: ${size} bytes in ${elapsed}s"

    echo "$decrypted_file"
}

# ---------------------------------------------------------------------------
# Step 3: Decompress the archive
# ---------------------------------------------------------------------------
step_decompress() {
    local compressed_file="$1"
    local decompressed_file="${compressed_file%.zst}"

    log_info "STEP 3/4: Decompress"
    log_info "  Input:  $compressed_file"
    log_info "  Output: $decompressed_file"

    local t0
    t0=$(date +%s)

    zstd -d --force "$compressed_file" -o "$decompressed_file" \
        2>> "$LOG_FILE"

    local t1
    t1=$(date +%s)
    local elapsed=$((t1 - t0))

    local size
    size=$(stat -c%s "$decompressed_file" 2>/dev/null || stat -f%z "$decompressed_file" 2>/dev/null || echo 0)
    log_info "  Decompression complete: ${size} bytes in ${elapsed}s"

    echo "$decompressed_file"
}

# ---------------------------------------------------------------------------
# Step 4: Restore to staging database and validate
# ---------------------------------------------------------------------------
step_restore_and_validate() {
    local dump_file="$1"

    log_info "STEP 4/4: Restore to staging database"
    log_info "  Host:     $STAGING_PGHOST:$STAGING_PGPORT"
    log_info "  Database: $STAGING_PGDATABASE"
    log_info "  File:     $dump_file"

    local t0
    t0=$(date +%s)

    # Drop and recreate staging database
    PGPASSFILE="${PGPASSFILE:-}" \
    psql \
        -h "$STAGING_PGHOST" \
        -p "$STAGING_PGPORT" \
        -U "$STAGING_PGUSER" \
        -c "DROP DATABASE IF EXISTS ${STAGING_PGDATABASE}; CREATE DATABASE ${STAGING_PGDATABASE};" \
        postgres \
        2>> "$LOG_FILE"

    # Restore
    PGPASSFILE="${PGPASSFILE:-}" \
    pg_restore \
        -h "$STAGING_PGHOST" \
        -p "$STAGING_PGPORT" \
        -U "$STAGING_PGUSER" \
        -d "$STAGING_PGDATABASE" \
        --jobs=4 \
        --verbose \
        "$dump_file" \
        2>> "$LOG_FILE"

    local t1
    t1=$(date +%s)
    local restore_duration=$((t1 - t0))
    log_info "  Restore duration: ${restore_duration}s"

    # Validation queries
    log_info "  Running validation queries..."

    local validation_errors=0

    # 1. Check that critical tables exist and are non-empty
    for table in players transactions game_rounds; do
        local count
        count=$(PGPASSFILE="${PGPASSFILE:-}" \
            psql -h "$STAGING_PGHOST" -p "$STAGING_PGPORT" \
                 -U "$STAGING_PGUSER" -d "$STAGING_PGDATABASE" \
                 -t -c "SELECT COUNT(*) FROM $table;" 2>/dev/null | tr -d ' ' || echo -1)

        if [[ "$count" -gt 0 ]]; then
            log_info "    Table $table: $count rows — PASS"
        else
            log_error "    Table $table: $count rows — FAIL (expected > 0)"
            validation_errors=$((validation_errors + 1))
        fi
    done

    # 2. Check wallet balances are non-negative (data integrity)
    local negative_balances
    negative_balances=$(PGPASSFILE="${PGPASSFILE:-}" \
        psql -h "$STAGING_PGHOST" -p "$STAGING_PGPORT" \
             -U "$STAGING_PGUSER" -d "$STAGING_PGDATABASE" \
             -t -c "SELECT COUNT(*) FROM wallets WHERE balance < 0;" 2>/dev/null | tr -d ' ' || echo -1)

    if [[ "$negative_balances" -eq 0 ]]; then
        log_info "    Wallet balance integrity: PASS (0 negative balances)"
    else
        log_error "    Wallet balance integrity: FAIL ($negative_balances negative balances)"
        validation_errors=$((validation_errors + 1))
    fi

    echo "$restore_duration $validation_errors"
}

# ---------------------------------------------------------------------------
# RTO compliance check
# ---------------------------------------------------------------------------
cmd_rto_check() {
    local rto_target="${RTO_TARGETS_MINUTES[$JURISDICTION]:-60}"
    local rto_seconds=$((rto_target * 60))

    log_info "RTO target for $JURISDICTION: ${rto_target} minutes"
    log_info "To measure actual RTO, run: $0 full-drill"
    log_info "Compare the 'Total elapsed' output against ${rto_seconds}s (${rto_target}min)"
}

# ---------------------------------------------------------------------------
# Full drill — download + decrypt + decompress + restore + validate + RTO check
# ---------------------------------------------------------------------------
cmd_full_drill() {
    local object_key="${1:-}"

    log_info "============================================================"
    log_info "Starting DR restore drill for jurisdiction: $JURISDICTION"
    log_info "Date: $DATE_STAMP"
    log_info "============================================================"

    # Validate required vars
    for var in JURISDICTION WASABI_REGION WASABI_ENDPOINT WASABI_BUCKET \
               WASABI_ACCESS_KEY WASABI_SECRET_KEY; do
        if [[ -z "${!var:-}" ]]; then
            log_error "Required environment variable not set: $var"
            exit 1
        fi
    done

    local drill_start
    drill_start=$(date +%s)

    # Find latest backup if none specified
    if [[ -z "$object_key" ]]; then
        log_info "No object key specified — finding latest backup..."
        object_key=$(find_latest_backup)
        if [[ -z "$object_key" ]]; then
            log_error "No backup objects found in s3://$WASABI_BUCKET/$JURISDICTION/"
            send_alert "critical" "DR drill FAILED: no backups found for $JURISDICTION"
            exit 1
        fi
        log_info "Using latest backup: $object_key"
    fi

    # Execute drill steps
    local encrypted_file
    encrypted_file=$(step_download "$object_key")

    local decrypted_file
    decrypted_file=$(step_decrypt "$encrypted_file")

    local dump_file
    dump_file=$(step_decompress "$decrypted_file")

    local restore_result="0 0"
    if [[ -n "$STAGING_PGHOST" && -n "$STAGING_PGUSER" ]]; then
        restore_result=$(step_restore_and_validate "$dump_file")
    else
        log_warn "STAGING_PGHOST or STAGING_PGUSER not set — skipping database restore step"
    fi

    local restore_duration validation_errors
    restore_duration=$(echo "$restore_result" | awk '{print $1}')
    validation_errors=$(echo "$restore_result" | awk '{print $2}')

    # Cleanup work files
    rm -f "$encrypted_file" "$decrypted_file" "$dump_file" 2>/dev/null || true

    local drill_end
    drill_end=$(date +%s)
    local total_elapsed=$((drill_end - drill_start))
    local total_minutes=$(( (total_elapsed + 59) / 60 ))

    # RTO assessment
    local rto_target="${RTO_TARGETS_MINUTES[$JURISDICTION]:-60}"
    local rto_status="PASS"
    if [[ $total_minutes -gt $rto_target ]]; then
        rto_status="FAIL"
    fi

    # Write structured result
    cat > "$RESULT_FILE" <<RESULT
{
  "drill_timestamp": "$DATE_STAMP",
  "jurisdiction": "$JURISDICTION",
  "wasabi_region": "$WASABI_REGION",
  "object_key": "$object_key",
  "total_elapsed_seconds": $total_elapsed,
  "total_elapsed_minutes": $total_minutes,
  "rto_target_minutes": $rto_target,
  "rto_status": "$rto_status",
  "validation_errors": ${validation_errors:-0},
  "drill_status": "$([ "${validation_errors:-0}" -eq 0 ] && [ "$rto_status" = "PASS" ] && echo "PASS" || echo "FAIL")"
}
RESULT

    log_info "============================================================"
    log_info "DR Drill Result for $JURISDICTION"
    log_info "  Total elapsed:     ${total_elapsed}s (${total_minutes}min)"
    log_info "  RTO target:        ${rto_target}min"
    log_info "  RTO status:        $rto_status"
    log_info "  Validation errors: ${validation_errors:-0}"
    log_info "  Result file:       $RESULT_FILE"
    log_info "============================================================"

    if [[ "$rto_status" = "FAIL" ]]; then
        send_alert "critical" \
            "DR drill FAILED RTO: ${total_minutes}min elapsed vs ${rto_target}min target for $JURISDICTION"
        exit 1
    fi

    if [[ "${validation_errors:-0}" -gt 0 ]]; then
        send_alert "critical" \
            "DR drill: ${validation_errors} validation error(s) in restored database for $JURISDICTION"
        exit 1
    fi

    log_info "DR drill PASSED for $JURISDICTION"
}

# ---------------------------------------------------------------------------
# Download only (without restore) — for lightweight integrity checks
# ---------------------------------------------------------------------------
cmd_download_only() {
    local object_key="${1:-}"

    if [[ -z "$object_key" ]]; then
        log_error "Usage: $0 download-only <object-key>"
        exit 1
    fi

    for var in JURISDICTION WASABI_REGION WASABI_ENDPOINT WASABI_BUCKET \
               WASABI_ACCESS_KEY WASABI_SECRET_KEY; do
        if [[ -z "${!var:-}" ]]; then
            log_error "Required environment variable not set: $var"
            exit 1
        fi
    done

    step_download "$object_key"
    log_info "Download-only complete"
}

# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------
main() {
    local command="${1:-help}"
    shift || true

    case "$command" in
        full-drill)
            cmd_full_drill "$@"
            ;;
        download-only)
            cmd_download_only "$@"
            ;;
        list-available)
            cmd_list_available
            ;;
        rto-check)
            cmd_rto_check
            ;;
        help|*)
            cat <<USAGE
Usage: $0 <command> [args]

Commands:
  full-drill [<object-key>]   Full DR drill: download, decrypt, decompress, restore, validate, RTO check
  download-only <object-key>  Download and SHA256-verify only (no restore)
  list-available              List backup objects available for this jurisdiction
  rto-check                   Display RTO target for the configured jurisdiction

Required environment variables:
  JURISDICTION, WASABI_ACCESS_KEY, WASABI_SECRET_KEY,
  WASABI_REGION, WASABI_ENDPOINT, WASABI_BUCKET, ENCRYPTION_KEY_FILE

Additional for full-drill (database restore step):
  STAGING_PGHOST, STAGING_PGPORT, STAGING_PGUSER, STAGING_PGDATABASE

RTO targets by jurisdiction:
  dge_nj   → 15 min  (NJ DGE: notify regulator within 1 hour of outage)
  agco_on  → 30 min  (AGCO: notify within 4 hours)
  ukgc     → 60 min  (UKGC: notify within 24 hours of significant incident)
  mga      → 60 min
  pgcb_pa  → 60 min
  mgcb_mi  → 60 min
  pagcor   → 120 min
USAGE
            ;;
    esac
}

main "$@"
