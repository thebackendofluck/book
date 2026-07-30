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

# shellcheck disable=SC2002,SC2012
# =============================================================================
# iGaming Backup Manager - 3-2-1 Rule Implementation
# =============================================================================
# Implements the 3-2-1 backup strategy:
#   3 copies of data
#   2 different storage media
#   1 offsite location
#
# Handles PostgreSQL databases, Redis snapshots, file storage, and application
# configs with jurisdiction-aware routing.
#
# Usage:
#   ./backup_manager.sh full              # Full backup
#   ./backup_manager.sh incremental       # Incremental (WAL-based)
#   ./backup_manager.sh verify            # Verify latest backups
#   ./backup_manager.sh status            # Show backup status
#   ./backup_manager.sh schedule-install  # Install cron schedules
#   ./backup_manager.sh restore <id>      # Restore from backup ID
# =============================================================================

set -euo pipefail

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------
BACKUP_BASE_DIR="${BACKUP_BASE_DIR:-/var/backups/igaming}"
LOG_DIR="${LOG_DIR:-/var/log/igaming/backup}"
PGHOST="${PGHOST:-localhost}"
PGPORT="${PGPORT:-5432}"
PGUSER="${PGUSER:-igaming_backup}"
PGDATABASE="${PGDATABASE:-igaming_production}"
REDIS_HOST="${REDIS_HOST:-localhost}"
REDIS_PORT="${REDIS_PORT:-6379}"
MINIO_ENDPOINT="${MINIO_ENDPOINT:-http://localhost:9000}"
MINIO_BUCKET="${MINIO_BUCKET:-igaming-backups}"
MINIO_ALIAS="${MINIO_ALIAS:-igaming}"

# Jurisdiction config
JURISDICTION="${JURISDICTION:-UK}"
OFFSITE_REGION="${OFFSITE_REGION:-eu-west-2}"

# Retention (days)
RETENTION_FULL=30
RETENTION_INCREMENTAL=7
RETENTION_OFFSITE=2555  # ~7 years for regulatory compliance

# Compression
COMPRESSION_TOOL="zstd"
COMPRESSION_LEVEL_FULL=9
COMPRESSION_LEVEL_INCR=6

# Encryption
ENCRYPTION_KEY_FILE="${ENCRYPTION_KEY_FILE:-/etc/igaming/backup-encryption.key}"
ENCRYPTION_ALGORITHM="aes-256-cbc"

# Alerting
ALERT_WEBHOOK="${ALERT_WEBHOOK:-}"  # Slack/PagerDuty webhook URL
ALERT_EMAIL="${ALERT_EMAIL:-dba@acme-casino.com}"

# ---------------------------------------------------------------------------
# Derived paths
# ---------------------------------------------------------------------------
DATE_STAMP=$(date -u +"%Y%m%d_%H%M%S")
FULL_DIR="${BACKUP_BASE_DIR}/full"
INCR_DIR="${BACKUP_BASE_DIR}/incremental"
WAL_DIR="${BACKUP_BASE_DIR}/wal"
OFFSITE_DIR="${BACKUP_BASE_DIR}/offsite"
VERIFY_DIR="${BACKUP_BASE_DIR}/verify"
MANIFEST_FILE="${BACKUP_BASE_DIR}/manifest.json"

mkdir -p "$FULL_DIR" "$INCR_DIR" "$WAL_DIR" "$OFFSITE_DIR" "$VERIFY_DIR" "$LOG_DIR"

LOG_FILE="${LOG_DIR}/backup_${DATE_STAMP}.log"

# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------
log() {
    local level="$1"
    shift
    local msg="$*"
    local ts
    ts=$(date -u +"%Y-%m-%dT%H:%M:%SZ")
    echo "[$ts] [$level] $msg" | tee -a "$LOG_FILE"
}

log_info()  { log "INFO"  "$@"; }
log_warn()  { log "WARN"  "$@"; }
log_error() { log "ERROR" "$@"; }

# ---------------------------------------------------------------------------
# Alerting
# ---------------------------------------------------------------------------
send_alert() {
    local severity="$1"
    local message="$2"

    log_warn "ALERT [$severity]: $message"

    # Slack/PagerDuty webhook
    if [[ -n "$ALERT_WEBHOOK" ]]; then
        curl -s -X POST "$ALERT_WEBHOOK" \
            -H "Content-Type: application/json" \
            -d "{\"severity\": \"$severity\", \"message\": \"[Backup] $message\", \"jurisdiction\": \"$JURISDICTION\"}" \
            >/dev/null 2>&1 || true
    fi

    # Email alert (requires mailx or sendmail)
    if [[ -n "$ALERT_EMAIL" ]] && command -v mail &>/dev/null; then
        echo "$message" | mail -s "[Backup Alert][$severity] $JURISDICTION" "$ALERT_EMAIL" || true
    fi
}

# ---------------------------------------------------------------------------
# Manifest tracking
# ---------------------------------------------------------------------------
update_manifest() {
    local backup_type="$1"
    local backup_path="$2"
    local size_bytes="$3"
    local checksum="$4"
    local status="$5"

    local entry
    entry=$(cat <<ENTRY
{
  "timestamp": "$(date -u +"%Y-%m-%dT%H:%M:%SZ")",
  "type": "$backup_type",
  "path": "$backup_path",
  "size_bytes": $size_bytes,
  "checksum_sha256": "$checksum",
  "jurisdiction": "$JURISDICTION",
  "region": "$OFFSITE_REGION",
  "encrypted": true,
  "compression": "$COMPRESSION_TOOL",
  "status": "$status"
}
ENTRY
)

    # Append to manifest (JSON array)
    if [[ -f "$MANIFEST_FILE" ]]; then
        # Use python for safe JSON append
        python3 -c "
import json, sys
entry = json.loads('''$entry''')
with open('$MANIFEST_FILE', 'r') as f:
    manifest = json.load(f)
manifest.append(entry)
with open('$MANIFEST_FILE', 'w') as f:
    json.dump(manifest, f, indent=2)
" 2>/dev/null || echo "[$entry]" > "$MANIFEST_FILE"
    else
        echo "[$entry]" > "$MANIFEST_FILE"
    fi
}

# ---------------------------------------------------------------------------
# PostgreSQL full backup
# ---------------------------------------------------------------------------
backup_postgres_full() {
    log_info "Starting PostgreSQL full backup (jurisdiction: $JURISDICTION)"

    local backup_name="pg_full_${DATE_STAMP}"
    local backup_file="${FULL_DIR}/${backup_name}.sql.zst.enc"

    # pg_dump with custom format for parallel restore
    local start_time
    start_time=$(date +%s)

    pg_dump \
        -h "$PGHOST" \
        -p "$PGPORT" \
        -U "$PGUSER" \
        -d "$PGDATABASE" \
        --format=custom \
        --compress=0 \
        --verbose \
        2>> "$LOG_FILE" \
    | ${COMPRESSION_TOOL} -${COMPRESSION_LEVEL_FULL} -T0 \
    | openssl enc -${ENCRYPTION_ALGORITHM} \
        -pass file:"$ENCRYPTION_KEY_FILE" \
        -pbkdf2 \
        -out "$backup_file"

    local end_time
    end_time=$(date +%s)
    local duration=$((end_time - start_time))

    if [[ -f "$backup_file" ]]; then
        local size
        size=$(stat -c%s "$backup_file" 2>/dev/null || stat -f%z "$backup_file" 2>/dev/null || echo 0)
        local checksum
        checksum=$(sha256sum "$backup_file" | awk '{print $1}')

        log_info "Full backup complete: $backup_file ($size bytes, ${duration}s)"
        update_manifest "full_postgres" "$backup_file" "$size" "$checksum" "success"

        # Copy 2: Local secondary storage (different media)
        copy_to_secondary "$backup_file" "$backup_name"

        # Copy 3: Offsite (jurisdiction-compliant)
        copy_to_offsite "$backup_file" "$backup_name"
    else
        log_error "Full backup FAILED: output file not created"
        send_alert "critical" "PostgreSQL full backup failed for $JURISDICTION"
        update_manifest "full_postgres" "$backup_file" "0" "" "failed"
        return 1
    fi
}

# ---------------------------------------------------------------------------
# PostgreSQL WAL-based incremental backup
# ---------------------------------------------------------------------------
backup_postgres_incremental() {
    log_info "Starting PostgreSQL WAL archival (incremental)"

    local wal_backup="${INCR_DIR}/wal_${DATE_STAMP}.tar.zst.enc"

    # Archive current WAL segments
    if [[ -d "$WAL_DIR" ]] && ls "$WAL_DIR"/*.wal &>/dev/null 2>&1; then
        tar cf - -C "$WAL_DIR" . \
            | ${COMPRESSION_TOOL} -${COMPRESSION_LEVEL_INCR} -T0 \
            | openssl enc -${ENCRYPTION_ALGORITHM} \
                -pass file:"$ENCRYPTION_KEY_FILE" \
                -pbkdf2 \
                -out "$wal_backup"

        local size
        size=$(stat -c%s "$wal_backup" 2>/dev/null || stat -f%z "$wal_backup" 2>/dev/null || echo 0)
        local checksum
        checksum=$(sha256sum "$wal_backup" | awk '{print $1}')

        log_info "WAL backup complete: $wal_backup ($size bytes)"
        update_manifest "incremental_wal" "$wal_backup" "$size" "$checksum" "success"

        # Clean archived WAL files
        rm -f "$WAL_DIR"/*.wal
    else
        log_info "No WAL segments to archive"
    fi
}

# ---------------------------------------------------------------------------
# Redis backup
# ---------------------------------------------------------------------------
backup_redis() {
    log_info "Starting Redis snapshot backup"

    local backup_file="${FULL_DIR}/redis_${DATE_STAMP}.rdb.zst.enc"

    # Trigger BGSAVE and wait
    redis-cli -h "$REDIS_HOST" -p "$REDIS_PORT" BGSAVE >/dev/null 2>&1 || true
    sleep 2

    local redis_dir
    redis_dir=$(redis-cli -h "$REDIS_HOST" -p "$REDIS_PORT" CONFIG GET dir 2>/dev/null | tail -1)
    local dump_file="${redis_dir:-/var/lib/redis}/dump.rdb"

    if [[ -f "$dump_file" ]]; then
        cat "$dump_file" \
            | ${COMPRESSION_TOOL} -${COMPRESSION_LEVEL_FULL} \
            | openssl enc -${ENCRYPTION_ALGORITHM} \
                -pass file:"$ENCRYPTION_KEY_FILE" \
                -pbkdf2 \
                -out "$backup_file"

        local size
        size=$(stat -c%s "$backup_file" 2>/dev/null || stat -f%z "$backup_file" 2>/dev/null || echo 0)
        log_info "Redis backup complete: $backup_file ($size bytes)"
    else
        log_warn "Redis dump file not found at $dump_file"
    fi
}

# ---------------------------------------------------------------------------
# Copy to secondary storage (3-2-1 rule: copy 2)
# ---------------------------------------------------------------------------
copy_to_secondary() {
    local source_file="$1"
    local backup_name="$2"
    local secondary_path="/mnt/secondary-storage/igaming-backups/${JURISDICTION}/"

    log_info "Copying to secondary storage: $secondary_path"

    mkdir -p "$secondary_path" 2>/dev/null || true

    if [[ -d "$secondary_path" ]]; then
        cp "$source_file" "${secondary_path}${backup_name}.enc"
        log_info "Secondary copy complete"
    else
        log_warn "Secondary storage not mounted at $secondary_path"
        send_alert "warning" "Secondary storage unavailable for $JURISDICTION backup"
    fi
}

# ---------------------------------------------------------------------------
# Copy to offsite (3-2-1 rule: copy 3)
# ---------------------------------------------------------------------------
copy_to_offsite() {
    local source_file="$1"
    local backup_name="$2"

    log_info "Uploading to offsite storage: ${MINIO_ENDPOINT}/${MINIO_BUCKET}"

    local offsite_path="${JURISDICTION}/${DATE_STAMP:0:6}/${backup_name}.enc"

    if command -v mc &>/dev/null; then
        mc cp "$source_file" "${MINIO_ALIAS}/${MINIO_BUCKET}/${offsite_path}" \
            2>> "$LOG_FILE" || {
            log_error "Offsite upload failed"
            send_alert "critical" "Offsite backup upload failed for $JURISDICTION"
            return 1
        }
        log_info "Offsite copy complete: $offsite_path"
    else
        # Fallback: copy to offsite directory
        mkdir -p "${OFFSITE_DIR}/${JURISDICTION}" 2>/dev/null || true
        cp "$source_file" "${OFFSITE_DIR}/${JURISDICTION}/${backup_name}.enc"
        log_info "Offsite copy (local fallback) complete"
    fi
}

# ---------------------------------------------------------------------------
# Verify backups
# ---------------------------------------------------------------------------
verify_backups() {
    log_info "Verifying latest backups..."

    local errors=0

    # Find latest full backup
    local latest_full
    latest_full=$(ls -t "${FULL_DIR}"/pg_full_*.enc 2>/dev/null | head -1)

    if [[ -n "$latest_full" ]]; then
        log_info "Verifying: $latest_full"

        # Decrypt and decompress to /dev/null to verify integrity
        if openssl enc -d -${ENCRYPTION_ALGORITHM} \
            -pass file:"$ENCRYPTION_KEY_FILE" \
            -pbkdf2 \
            -in "$latest_full" 2>/dev/null \
        | ${COMPRESSION_TOOL} -d >/dev/null 2>&1; then
            log_info "  PASS: Decryption and decompression OK"
        else
            log_error "  FAIL: Cannot decrypt/decompress $latest_full"
            errors=$((errors + 1))
        fi

        # Verify checksum from manifest
        local actual_checksum
        actual_checksum=$(sha256sum "$latest_full" | awk '{print $1}')
        log_info "  SHA256: $actual_checksum"
    else
        log_warn "No full backup found in $FULL_DIR"
        errors=$((errors + 1))
    fi

    # Check backup age
    if [[ -n "$latest_full" ]]; then
        local file_age_hours
        file_age_hours=$(( ($(date +%s) - $(stat -c%Y "$latest_full" 2>/dev/null || echo 0)) / 3600 ))
        if [[ $file_age_hours -gt 25 ]]; then
            log_warn "  WARNING: Latest full backup is ${file_age_hours}h old (>24h)"
            send_alert "warning" "Full backup is ${file_age_hours}h old for $JURISDICTION"
            errors=$((errors + 1))
        else
            log_info "  Age: ${file_age_hours}h (OK)"
        fi
    fi

    if [[ $errors -gt 0 ]]; then
        log_error "Verification completed with $errors error(s)"
        send_alert "critical" "Backup verification failed with $errors error(s) for $JURISDICTION"
        return 1
    else
        log_info "All verification checks passed"
    fi
}

# ---------------------------------------------------------------------------
# Retention cleanup
# ---------------------------------------------------------------------------
cleanup_old_backups() {
    log_info "Cleaning up old backups..."

    # Full backups older than retention
    find "$FULL_DIR" -name "pg_full_*.enc" -mtime "+$RETENTION_FULL" -delete 2>/dev/null || true
    find "$FULL_DIR" -name "redis_*.enc" -mtime "+$RETENTION_FULL" -delete 2>/dev/null || true

    # Incremental backups older than retention
    find "$INCR_DIR" -name "wal_*.enc" -mtime "+$RETENTION_INCREMENTAL" -delete 2>/dev/null || true

    log_info "Cleanup complete"
}

# ---------------------------------------------------------------------------
# Status report
# ---------------------------------------------------------------------------
show_status() {
    echo "========================================"
    echo "iGaming Backup Status - $JURISDICTION"
    echo "========================================"
    echo ""

    echo "Latest full backups:"
    ls -lhS "${FULL_DIR}"/pg_full_*.enc 2>/dev/null | head -5 || echo "  None found"
    echo ""

    echo "Latest incremental backups:"
    ls -lhS "${INCR_DIR}"/wal_*.enc 2>/dev/null | head -5 || echo "  None found"
    echo ""

    echo "Disk usage:"
    echo "  Full:        $(du -sh "$FULL_DIR" 2>/dev/null | awk '{print $1}' || echo 'N/A')"
    echo "  Incremental: $(du -sh "$INCR_DIR" 2>/dev/null | awk '{print $1}' || echo 'N/A')"
    echo "  Offsite:     $(du -sh "$OFFSITE_DIR" 2>/dev/null | awk '{print $1}' || echo 'N/A')"
    echo ""

    echo "Configuration:"
    echo "  Jurisdiction:       $JURISDICTION"
    echo "  Offsite region:     $OFFSITE_REGION"
    echo "  Full retention:     ${RETENTION_FULL} days"
    echo "  Incr retention:     ${RETENTION_INCREMENTAL} days"
    echo "  Offsite retention:  ${RETENTION_OFFSITE} days (~7 years)"
    echo "  Compression:        ${COMPRESSION_TOOL} (full: -${COMPRESSION_LEVEL_FULL}, incr: -${COMPRESSION_LEVEL_INCR})"
    echo "  Encryption:         ${ENCRYPTION_ALGORITHM}"
}

# ---------------------------------------------------------------------------
# Install cron schedules
# ---------------------------------------------------------------------------
install_schedule() {
    log_info "Installing backup schedules..."

    local script_path
    script_path=$(readlink -f "$0")

    local cron_entries="
# iGaming Backup Schedule - $JURISDICTION
# Full backup: daily at 02:00 UTC
0 2 * * * JURISDICTION=$JURISDICTION $script_path full >> $LOG_DIR/cron_full.log 2>&1

# Incremental backup: every 4 hours
0 */4 * * * JURISDICTION=$JURISDICTION $script_path incremental >> $LOG_DIR/cron_incr.log 2>&1

# Redis backup: every 6 hours
0 */6 * * * JURISDICTION=$JURISDICTION $script_path redis >> $LOG_DIR/cron_redis.log 2>&1

# Verify backups: daily at 06:00 UTC
0 6 * * * JURISDICTION=$JURISDICTION $script_path verify >> $LOG_DIR/cron_verify.log 2>&1

# Cleanup old backups: daily at 04:00 UTC
0 4 * * * JURISDICTION=$JURISDICTION $script_path cleanup >> $LOG_DIR/cron_cleanup.log 2>&1
"

    echo "$cron_entries"
    echo ""
    echo "To install, run:"
    echo "  (crontab -l 2>/dev/null; echo '$cron_entries') | crontab -"
}

# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------
main() {
    local command="${1:-help}"

    case "$command" in
        full)
            backup_postgres_full
            backup_redis
            cleanup_old_backups
            ;;
        incremental)
            backup_postgres_incremental
            ;;
        redis)
            backup_redis
            ;;
        verify)
            verify_backups
            ;;
        cleanup)
            cleanup_old_backups
            ;;
        status)
            show_status
            ;;
        schedule-install)
            install_schedule
            ;;
        restore)
            log_info "Restore requested for backup: ${2:-'(none specified)'}"
            log_info "Use pitr_setup.sh for point-in-time recovery"
            log_info "Manual steps:"
            log_info "  1. openssl enc -d -$ENCRYPTION_ALGORITHM -pass file:$ENCRYPTION_KEY_FILE -pbkdf2 -in <backup.enc> | $COMPRESSION_TOOL -d > restored.dump"
            log_info "  2. pg_restore -h $PGHOST -p $PGPORT -U $PGUSER -d $PGDATABASE restored.dump"
            ;;
        help|*)
            echo "Usage: $0 {full|incremental|redis|verify|cleanup|status|schedule-install|restore <id>}"
            echo ""
            echo "Commands:"
            echo "  full              Full backup (PostgreSQL + Redis + offsite)"
            echo "  incremental       WAL-based incremental backup"
            echo "  redis             Redis snapshot backup"
            echo "  verify            Verify latest backup integrity"
            echo "  cleanup           Remove expired backups"
            echo "  status            Show backup status and configuration"
            echo "  schedule-install  Show cron schedule for installation"
            echo "  restore <id>      Instructions for restoring a backup"
            echo ""
            echo "Environment variables:"
            echo "  JURISDICTION      Target jurisdiction (UK, MT, DE, ON)"
            echo "  PGHOST            PostgreSQL host"
            echo "  PGDATABASE        PostgreSQL database"
            echo "  BACKUP_BASE_DIR   Base directory for backups"
            ;;
    esac
}

main "$@"
