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

# shellcheck disable=SC2012,SC2086,SC2155
# =============================================================================
# PostgreSQL Point-in-Time Recovery (PITR) Setup
# =============================================================================
# Configures PostgreSQL for continuous WAL archiving and point-in-time recovery.
# Designed for iGaming platforms requiring sub-second RPO for wallet data.
#
# Features:
#   - WAL archiving to local and remote (S3/MinIO) storage
#   - pg_basebackup for full base backups
#   - PITR restoration to any point in time
#   - Automated WAL cleanup with retention policies
#   - Jurisdiction-aware storage configuration
#
# Usage:
#   ./pitr_setup.sh configure          # Configure WAL archiving
#   ./pitr_setup.sh base-backup        # Take a base backup
#   ./pitr_setup.sh restore <timestamp> # Restore to point in time
#   ./pitr_setup.sh status             # Show PITR status
#   ./pitr_setup.sh test               # Test PITR chain
#   ./pitr_setup.sh cleanup            # Clean old WAL files
# =============================================================================

set -euo pipefail

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------
PGHOST="${PGHOST:-localhost}"
PGPORT="${PGPORT:-5432}"
PGUSER="${PGUSER:-postgres}"
PGDATA="${PGDATA:-/var/lib/postgresql/18/main}"
PGBIN="${PGBIN:-/usr/lib/postgresql/18/bin}"

# WAL archiving
WAL_ARCHIVE_DIR="${WAL_ARCHIVE_DIR:-/var/backups/igaming/wal-archive}"
WAL_ARCHIVE_REMOTE="${WAL_ARCHIVE_REMOTE:-}"  # s3://bucket/wal/ or minio alias
BASEBACKUP_DIR="${BASEBACKUP_DIR:-/var/backups/igaming/basebackups}"

# Jurisdiction
JURISDICTION="${JURISDICTION:-UK}"

# Retention
WAL_RETENTION_DAYS="${WAL_RETENTION_DAYS:-30}"
BASEBACKUP_RETENTION_DAYS="${BASEBACKUP_RETENTION_DAYS:-30}"

# Compression
COMPRESSION="${COMPRESSION:-zstd}"

LOG_DIR="/var/log/igaming/pitr"
mkdir -p "$WAL_ARCHIVE_DIR" "$BASEBACKUP_DIR" "$LOG_DIR"

# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------
log() {
    local level="$1"; shift
    echo "[$(date -u +"%Y-%m-%dT%H:%M:%SZ")] [$level] $*" | tee -a "$LOG_DIR/pitr.log"
}
log_info()  { log "INFO"  "$@"; }
log_warn()  { log "WARN"  "$@"; }
log_error() { log "ERROR" "$@"; }

# ---------------------------------------------------------------------------
# Configure WAL archiving
# ---------------------------------------------------------------------------
configure_wal_archiving() {
    log_info "Configuring PostgreSQL WAL archiving (jurisdiction: $JURISDICTION)"

    # Generate archive command
    # Archives WAL to local directory and optionally to remote storage
    local archive_cmd="test ! -f ${WAL_ARCHIVE_DIR}/%f && cp %p ${WAL_ARCHIVE_DIR}/%f"
    if [[ -n "$WAL_ARCHIVE_REMOTE" ]]; then
        archive_cmd="${archive_cmd} && mc cp %p ${WAL_ARCHIVE_REMOTE}/%f"
    fi

    local restore_cmd="cp ${WAL_ARCHIVE_DIR}/%f %p"

    cat <<PGCONF

# =============================================================================
# PostgreSQL PITR Configuration
# Add to postgresql.conf (or ALTER SYSTEM)
# =============================================================================

# --- WAL Settings ---
wal_level = replica                    # Required for PITR (logical also works)
max_wal_senders = 5                    # Allow streaming replication clients
max_wal_size = '16GB'                  # Max WAL before forced checkpoint
min_wal_size = '2GB'                   # Keep at least this much WAL
wal_keep_size = '8GB'                  # WAL to keep for replication slots

# --- Archive Settings ---
archive_mode = on                      # Enable WAL archiving
archive_command = '${archive_cmd}'
archive_timeout = 60                   # Archive every 60 seconds (max RPO window)

# --- Recovery Settings ---
# restore_command is used during recovery only
# restore_command = '${restore_cmd}'

# --- Checkpoint Tuning ---
checkpoint_timeout = '15min'           # Checkpoint every 15 minutes
checkpoint_completion_target = 0.9     # Spread checkpoint I/O over 90% of interval

# --- Performance ---
wal_compression = ${COMPRESSION}       # Compress WAL for faster archiving
wal_buffers = '256MB'                  # WAL buffer size (high-throughput gambling)
synchronous_commit = on                # Required for Tier-1 (wallet) data
full_page_writes = on                  # Required for crash recovery

PGCONF

    log_info "Apply these settings to postgresql.conf and restart PostgreSQL"
    log_info "Or use ALTER SYSTEM SET commands:"

    cat <<ALTERSYS

-- Apply via psql:
ALTER SYSTEM SET wal_level = 'replica';
ALTER SYSTEM SET archive_mode = 'on';
ALTER SYSTEM SET archive_command = '${archive_cmd}';
ALTER SYSTEM SET archive_timeout = 60;
ALTER SYSTEM SET max_wal_senders = 5;
ALTER SYSTEM SET max_wal_size = '16GB';
ALTER SYSTEM SET min_wal_size = '2GB';
ALTER SYSTEM SET wal_compression = '${COMPRESSION}';
ALTER SYSTEM SET checkpoint_timeout = '15min';
ALTER SYSTEM SET checkpoint_completion_target = 0.9;

-- Reload (archive_mode and wal_level require restart):
SELECT pg_reload_conf();

-- IMPORTANT: wal_level and archive_mode changes require restart:
-- sudo systemctl restart postgresql@18-main

ALTERSYS
}

# ---------------------------------------------------------------------------
# Take base backup
# ---------------------------------------------------------------------------
take_base_backup() {
    local backup_label="basebackup_${JURISDICTION}_$(date -u +%Y%m%d_%H%M%S)"
    local backup_path="${BASEBACKUP_DIR}/${backup_label}"

    log_info "Taking base backup: $backup_label"

    mkdir -p "$backup_path"

    local start_time
    start_time=$(date +%s)

    # pg_basebackup with checkpoint and progress
    ${PGBIN}/pg_basebackup \
        -h "$PGHOST" \
        -p "$PGPORT" \
        -U "$PGUSER" \
        -D "$backup_path" \
        -Ft \
        -z \
        -Xs \
        -P \
        -l "$backup_label" \
        --checkpoint=fast \
        --wal-method=stream \
        2>&1 | tee -a "$LOG_DIR/basebackup.log"

    local end_time
    end_time=$(date +%s)
    local duration=$((end_time - start_time))

    if [[ -f "${backup_path}/base.tar.gz" ]] || [[ -f "${backup_path}/base.tar" ]]; then
        local size
        size=$(du -sh "$backup_path" | awk '{print $1}')
        log_info "Base backup complete: $backup_path ($size, ${duration}s)"

        # Record backup info
        cat > "${backup_path}/backup_info.json" <<INFO
{
    "label": "$backup_label",
    "jurisdiction": "$JURISDICTION",
    "start_time": "$(date -u +"%Y-%m-%dT%H:%M:%SZ" -d "@$start_time")",
    "end_time": "$(date -u +"%Y-%m-%dT%H:%M:%SZ" -d "@$end_time")",
    "duration_seconds": $duration,
    "size": "$size",
    "pg_version": "$(${PGBIN}/postgres --version 2>/dev/null || echo 'unknown')",
    "wal_archive_dir": "$WAL_ARCHIVE_DIR"
}
INFO

        # Copy to remote if configured
        if [[ -n "$WAL_ARCHIVE_REMOTE" ]]; then
            log_info "Uploading base backup to remote storage..."
            # mc mirror or aws s3 sync
            if command -v mc &>/dev/null; then
                mc cp --recursive "$backup_path" \
                    "${WAL_ARCHIVE_REMOTE}/../basebackups/${backup_label}/" \
                    2>&1 | tee -a "$LOG_DIR/basebackup.log" || \
                    log_warn "Remote upload failed"
            fi
        fi
    else
        log_error "Base backup FAILED -- no output files in $backup_path"
        return 1
    fi
}

# ---------------------------------------------------------------------------
# Point-in-time restore
# ---------------------------------------------------------------------------
restore_pitr() {
    local target_time="${1:-}"
    local restore_dir="${2:-/var/lib/postgresql/18/restore}"

    if [[ -z "$target_time" ]]; then
        echo "Usage: $0 restore <timestamp>"
        echo "  Timestamp format: '2026-03-08 14:30:00 UTC'"
        echo ""
        echo "Available base backups:"
        ls -lt "$BASEBACKUP_DIR"/ 2>/dev/null | head -10
        return 1
    fi

    log_info "=== POINT-IN-TIME RECOVERY ==="
    log_info "Target time:  $target_time"
    log_info "Restore dir:  $restore_dir"
    log_info "Jurisdiction: $JURISDICTION"

    # Find latest base backup before target time
    local latest_backup
    latest_backup=$(ls -dt "$BASEBACKUP_DIR"/basebackup_* 2>/dev/null | head -1)

    if [[ -z "$latest_backup" ]]; then
        log_error "No base backups found in $BASEBACKUP_DIR"
        return 1
    fi

    log_info "Using base backup: $latest_backup"

    # Safety check: don't overwrite production
    if [[ "$restore_dir" == "$PGDATA" ]]; then
        log_error "SAFETY: Cannot restore directly to PGDATA ($PGDATA)"
        log_error "Use a separate directory and swap after validation"
        return 1
    fi

    mkdir -p "$restore_dir"

    cat <<STEPS

=== PITR RESTORE PROCEDURE ===

STEP 1: Stop PostgreSQL (if restoring in place)
  sudo systemctl stop postgresql@18-main

STEP 2: Extract base backup
  mkdir -p ${restore_dir}
  tar xzf ${latest_backup}/base.tar.gz -C ${restore_dir}

STEP 3: Create recovery signal file
  touch ${restore_dir}/recovery.signal

STEP 4: Configure recovery in postgresql.conf
  Add to ${restore_dir}/postgresql.conf:

  restore_command = 'cp ${WAL_ARCHIVE_DIR}/%f %p'
  recovery_target_time = '${target_time}'
  recovery_target_action = 'promote'
  recovery_target_inclusive = true

  # For specific transaction (if known):
  # recovery_target_xid = '<transaction_id>'
  # recovery_target_name = '<restore_point_name>'

STEP 5: Start PostgreSQL with new data directory
  ${PGBIN}/pg_ctl -D ${restore_dir} start

STEP 6: Verify recovery
  psql -h localhost -p ${PGPORT} -U ${PGUSER} -c "SELECT pg_is_in_recovery();"
  psql -h localhost -p ${PGPORT} -U ${PGUSER} -c "SELECT now(), pg_last_xact_replay_timestamp();"

STEP 7: Validate data integrity
  -- Check wallet balances match audit trail
  SELECT COUNT(*) FROM player_wallets;
  SELECT SUM(balance) FROM player_wallets;

  -- Check transaction continuity
  SELECT MAX(created_at), COUNT(*) FROM transactions
  WHERE created_at <= '${target_time}';

STEP 8: Promote to primary (if recovery is complete)
  SELECT pg_promote();

STEP 9: Verify application connectivity
  -- Run application health checks
  -- Verify player balances
  -- Check game session continuity

=== END PROCEDURE ===

STEPS

    log_info "PITR restore procedure generated. Review and execute manually."
    log_info "CRITICAL: Always validate data integrity before switching production traffic"
}

# ---------------------------------------------------------------------------
# Status
# ---------------------------------------------------------------------------
show_status() {
    echo "========================================"
    echo "PostgreSQL PITR Status - $JURISDICTION"
    echo "========================================"

    # Check if archiving is enabled
    echo ""
    echo "--- Archive Status ---"
    if command -v psql &>/dev/null; then
        psql -h "$PGHOST" -p "$PGPORT" -U "$PGUSER" -t -c \
            "SELECT 'archive_mode: ' || setting FROM pg_settings WHERE name = 'archive_mode'
             UNION ALL
             SELECT 'wal_level: ' || setting FROM pg_settings WHERE name = 'wal_level'
             UNION ALL
             SELECT 'archive_timeout: ' || setting || 's' FROM pg_settings WHERE name = 'archive_timeout';" \
            2>/dev/null || echo "  Cannot connect to PostgreSQL"
    else
        echo "  psql not available"
    fi

    # WAL archive stats
    echo ""
    echo "--- WAL Archive ---"
    echo "  Directory: $WAL_ARCHIVE_DIR"
    if [[ -d "$WAL_ARCHIVE_DIR" ]]; then
        local wal_count
        wal_count=$(find "$WAL_ARCHIVE_DIR" -type f -name "0*" 2>/dev/null | wc -l)
        local wal_size
        wal_size=$(du -sh "$WAL_ARCHIVE_DIR" 2>/dev/null | awk '{print $1}')
        local oldest_wal
        oldest_wal=$(find "$WAL_ARCHIVE_DIR" -type f -name "0*" -printf '%T+ %p\n' 2>/dev/null | sort | head -1 | awk '{print $1}')
        local newest_wal
        newest_wal=$(find "$WAL_ARCHIVE_DIR" -type f -name "0*" -printf '%T+ %p\n' 2>/dev/null | sort -r | head -1 | awk '{print $1}')

        echo "  WAL files:   $wal_count"
        echo "  Total size:  $wal_size"
        echo "  Oldest:      ${oldest_wal:-N/A}"
        echo "  Newest:      ${newest_wal:-N/A}"
    else
        echo "  Directory not found"
    fi

    # Base backups
    echo ""
    echo "--- Base Backups ---"
    echo "  Directory: $BASEBACKUP_DIR"
    if [[ -d "$BASEBACKUP_DIR" ]]; then
        ls -lt "$BASEBACKUP_DIR"/ 2>/dev/null | head -6
    else
        echo "  No base backups found"
    fi

    # Replication info
    echo ""
    echo "--- Replication Status ---"
    if command -v psql &>/dev/null; then
        psql -h "$PGHOST" -p "$PGPORT" -U "$PGUSER" -t -c \
            "SELECT 'Current WAL: ' || pg_current_wal_lsn()::text;" \
            2>/dev/null || echo "  Cannot query replication status"

        psql -h "$PGHOST" -p "$PGPORT" -U "$PGUSER" -c \
            "SELECT pid, client_addr, state, sent_lsn, replay_lsn,
                    pg_wal_lsn_diff(sent_lsn, replay_lsn) AS lag_bytes
             FROM pg_stat_replication;" \
            2>/dev/null || true
    fi

    echo ""
    echo "--- Configuration ---"
    echo "  Jurisdiction:    $JURISDICTION"
    echo "  WAL retention:   ${WAL_RETENTION_DAYS} days"
    echo "  Base retention:  ${BASEBACKUP_RETENTION_DAYS} days"
    echo "  Compression:     $COMPRESSION"
}

# ---------------------------------------------------------------------------
# Test PITR chain
# ---------------------------------------------------------------------------
test_pitr_chain() {
    log_info "Testing PITR chain integrity..."

    local errors=0

    # 1. Check archive_mode
    if command -v psql &>/dev/null; then
        local archive_mode
        archive_mode=$(psql -h "$PGHOST" -p "$PGPORT" -U "$PGUSER" -t -c \
            "SELECT setting FROM pg_settings WHERE name = 'archive_mode';" \
            2>/dev/null | tr -d ' ')

        if [[ "$archive_mode" == "on" ]]; then
            log_info "PASS: archive_mode is ON"
        else
            log_error "FAIL: archive_mode is '$archive_mode' (expected: on)"
            errors=$((errors + 1))
        fi

        # 2. Check wal_level
        local wal_level
        wal_level=$(psql -h "$PGHOST" -p "$PGPORT" -U "$PGUSER" -t -c \
            "SELECT setting FROM pg_settings WHERE name = 'wal_level';" \
            2>/dev/null | tr -d ' ')

        if [[ "$wal_level" == "replica" ]] || [[ "$wal_level" == "logical" ]]; then
            log_info "PASS: wal_level is '$wal_level'"
        else
            log_error "FAIL: wal_level is '$wal_level' (need replica or logical)"
            errors=$((errors + 1))
        fi
    fi

    # 3. Check WAL archive directory
    if [[ -d "$WAL_ARCHIVE_DIR" ]] && [[ -w "$WAL_ARCHIVE_DIR" ]]; then
        log_info "PASS: WAL archive directory exists and is writable"
    else
        log_error "FAIL: WAL archive directory issue ($WAL_ARCHIVE_DIR)"
        errors=$((errors + 1))
    fi

    # 4. Check base backup exists
    local latest_base
    latest_base=$(ls -dt "$BASEBACKUP_DIR"/basebackup_* 2>/dev/null | head -1)
    if [[ -n "$latest_base" ]]; then
        local age_hours
        age_hours=$(( ($(date +%s) - $(stat -c%Y "$latest_base" 2>/dev/null || echo 0)) / 3600 ))
        log_info "PASS: Latest base backup: $latest_base (${age_hours}h ago)"
        if [[ $age_hours -gt $((BASEBACKUP_RETENTION_DAYS * 24)) ]]; then
            log_warn "WARN: Base backup is older than retention period"
        fi
    else
        log_error "FAIL: No base backup found in $BASEBACKUP_DIR"
        errors=$((errors + 1))
    fi

    # 5. Force a WAL switch to test archiving
    if command -v psql &>/dev/null; then
        log_info "Forcing WAL switch to test archiving..."
        psql -h "$PGHOST" -p "$PGPORT" -U "$PGUSER" -c \
            "SELECT pg_switch_wal();" 2>/dev/null || true
        sleep 2

        # Check if new WAL file appeared
        local wal_count_after
        wal_count_after=$(find "$WAL_ARCHIVE_DIR" -type f -name "0*" -newer "$LOG_DIR/pitr.log" 2>/dev/null | wc -l)
        if [[ $wal_count_after -gt 0 ]]; then
            log_info "PASS: WAL archiving is working (new file archived)"
        else
            log_warn "WARN: WAL file not yet archived (may be delayed by archive_timeout)"
        fi
    fi

    echo ""
    if [[ $errors -eq 0 ]]; then
        log_info "PITR chain test: ALL PASSED"
    else
        log_error "PITR chain test: $errors FAILURE(S)"
    fi

    return $errors
}

# ---------------------------------------------------------------------------
# Cleanup old WAL files
# ---------------------------------------------------------------------------
cleanup_wal() {
    log_info "Cleaning WAL archives older than ${WAL_RETENTION_DAYS} days..."

    local deleted
    deleted=$(find "$WAL_ARCHIVE_DIR" -type f -mtime "+${WAL_RETENTION_DAYS}" -delete -print 2>/dev/null | wc -l)
    log_info "Deleted $deleted old WAL files"

    # Cleanup old base backups
    log_info "Cleaning base backups older than ${BASEBACKUP_RETENTION_DAYS} days..."
    deleted=$(find "$BASEBACKUP_DIR" -maxdepth 1 -type d -name "basebackup_*" -mtime "+${BASEBACKUP_RETENTION_DAYS}" -exec rm -rf {} \; -print 2>/dev/null | wc -l)
    log_info "Deleted $deleted old base backups"
}

# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------
main() {
    local command="${1:-help}"

    case "$command" in
        configure)
            configure_wal_archiving
            ;;
        base-backup)
            take_base_backup
            ;;
        restore)
            restore_pitr "${2:-}" "${3:-}"
            ;;
        status)
            show_status
            ;;
        test)
            test_pitr_chain
            ;;
        cleanup)
            cleanup_wal
            ;;
        help|*)
            echo "Usage: $0 {configure|base-backup|restore|status|test|cleanup}"
            echo ""
            echo "Commands:"
            echo "  configure              Show WAL archiving configuration"
            echo "  base-backup            Take a new base backup"
            echo "  restore <timestamp>    Generate PITR restore procedure"
            echo "  status                 Show PITR chain status"
            echo "  test                   Test PITR chain integrity"
            echo "  cleanup                Remove old WAL and base backups"
            echo ""
            echo "Environment variables:"
            echo "  JURISDICTION       Target jurisdiction (UK, MT, DE, ON)"
            echo "  PGHOST/PGPORT      PostgreSQL connection"
            echo "  PGDATA             PostgreSQL data directory"
            echo "  WAL_ARCHIVE_DIR    WAL archive destination"
            echo "  BASEBACKUP_DIR     Base backup storage"
            echo "  WAL_RETENTION_DAYS WAL retention period (default: 30)"
            ;;
    esac
}

main "$@"
