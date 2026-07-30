#!/usr/bin/env bash
# Companion code for "The Backend of Luck" - Chapter 23b, DevSecOps Pipeline Implementation.
# https://thebackendofluck.com | https://github.com/thebackendofluck/book
# SPDX-License-Identifier: Apache-2.0
#
# FOR TESTING AND EVALUATION ONLY. NOT FOR PRODUCTION USE.
# Published to demonstrate the patterns explained in the book. This code is
# not certified for real-money gaming: operating a gambling platform requires
# your own licence, independent test-lab certification (GLI, eCOGRA or
# equivalent) and regulator approval.

# ============================================================================
# CONFIGURATION: Update the variables below before running this script.
# Replace all YOUR_* placeholders with your environment-specific values.
# ============================================================================
# gitlab-restore.sh — GitLab CE restore script
#
# Usage:
#   gitlab-restore.sh <backup_filename.tar>
#   gitlab-restore.sh --dry-run <backup_filename.tar>
#
# The backup_filename must exist in the daily or weekly backup directory on
# your GitLab host's backup storage, and the corresponding gitlab-secrets.json and gitlab.rb
# must be present in the secrets directory.
#
# This script stops GitLab services, restores the application backup, restores
# the configuration files, then brings services back up.
#
# IMPORTANT: This script is DESTRUCTIVE. It will overwrite the current GitLab
# data. Always run with --dry-run first to validate that all required files are
# present before committing to the restore.

set -euo pipefail

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------
GITLAB_CONTAINER="gitlab"
GITLAB_HOST="YOUR_GITLAB_USER@YOUR_GITLAB_HOST"

BACKUP_ROOT="/data/backups/gitlab"
DAILY_DIR="${BACKUP_ROOT}/daily"
WEEKLY_DIR="${BACKUP_ROOT}/weekly"
SECRETS_DIR="${BACKUP_ROOT}/secrets"

# Minimum free disk space required for restore (GB)
MIN_FREE_GB=20

LOG_FILE="/var/log/gitlab-restore.log"

# ---------------------------------------------------------------------------
# Globals
# ---------------------------------------------------------------------------
DRY_RUN=false
BACKUP_FILENAME=""
BACKUP_TIMESTAMP=""

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
log() {
    echo "[$(date '+%Y-%m-%d %H:%M:%S')] $*" | tee -a "${LOG_FILE}"
}

log_error() {
    echo "[$(date '+%Y-%m-%d %H:%M:%S')] ERROR: $*" | tee -a "${LOG_FILE}" >&2
}

die() {
    log_error "$*"
    exit 1
}

confirm() {
    local prompt="$1"
    if [[ "${DRY_RUN}" == "true" ]]; then
        log "[DRY-RUN] Would prompt: ${prompt}"
        return 0
    fi
    read -r -p "${prompt} [y/N] " response
    case "${response}" in
        [yY][eE][sS]|[yY]) return 0 ;;
        *) log "Restore aborted by user."; exit 0 ;;
    esac
}

run_on_gitlab_host() {
    if [[ "${DRY_RUN}" == "true" ]]; then
        log "[DRY-RUN] ssh ${GITLAB_HOST}: $1"
        return 0
    fi
    ssh -o BatchMode=yes -o ConnectTimeout=10 "${GITLAB_HOST}" "$1"
}

run_in_container() {
    local cmd="$1"
    if [[ "${DRY_RUN}" == "true" ]]; then
        log "[DRY-RUN] docker exec ${GITLAB_CONTAINER}: ${cmd}"
        return 0
    fi
    run_on_gitlab_host "docker exec ${GITLAB_CONTAINER} ${cmd}"
}

# ---------------------------------------------------------------------------
# Argument parsing
# ---------------------------------------------------------------------------
parse_args() {
    if [[ $# -lt 1 ]]; then
        echo "Usage: $0 [--dry-run] <backup_filename.tar>"
        echo ""
        echo "Example:"
        echo "  $0 --dry-run 1712000000_2026_04_01_16.11.3_gitlab_backup.tar"
        echo "  $0 1712000000_2026_04_01_16.11.3_gitlab_backup.tar"
        exit 1
    fi

    while [[ $# -gt 0 ]]; do
        case "$1" in
            --dry-run)
                DRY_RUN=true
                log "DRY-RUN mode enabled — no changes will be made."
                shift
                ;;
            -*)
                die "Unknown option: $1"
                ;;
            *)
                BACKUP_FILENAME="$1"
                shift
                ;;
        esac
    done

    if [[ -z "${BACKUP_FILENAME}" ]]; then
        die "No backup filename provided."
    fi

    # Extract timestamp prefix from filename (format: EPOCH_DATE_VERSION_gitlab_backup.tar)
    BACKUP_TIMESTAMP="${BACKUP_FILENAME%_gitlab_backup.tar}"
}

# ---------------------------------------------------------------------------
# Pre-restore checks
# ---------------------------------------------------------------------------
prechecks() {
    log "=== Pre-restore checks ==="

    # SSH connectivity
    if ! ssh -o BatchMode=yes -o ConnectTimeout=10 "${GITLAB_HOST}" "true" 2>/dev/null; then
        die "Cannot SSH to ${GITLAB_HOST}"
    fi
    log "SSH connectivity: OK"

    # Locate backup tarball (check daily then weekly)
    local backup_path=""
    if ssh -o BatchMode=yes "${GITLAB_HOST}" "test -f '${DAILY_DIR}/${BACKUP_FILENAME}'" 2>/dev/null; then
        backup_path="${DAILY_DIR}/${BACKUP_FILENAME}"
    elif ssh -o BatchMode=yes "${GITLAB_HOST}" "test -f '${WEEKLY_DIR}/${BACKUP_FILENAME}'" 2>/dev/null; then
        backup_path="${WEEKLY_DIR}/${BACKUP_FILENAME}"
    else
        die "Backup file not found in daily or weekly directories: ${BACKUP_FILENAME}"
    fi
    log "Backup tarball found: ${backup_path}"

    # Check sha256 checksum if present
    if ssh -o BatchMode=yes "${GITLAB_HOST}" "test -f '${backup_path}.sha256'" 2>/dev/null; then
        local stored_hash
        stored_hash=$(ssh -o BatchMode=yes "${GITLAB_HOST}" \
            "awk '{print \$1}' '${backup_path}.sha256'")
        local actual_hash
        actual_hash=$(ssh -o BatchMode=yes "${GITLAB_HOST}" \
            "sha256sum '${backup_path}' | awk '{print \$1}'")
        if [[ "${stored_hash}" == "${actual_hash}" ]]; then
            log "Checksum verification: OK (${actual_hash})"
        else
            die "Checksum mismatch! Stored: ${stored_hash}, Actual: ${actual_hash}"
        fi
    else
        log "Warning: no .sha256 file found — skipping checksum verification"
    fi

    # Secrets files
    local secrets_file="${SECRETS_DIR}/gitlab-secrets.json.${BACKUP_TIMESTAMP}"
    if ! ssh -o BatchMode=yes "${GITLAB_HOST}" "test -f '${secrets_file}'" 2>/dev/null; then
        # Try to find any secrets file that matches the date portion
        local date_prefix
        date_prefix=$(echo "${BACKUP_TIMESTAMP}" | grep -oP '\d{4}-\d{2}-\d{2}' | head -1 || true)
        if [[ -n "${date_prefix}" ]]; then
            local fallback_secrets
            fallback_secrets=$(ssh -o BatchMode=yes "${GITLAB_HOST}" \
                "ls -1t '${SECRETS_DIR}'/gitlab-secrets.json.*${date_prefix}* 2>/dev/null | head -1") || true
            if [[ -n "${fallback_secrets}" ]]; then
                log "Warning: exact secrets match not found. Using nearest: ${fallback_secrets}"
                secrets_file="${fallback_secrets}"
            else
                die "gitlab-secrets.json not found for timestamp ${BACKUP_TIMESTAMP}. Cannot restore encrypted data."
            fi
        else
            die "gitlab-secrets.json not found: ${secrets_file}"
        fi
    fi
    log "Secrets file found: ${secrets_file}"

    local rb_file="${SECRETS_DIR}/gitlab.rb.${BACKUP_TIMESTAMP}"
    if ! ssh -o BatchMode=yes "${GITLAB_HOST}" "test -f '${rb_file}'" 2>/dev/null; then
        log "Warning: gitlab.rb backup not found for exact timestamp. Existing container gitlab.rb will be used."
    else
        log "gitlab.rb found: ${rb_file}"
    fi

    # Disk space
    local free_gb
    free_gb=$(ssh -o BatchMode=yes "${GITLAB_HOST}" \
        "df -BG '/var/lib/docker' | awk 'NR==2{print \$4}' | tr -d 'G'" 2>/dev/null) || free_gb=0
    if [[ "${free_gb}" -lt "${MIN_FREE_GB}" ]]; then
        die "Insufficient disk space for restore. Required: ${MIN_FREE_GB}G, Available: ${free_gb}G"
    fi
    log "Disk space check: ${free_gb}G free — OK"

    # Backup tarball size
    local backup_size
    backup_size=$(ssh -o BatchMode=yes "${GITLAB_HOST}" \
        "du -sh '${backup_path}' | awk '{print \$1}'" 2>/dev/null) || backup_size="unknown"
    log "Backup size: ${backup_size}"

    log "=== All pre-restore checks passed ==="
    echo "${backup_path}"
}

# ---------------------------------------------------------------------------
# Stop GitLab services
# ---------------------------------------------------------------------------
stop_services() {
    log "Stopping GitLab services (puma, sidekiq)..."
    run_in_container "gitlab-ctl stop puma"
    run_in_container "gitlab-ctl stop sidekiq"
    # Verify services stopped
    if [[ "${DRY_RUN}" != "true" ]]; then
        sleep 5
        run_in_container "gitlab-ctl status" || true
    fi
    log "GitLab services stopped."
}

# ---------------------------------------------------------------------------
# Copy backup into container
# ---------------------------------------------------------------------------
stage_backup_in_container() {
    local backup_path="$1"
    log "Staging backup tarball inside container..."
    run_on_gitlab_host \
        "docker cp '${backup_path}' \
         '${GITLAB_CONTAINER}:/var/opt/gitlab/backups/${BACKUP_FILENAME}'"
    run_in_container "chmod 600 '/var/opt/gitlab/backups/${BACKUP_FILENAME}'"
    log "Backup staged."
}

# ---------------------------------------------------------------------------
# Restore application data
# ---------------------------------------------------------------------------
restore_application_data() {
    log "Running gitlab-backup restore..."
    # Strip _gitlab_backup.tar suffix for the BACKUP env var
    local backup_id
    backup_id="${BACKUP_FILENAME%_gitlab_backup.tar}"
    run_in_container "gitlab-backup restore BACKUP=${backup_id} force=yes"
    log "gitlab-backup restore completed."
}

# ---------------------------------------------------------------------------
# Restore configuration files
# ---------------------------------------------------------------------------
restore_config_files() {
    local backup_path="$1"

    # gitlab-secrets.json
    local secrets_file="${SECRETS_DIR}/gitlab-secrets.json.${BACKUP_TIMESTAMP}"
    if run_on_gitlab_host "test -f '${secrets_file}'" 2>/dev/null || [[ "${DRY_RUN}" == "true" ]]; then
        log "Restoring gitlab-secrets.json..."
        run_on_gitlab_host \
            "docker cp '${secrets_file}' \
             '${GITLAB_CONTAINER}:/etc/gitlab/gitlab-secrets.json'"
        run_in_container "chmod 600 /etc/gitlab/gitlab-secrets.json"
        run_in_container "chown root:root /etc/gitlab/gitlab-secrets.json"
    else
        log "Warning: gitlab-secrets.json not found — existing file in container will be kept."
        log "CAUTION: If this is a restore to a different instance, encrypted data will be unreadable."
    fi

    # gitlab.rb
    local rb_file="${SECRETS_DIR}/gitlab.rb.${BACKUP_TIMESTAMP}"
    if run_on_gitlab_host "test -f '${rb_file}'" 2>/dev/null || [[ "${DRY_RUN}" == "true" ]]; then
        log "Restoring gitlab.rb..."
        run_on_gitlab_host \
            "docker cp '${rb_file}' \
             '${GITLAB_CONTAINER}:/etc/gitlab/gitlab.rb'"
        run_in_container "chmod 600 /etc/gitlab/gitlab.rb"
        run_in_container "chown root:root /etc/gitlab/gitlab.rb"
    fi
}

# ---------------------------------------------------------------------------
# Reconfigure and restart
# ---------------------------------------------------------------------------
restart_services() {
    log "Running gitlab-ctl reconfigure..."
    run_in_container "gitlab-ctl reconfigure"

    log "Restarting all GitLab services..."
    run_in_container "gitlab-ctl restart"

    if [[ "${DRY_RUN}" != "true" ]]; then
        log "Waiting 60 seconds for services to stabilize..."
        sleep 60
    fi

    log "Running gitlab-rake gitlab:check..."
    run_in_container "gitlab-rake gitlab:check SANITIZE=true" || \
        log "Warning: gitlab:check reported issues. Review output above."

    log "Services restarted."
}

# ---------------------------------------------------------------------------
# Post-restore verification
# ---------------------------------------------------------------------------
post_restore_verify() {
    log "=== Post-restore verification ==="

    run_in_container "gitlab-ctl status" || log "Warning: some services may not be running"

    log "Checking database connectivity..."
    run_in_container "gitlab-rails runner 'puts User.count rescue puts \"DB ERROR\"'" || true

    log "=== Post-restore verification complete ==="
    log "Next steps:"
    log "  1. Open http://YOUR_GITLAB_IP:8929 and verify login"
    log "  2. Check CI/CD variables are still accessible in a test project"
    log "  3. Verify runner registration tokens are intact"
    log "  4. Check audit logs for restore event"
}

# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------
main() {
    parse_args "$@"

    log "================================================================"
    log "GitLab restore started"
    log "  Backup file : ${BACKUP_FILENAME}"
    log "  Dry-run     : ${DRY_RUN}"
    log "================================================================"

    local backup_path
    backup_path=$(prechecks)

    echo ""
    echo "=========================================================="
    echo "  WARNING: This will OVERWRITE all current GitLab data."
    echo "  Backup: ${BACKUP_FILENAME}"
    echo "  Target: ${GITLAB_CONTAINER} on ${GITLAB_HOST}"
    echo "=========================================================="
    echo ""

    confirm "Proceed with restore?"

    stop_services
    stage_backup_in_container "${backup_path}"
    restore_application_data

    confirm "Application data restored. Proceed with restoring config files?"

    restore_config_files "${backup_path}"
    restart_services
    post_restore_verify

    log "================================================================"
    if [[ "${DRY_RUN}" == "true" ]]; then
        log "DRY-RUN complete. No changes were made."
    else
        log "Restore completed successfully."
    fi
    log "================================================================"
}

main "$@"
