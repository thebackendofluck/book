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
# gitlab-backup.sh — Production GitLab CE backup script
#
# Backs up:
#   - GitLab application data via gitlab-backup create
#   - /etc/gitlab/gitlab.rb (configuration)
#   - /etc/gitlab/gitlab-secrets.json (CRITICAL: encrypted data keys)
#
# Rotation:
#   - Keep 7 daily backups
#   - Keep 4 weekly backups (Sunday)
#
# Dependencies: docker, ssh (for remote GitLab host), jq
# Cron example (on your GitLab host):
#   0 2 * * * /opt/scripts/gitlab-backup.sh >> /var/log/gitlab-backup.log 2>&1

set -euo pipefail

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------
GITLAB_CONTAINER="gitlab"
GITLAB_HOST="YOUR_GITLAB_USER@YOUR_GITLAB_HOST"

# ZFS backup destination on your GitLab host
BACKUP_ROOT="/data/backups/gitlab"
DAILY_DIR="${BACKUP_ROOT}/daily"
WEEKLY_DIR="${BACKUP_ROOT}/weekly"
SECRETS_DIR="${BACKUP_ROOT}/secrets"

# Retention
KEEP_DAILY=7
KEEP_WEEKLY=4

# Logging
LOG_FILE="/var/log/gitlab-backup.log"
TIMESTAMP=$(date +"%Y-%m-%d_%H-%M-%S")
DAY_OF_WEEK=$(date +%u)  # 1=Mon … 7=Sun

# Dashboard webhook
WEBHOOK_URL="https://your-dashboard.example.com/webhooks/gitlab-backup"
WEBHOOK_TOKEN="${GITLAB_BACKUP_WEBHOOK_TOKEN:-}"

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
    notify_webhook "failure" "$*"
    exit 1
}

notify_webhook() {
    local status="$1"
    local message="$2"
    local backup_size="${3:-unknown}"

    if [[ -z "${WEBHOOK_URL}" ]]; then
        return 0
    fi

    local payload
    payload=$(jq -n \
        --arg status "${status}" \
        --arg message "${message}" \
        --arg timestamp "${TIMESTAMP}" \
        --arg host "YOUR_GITLAB_HOST" \
        --arg backup_size "${backup_size}" \
        '{
            event: "gitlab_backup",
            status: $status,
            message: $message,
            timestamp: $timestamp,
            host: $host,
            backup_size: $backup_size
        }')

    local curl_args=(-s -o /dev/null -w "%{http_code}" -X POST
        -H "Content-Type: application/json"
        -H "X-Backup-Token: ${WEBHOOK_TOKEN}"
        --data "${payload}"
        --max-time 10
        "${WEBHOOK_URL}")

    local http_code
    http_code=$(curl "${curl_args[@]}" 2>/dev/null) || true
    if [[ "${http_code}" != "200" && "${http_code}" != "204" ]]; then
        log "Warning: webhook returned HTTP ${http_code}"
    fi
}

run_on_gitlab_host() {
    # Run a command on the GitLab host via SSH.
    # Usage: run_on_gitlab_host "command string"
    ssh -o BatchMode=yes -o ConnectTimeout=10 "${GITLAB_HOST}" "$1"
}

# ---------------------------------------------------------------------------
# Pre-flight checks
# ---------------------------------------------------------------------------
preflight_checks() {
    log "Running pre-flight checks..."

    # SSH connectivity
    if ! run_on_gitlab_host "true" 2>/dev/null; then
        die "Cannot SSH to ${GITLAB_HOST}"
    fi

    # Container running
    local container_status
    container_status=$(run_on_gitlab_host "docker inspect --format '{{.State.Status}}' ${GITLAB_CONTAINER} 2>/dev/null") || true
    if [[ "${container_status}" != "running" ]]; then
        die "GitLab container '${GITLAB_CONTAINER}' is not running (status: ${container_status:-not found})"
    fi

    # Backup directories exist or can be created
    run_on_gitlab_host "mkdir -p '${DAILY_DIR}' '${WEEKLY_DIR}' '${SECRETS_DIR}'" \
        || die "Cannot create backup directories on your-gitlab-host"

    # ZFS pool check
    local zfs_free
    zfs_free=$(run_on_gitlab_host "df -BG '${BACKUP_ROOT}' | awk 'NR==2{print \$4}' | tr -d 'G'") || true
    if [[ -n "${zfs_free}" && "${zfs_free}" -lt 20 ]]; then
        die "Less than 20 GB free on ZFS pool at ${BACKUP_ROOT} (found: ${zfs_free}G)"
    fi

    log "Pre-flight checks passed."
}

# ---------------------------------------------------------------------------
# GitLab application backup
# ---------------------------------------------------------------------------
run_gitlab_backup() {
    log "Starting gitlab-backup create inside container..."

    # SKIP=registry skips the container registry blobs (often very large).
    # Remove SKIP=registry if you want a full backup including registry images.
    run_on_gitlab_host \
        "docker exec ${GITLAB_CONTAINER} gitlab-backup create SKIP=registry BACKUP=${TIMESTAMP}" \
        || die "gitlab-backup create failed"

    log "gitlab-backup create completed."
}

# ---------------------------------------------------------------------------
# Copy backup tarball from container's backup dir to ZFS
# ---------------------------------------------------------------------------
copy_backup_to_zfs() {
    log "Locating backup tarball in container..."

    local backup_file
    backup_file=$(run_on_gitlab_host \
        "docker exec ${GITLAB_CONTAINER} ls -1 /var/opt/gitlab/backups/ \
         | grep '${TIMESTAMP}' | head -1") || true

    if [[ -z "${backup_file}" ]]; then
        # Fallback: grab the most recent backup file
        backup_file=$(run_on_gitlab_host \
            "docker exec ${GITLAB_CONTAINER} ls -1t /var/opt/gitlab/backups/*.tar 2>/dev/null | head -1 | xargs basename") \
            || die "No backup tarball found in container"
    fi

    log "Backup tarball: ${backup_file}"

    local dest_dir="${DAILY_DIR}"
    local dest_file="${dest_dir}/${backup_file}"

    # Copy out of container to ZFS
    run_on_gitlab_host \
        "docker cp '${GITLAB_CONTAINER}:/var/opt/gitlab/backups/${backup_file}' '${dest_file}'" \
        || die "Failed to copy backup tarball to ZFS"

    log "Backup copied to ${dest_file}"

    # If Sunday, also place in weekly dir
    if [[ "${DAY_OF_WEEK}" -eq 7 ]]; then
        run_on_gitlab_host "cp '${dest_file}' '${WEEKLY_DIR}/${backup_file}'" \
            || log "Warning: failed to copy to weekly directory"
        log "Weekly backup saved: ${WEEKLY_DIR}/${backup_file}"
    fi

    # Return filename for integrity check
    echo "${backup_file}"
}

# ---------------------------------------------------------------------------
# Backup configuration and secrets
# ---------------------------------------------------------------------------
backup_config_files() {
    log "Backing up gitlab.rb and gitlab-secrets.json..."

    # gitlab.rb — GitLab Omnibus configuration
    run_on_gitlab_host \
        "docker cp '${GITLAB_CONTAINER}:/etc/gitlab/gitlab.rb' \
         '${SECRETS_DIR}/gitlab.rb.${TIMESTAMP}'" \
        || die "Failed to backup gitlab.rb"

    # gitlab-secrets.json — Contains db_key_base and other encryption keys.
    # WITHOUT THIS FILE, encrypted CI/CD variables, 2FA tokens, and webhook
    # secrets CANNOT be decrypted after a restore.
    run_on_gitlab_host \
        "docker cp '${GITLAB_CONTAINER}:/etc/gitlab/gitlab-secrets.json' \
         '${SECRETS_DIR}/gitlab-secrets.json.${TIMESTAMP}'" \
        || die "Failed to backup gitlab-secrets.json"

    # Set restrictive permissions on secrets
    run_on_gitlab_host \
        "chmod 600 '${SECRETS_DIR}/gitlab-secrets.json.${TIMESTAMP}' \
                   '${SECRETS_DIR}/gitlab.rb.${TIMESTAMP}'" \
        || true

    log "Configuration files backed up to ${SECRETS_DIR}/"
}

# ---------------------------------------------------------------------------
# Integrity verification
# ---------------------------------------------------------------------------
verify_backup() {
    local backup_file="$1"

    log "Verifying backup integrity: ${backup_file}"

    local dest_file="${DAILY_DIR}/${backup_file}"

    # Check file exists and is non-zero
    local file_size
    file_size=$(run_on_gitlab_host "stat -c%s '${dest_file}' 2>/dev/null") || true
    if [[ -z "${file_size}" || "${file_size}" -eq 0 ]]; then
        die "Backup file is empty or missing: ${dest_file}"
    fi

    # Verify tar integrity
    run_on_gitlab_host "tar --list --file '${dest_file}' > /dev/null 2>&1" \
        || die "Backup tarball failed tar integrity check: ${dest_file}"

    # Compute and store SHA256 checksum
    local checksum
    checksum=$(run_on_gitlab_host "sha256sum '${dest_file}' | awk '{print \$1}'")
    run_on_gitlab_host "echo '${checksum}  ${backup_file}' > '${dest_file}.sha256'"

    log "Integrity check passed. Size: ${file_size} bytes. SHA256: ${checksum}"
    echo "${file_size}"
}

# ---------------------------------------------------------------------------
# Backup rotation
# ---------------------------------------------------------------------------
rotate_backups() {
    log "Rotating daily backups (keeping last ${KEEP_DAILY})..."
    run_on_gitlab_host \
        "ls -1t '${DAILY_DIR}'/*.tar 2>/dev/null | tail -n +$((KEEP_DAILY + 1)) | xargs -r rm -f --" \
        || true
    # Also clean associated .sha256 files for removed tarballs
    run_on_gitlab_host \
        "ls -1t '${DAILY_DIR}'/*.sha256 2>/dev/null | tail -n +$((KEEP_DAILY + 1)) | xargs -r rm -f --" \
        || true

    log "Rotating weekly backups (keeping last ${KEEP_WEEKLY})..."
    run_on_gitlab_host \
        "ls -1t '${WEEKLY_DIR}'/*.tar 2>/dev/null | tail -n +$((KEEP_WEEKLY + 1)) | xargs -r rm -f --" \
        || true

    log "Rotating secrets backups (keeping last $((KEEP_DAILY * 2)))..."
    run_on_gitlab_host \
        "ls -1t '${SECRETS_DIR}'/gitlab-secrets.json.* 2>/dev/null \
         | tail -n +$((KEEP_DAILY * 2 + 1)) | xargs -r rm -f --" \
        || true
    run_on_gitlab_host \
        "ls -1t '${SECRETS_DIR}'/gitlab.rb.* 2>/dev/null \
         | tail -n +$((KEEP_DAILY * 2 + 1)) | xargs -r rm -f --" \
        || true

    log "Rotation complete."
}

# ---------------------------------------------------------------------------
# Clean up backup from container (prevent disk fill inside container volume)
# ---------------------------------------------------------------------------
cleanup_container_backup() {
    local backup_file="$1"
    log "Cleaning up backup tarball from container..."
    run_on_gitlab_host \
        "docker exec ${GITLAB_CONTAINER} rm -f '/var/opt/gitlab/backups/${backup_file}'" \
        || log "Warning: failed to remove backup from container"
}

# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------
main() {
    log "================================================================"
    log "GitLab backup started — ${TIMESTAMP}"
    log "================================================================"

    preflight_checks
    run_gitlab_backup

    local backup_file
    backup_file=$(copy_backup_to_zfs)

    backup_config_files

    local file_size
    file_size=$(verify_backup "${backup_file}")

    cleanup_container_backup "${backup_file}"

    rotate_backups

    local size_human
    size_human=$(run_on_gitlab_host "du -sh '${DAILY_DIR}/${backup_file}' | awk '{print \$1}'") || size_human="unknown"

    log "================================================================"
    log "Backup completed successfully."
    log "  Tarball : ${DAILY_DIR}/${backup_file}"
    log "  Size    : ${size_human}"
    log "  Secrets : ${SECRETS_DIR}/gitlab-secrets.json.${TIMESTAMP}"
    log "================================================================"

    notify_webhook "success" "GitLab backup completed" "${size_human}"
}

main "$@"
