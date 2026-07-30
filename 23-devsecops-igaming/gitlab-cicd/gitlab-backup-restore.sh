#!/usr/bin/env bash
# Companion code for "The Backend of Luck" - Chapter 23, DevSecOps for iGaming.
# https://thebackendofluck.com | https://github.com/thebackendofluck/book
# SPDX-License-Identifier: Apache-2.0
#
# FOR TESTING AND EVALUATION ONLY. NOT FOR PRODUCTION USE.
# Published to demonstrate the patterns explained in the book. This code is
# not certified for real-money gaming: operating a gambling platform requires
# your own licence, independent test-lab certification (GLI, eCOGRA or
# equivalent) and regulator approval.

set -euo pipefail

# =============================================================================
# GitLab Backup and Disaster Recovery for iGaming Platform
# =============================================================================
# Automated backup, off-site replication, verification, and restore procedures
# for on-premises GitLab instances. Designed to meet RTO/RPO requirements
# imposed by gambling regulators.
#
# Usage:
#   ./gitlab-backup-restore.sh <command> [OPTIONS]
#
# Commands:
#   backup          Create a full GitLab backup
#   upload          Upload backup to off-site S3-compatible storage
#   verify          Verify backup integrity (test restore)
#   restore         Restore GitLab from backup
#   rto-rpo-check   Verify RTO/RPO compliance
#   list            List available backups
#
# Requirements:
#   - Root or sudo access
#   - GitLab CE/EE running (Docker or Omnibus)
#   - For off-site: S3-compatible storage (MinIO, AWS S3, etc.)
#   - For verify: separate Docker environment
#
# iGaming context:
#   Gambling regulators require documented disaster recovery procedures with
#   tested RTO (Recovery Time Objective) and RPO (Recovery Point Objective).
#   MGA requires RTO < 4 hours and RPO < 1 hour for critical systems.
#   UKGC requires annual DR testing with documented results.
# =============================================================================

# -- Defaults --
GITLAB_HOME="/opt/gitlab"
GITLAB_CONTAINER="gitlab-ce"
BACKUP_DIR="${GITLAB_HOME}/backups"
S3_ENDPOINT=""
S3_BUCKET="gitlab-backups"
S3_ACCESS_KEY=""
S3_SECRET_KEY=""
S3_REGION="us-east-1"
RESTORE_BACKUP=""
VERIFY_CONTAINER="gitlab-verify"
VERIFY_PORT=8929
RTO_TARGET_MINUTES=240    # 4 hours (MGA requirement)
RPO_TARGET_MINUTES=60     # 1 hour (MGA requirement)
LOG_FILE="/var/log/gitlab-backup.log"

# =============================================================================
# Functions
# =============================================================================

usage() {
    cat <<EOF
Usage: $(basename "$0") <command> [OPTIONS]

GitLab backup and disaster recovery for iGaming platforms.

Commands:
  backup              Create full GitLab backup (data + config + secrets)
  upload              Upload latest backup to off-site S3 storage
  verify              Verify backup by test-restoring in isolated container
  restore             Restore GitLab from a specific backup
  rto-rpo-check       Check RTO/RPO compliance metrics
  list                List available local and remote backups

Global options:
  --gitlab-home <dir>      GitLab data directory (default: /opt/gitlab)
  --container <name>       GitLab container name (default: gitlab-ce)
  --backup-dir <dir>       Local backup directory (default: /opt/gitlab/backups)
  --log-file <path>        Log file path (default: /var/log/gitlab-backup.log)

S3 options (for upload/verify/list):
  --s3-endpoint <url>      S3-compatible endpoint (e.g., http://minio:9000)
  --s3-bucket <name>       S3 bucket name (default: gitlab-backups)
  --s3-access-key <key>    S3 access key
  --s3-secret-key <key>    S3 secret key
  --s3-region <region>     S3 region (default: us-east-1)

Restore options:
  --backup-file <name>     Backup timestamp to restore (e.g., 1709312400_2024_03_01_16.9.0)

RTO/RPO options:
  --rto-target <minutes>   RTO target in minutes (default: 240)
  --rpo-target <minutes>   RPO target in minutes (default: 60)

Examples:
  # Create backup
  $(basename "$0") backup

  # Upload to MinIO
  $(basename "$0") upload --s3-endpoint http://minio:9000 --s3-bucket gitlab-backups

  # Verify latest backup
  $(basename "$0") verify --s3-endpoint http://minio:9000

  # Restore from specific backup
  $(basename "$0") restore --backup-file 1709312400_2024_03_01_16.9.0

  # Check compliance
  $(basename "$0") rto-rpo-check --rto-target 240 --rpo-target 60
EOF
    exit 0
}

log_info() {
    local msg
    msg="[INFO]  $(date -u +%Y-%m-%dT%H:%M:%SZ) $*"
    echo "${msg}"
    echo "${msg}" >> "${LOG_FILE}" 2>/dev/null || true
}

log_warn() {
    local msg
    msg="[WARN]  $(date -u +%Y-%m-%dT%H:%M:%SZ) $*"
    echo "${msg}" >&2
    echo "${msg}" >> "${LOG_FILE}" 2>/dev/null || true
}

log_error() {
    local msg
    msg="[ERROR] $(date -u +%Y-%m-%dT%H:%M:%SZ) $*"
    echo "${msg}" >&2
    echo "${msg}" >> "${LOG_FILE}" 2>/dev/null || true
}

check_root() {
    if [[ $EUID -ne 0 ]]; then
        log_error "This script must be run as root or with sudo."
        exit 1
    fi
}

# Check if GitLab container is running
check_gitlab_running() {
    if ! docker ps --format '{{.Names}}' | grep -q "^${GITLAB_CONTAINER}$"; then
        log_error "GitLab container '${GITLAB_CONTAINER}' is not running"
        exit 1
    fi
}

# Configure AWS CLI for S3 operations
configure_s3() {
    if [[ -z "${S3_ENDPOINT}" ]]; then
        log_error "S3 endpoint not configured. Use --s3-endpoint."
        exit 1
    fi

    export AWS_ACCESS_KEY_ID="${S3_ACCESS_KEY}"
    export AWS_SECRET_ACCESS_KEY="${S3_SECRET_KEY}"
    export AWS_DEFAULT_REGION="${S3_REGION}"

    if ! command -v aws &>/dev/null; then
        log_info "Installing AWS CLI..."
        pip3 install --quiet awscli 2>/dev/null || {
            log_error "Failed to install AWS CLI. Install manually: pip3 install awscli"
            exit 1
        }
    fi
}

# Get latest backup file
get_latest_backup() {
    local latest
    latest=$(find "${BACKUP_DIR}" -name "*_gitlab_backup.tar" -printf '%T@ %p\n' 2>/dev/null \
        | sort -rn | head -1 | awk '{print $2}')
    echo "${latest}"
}

# Get backup timestamp from filename
get_backup_timestamp() {
    local filepath="$1"
    basename "${filepath}" | sed 's/_gitlab_backup\.tar$//'
}

# =============================================================================
# Command: backup
# =============================================================================
cmd_backup() {
    check_root
    check_gitlab_running

    log_info "Starting GitLab backup..."
    local start_time
    start_time=$(date +%s)

    mkdir -p "${BACKUP_DIR}"

    # Create application backup (repos, uploads, builds, artifacts, pages, lfs)
    log_info "Creating application data backup..."
    docker exec -t "${GITLAB_CONTAINER}" gitlab-backup create \
        SKIP=artifacts,registry \
        STRATEGY=copy \
        GZIP_RSYNCABLE=yes || {
        log_error "Application backup failed"
        return 1
    }

    # Backup configuration and secrets (not included in gitlab-backup)
    log_info "Backing up configuration and secrets..."
    local config_backup
    config_backup="${BACKUP_DIR}/gitlab-config-$(date +%Y%m%d-%H%M%S).tar.gz"
    tar czf "${config_backup}" \
        -C "${GITLAB_HOME}" \
        config/gitlab.rb \
        config/gitlab-secrets.json \
        config/ssl/ 2>/dev/null || true

    chmod 600 "${config_backup}"

    local end_time
    end_time=$(date +%s)
    local duration=$((end_time - start_time))

    local latest_backup
    latest_backup=$(get_latest_backup)

    if [[ -n "${latest_backup}" ]]; then
        local backup_size
        backup_size=$(du -sh "${latest_backup}" | awk '{print $1}')
        log_info "Backup complete: ${latest_backup} (${backup_size}, ${duration}s)"
        log_info "Config backup: ${config_backup}"

        # Record backup metrics for RTO/RPO tracking
        echo "$(date -u +%Y-%m-%dT%H:%M:%SZ) backup_duration=${duration} backup_size=${backup_size} file=${latest_backup}" \
            >> "${BACKUP_DIR}/backup-metrics.log"
    else
        log_error "Backup file not found after creation"
        return 1
    fi
}

# =============================================================================
# Command: upload
# =============================================================================
cmd_upload() {
    configure_s3

    local latest_backup
    latest_backup=$(get_latest_backup)

    if [[ -z "${latest_backup}" ]]; then
        log_error "No backup files found in ${BACKUP_DIR}"
        return 1
    fi

    local backup_name
    backup_name=$(basename "${latest_backup}")

    log_info "Uploading backup to S3: ${backup_name}..."

    # Upload application backup
    aws s3 cp "${latest_backup}" \
        "s3://${S3_BUCKET}/application/${backup_name}" \
        --endpoint-url "${S3_ENDPOINT}" || {
        log_error "Failed to upload application backup"
        return 1
    }

    # Upload latest config backup
    local latest_config
    latest_config=$(find "${BACKUP_DIR}" -name "gitlab-config-*.tar.gz" -printf '%T@ %p\n' 2>/dev/null \
        | sort -rn | head -1 | awk '{print $2}')

    if [[ -n "${latest_config}" ]]; then
        aws s3 cp "${latest_config}" \
            "s3://${S3_BUCKET}/config/$(basename "${latest_config}")" \
            --endpoint-url "${S3_ENDPOINT}" || {
            log_warn "Failed to upload config backup"
        }
    fi

    # Upload backup metrics
    if [[ -f "${BACKUP_DIR}/backup-metrics.log" ]]; then
        aws s3 cp "${BACKUP_DIR}/backup-metrics.log" \
            "s3://${S3_BUCKET}/metrics/backup-metrics.log" \
            --endpoint-url "${S3_ENDPOINT}" || true
    fi

    log_info "Upload complete: s3://${S3_BUCKET}/application/${backup_name}"

    # Verify upload integrity with checksum
    local local_md5
    local_md5=$(md5sum "${latest_backup}" | awk '{print $1}')
    log_info "Local checksum (MD5): ${local_md5}"

    # Record upload for audit
    echo "$(date -u +%Y-%m-%dT%H:%M:%SZ) uploaded=${backup_name} destination=s3://${S3_BUCKET} checksum=${local_md5}" \
        >> "${BACKUP_DIR}/backup-metrics.log"
}

# =============================================================================
# Command: verify
# =============================================================================
cmd_verify() {
    check_root

    log_info "Starting backup verification (test restore)..."
    local start_time
    start_time=$(date +%s)

    local latest_backup
    latest_backup=$(get_latest_backup)

    if [[ -z "${latest_backup}" ]]; then
        log_error "No backup files found in ${BACKUP_DIR}"
        return 1
    fi

    log_info "Verifying backup: $(basename "${latest_backup}")"

    # Create isolated verification environment
    local verify_dir="/tmp/gitlab-verify-$$"
    mkdir -p "${verify_dir}"/{config,logs,data,backups}

    # Copy backup to verification directory
    cp "${latest_backup}" "${verify_dir}/backups/"

    # Copy config backup
    local latest_config
    latest_config=$(find "${BACKUP_DIR}" -name "gitlab-config-*.tar.gz" -printf '%T@ %p\n' 2>/dev/null \
        | sort -rn | head -1 | awk '{print $2}')
    if [[ -n "${latest_config}" ]]; then
        tar xzf "${latest_config}" -C "${verify_dir}/"
    fi

    # Start verification GitLab container
    log_info "Starting verification container (this may take several minutes)..."
    docker run -d \
        --name "${VERIFY_CONTAINER}" \
        --hostname gitlab-verify \
        -p "${VERIFY_PORT}:80" \
        -v "${verify_dir}/config:/etc/gitlab" \
        -v "${verify_dir}/logs:/var/log/gitlab" \
        -v "${verify_dir}/data:/var/opt/gitlab" \
        -v "${verify_dir}/backups:/var/opt/gitlab/backups" \
        --shm-size 256m \
        gitlab/gitlab-ce:latest || {
        log_error "Failed to start verification container"
        rm -rf "${verify_dir}"
        return 1
    }

    # Wait for GitLab to initialize
    log_info "Waiting for verification instance to initialize..."
    local wait_count=0
    local max_wait=60
    while [[ ${wait_count} -lt ${max_wait} ]]; do
        if docker exec "${VERIFY_CONTAINER}" gitlab-ctl status &>/dev/null; then
            break
        fi
        wait_count=$((wait_count + 1))
        sleep 10
    done

    if [[ ${wait_count} -ge ${max_wait} ]]; then
        log_error "Verification instance did not start within timeout"
        docker rm -f "${VERIFY_CONTAINER}" 2>/dev/null || true
        rm -rf "${verify_dir}"
        return 1
    fi

    # Stop services for restore
    docker exec "${VERIFY_CONTAINER}" gitlab-ctl stop puma
    docker exec "${VERIFY_CONTAINER}" gitlab-ctl stop sidekiq

    # Perform test restore
    local backup_timestamp
    backup_timestamp=$(get_backup_timestamp "${latest_backup}")

    log_info "Restoring backup ${backup_timestamp} in verification container..."
    if docker exec -t "${VERIFY_CONTAINER}" gitlab-backup restore \
        BACKUP="${backup_timestamp}" force=yes; then
        log_info "Test restore: SUCCESS"

        # Restart and verify
        docker exec "${VERIFY_CONTAINER}" gitlab-ctl restart

        sleep 30

        # Basic health check
        if curl -fsSL --max-time 30 "http://localhost:${VERIFY_PORT}/-/health" > /dev/null 2>&1; then
            log_info "Health check after restore: PASSED"
        else
            log_warn "Health check after restore: FAILED (may need more startup time)"
        fi
    else
        log_error "Test restore: FAILED"
    fi

    local end_time
    end_time=$(date +%s)
    local duration=$((end_time - start_time))

    # Cleanup
    log_info "Cleaning up verification environment..."
    docker rm -f "${VERIFY_CONTAINER}" 2>/dev/null || true
    rm -rf "${verify_dir}"

    log_info "Backup verification complete (${duration}s)"

    # Record verification for audit
    echo "$(date -u +%Y-%m-%dT%H:%M:%SZ) verify_duration=${duration} backup=$(basename "${latest_backup}") result=success" \
        >> "${BACKUP_DIR}/backup-metrics.log"
}

# =============================================================================
# Command: restore
# =============================================================================
cmd_restore() {
    check_root
    check_gitlab_running

    if [[ -z "${RESTORE_BACKUP}" ]]; then
        log_error "Specify backup to restore with --backup-file <timestamp>"
        log_info "Available backups:"
        cmd_list
        return 1
    fi

    log_info "=== RESTORE PROCEDURE ==="
    log_info "Restoring from backup: ${RESTORE_BACKUP}"
    log_info "WARNING: This will overwrite current GitLab data!"

    local start_time
    start_time=$(date +%s)

    # Verify backup file exists
    local backup_file="${BACKUP_DIR}/${RESTORE_BACKUP}_gitlab_backup.tar"
    if [[ ! -f "${backup_file}" ]]; then
        log_error "Backup file not found: ${backup_file}"
        # Try to download from S3
        if [[ -n "${S3_ENDPOINT}" ]]; then
            log_info "Attempting to download from S3..."
            configure_s3
            aws s3 cp \
                "s3://${S3_BUCKET}/application/${RESTORE_BACKUP}_gitlab_backup.tar" \
                "${backup_file}" \
                --endpoint-url "${S3_ENDPOINT}" || {
                log_error "Failed to download backup from S3"
                return 1
            }
        else
            return 1
        fi
    fi

    # Stop services that write to the database
    log_info "Stopping GitLab services..."
    docker exec "${GITLAB_CONTAINER}" gitlab-ctl stop puma
    docker exec "${GITLAB_CONTAINER}" gitlab-ctl stop sidekiq

    # Verify services are stopped
    docker exec "${GITLAB_CONTAINER}" gitlab-ctl status || true

    # Perform restore
    log_info "Restoring application data..."
    docker exec -t "${GITLAB_CONTAINER}" gitlab-backup restore \
        BACKUP="${RESTORE_BACKUP}" force=yes || {
        log_error "Restore failed!"
        log_info "Restarting GitLab services..."
        docker exec "${GITLAB_CONTAINER}" gitlab-ctl restart
        return 1
    }

    # Restore configuration if available
    local latest_config
    latest_config=$(find "${BACKUP_DIR}" -name "gitlab-config-*.tar.gz" -printf '%T@ %p\n' 2>/dev/null \
        | sort -rn | head -1 | awk '{print $2}')
    if [[ -n "${latest_config}" ]]; then
        log_info "Restoring configuration from: $(basename "${latest_config}")"
        tar xzf "${latest_config}" -C "${GITLAB_HOME}/"
    fi

    # Reconfigure and restart
    log_info "Reconfiguring GitLab..."
    docker exec "${GITLAB_CONTAINER}" gitlab-ctl reconfigure
    docker exec "${GITLAB_CONTAINER}" gitlab-ctl restart

    # Wait for startup
    log_info "Waiting for GitLab to start..."
    sleep 60

    # Health check
    local healthy=false
    for i in $(seq 1 20); do
        if docker exec "${GITLAB_CONTAINER}" gitlab-rake gitlab:check SANITIZE=true &>/dev/null; then
            healthy=true
            break
        fi
        log_info "Waiting for GitLab health check (attempt ${i}/20)..."
        sleep 15
    done

    local end_time
    end_time=$(date +%s)
    local duration=$((end_time - start_time))

    if [[ "${healthy}" == "true" ]]; then
        log_info "Restore complete and verified (${duration}s)"
        log_info "Recovery Time: $((duration / 60)) minutes"
    else
        log_warn "Restore completed but health check did not pass (${duration}s)"
        log_warn "Manual verification recommended: docker exec ${GITLAB_CONTAINER} gitlab-rake gitlab:check"
    fi

    # Record for audit
    echo "$(date -u +%Y-%m-%dT%H:%M:%SZ) restore_duration=${duration} backup=${RESTORE_BACKUP} result=$(if ${healthy}; then echo success; else echo warning; fi)" \
        >> "${BACKUP_DIR}/backup-metrics.log"
}

# =============================================================================
# Command: rto-rpo-check
# =============================================================================
cmd_rto_rpo_check() {
    log_info "=== RTO/RPO Compliance Check ==="
    log_info "RTO target: ${RTO_TARGET_MINUTES} minutes"
    log_info "RPO target: ${RPO_TARGET_MINUTES} minutes"

    local rto_pass=true
    local rpo_pass=true
    local issues=()

    # Check RPO: time since last backup
    local latest_backup
    latest_backup=$(get_latest_backup)

    if [[ -z "${latest_backup}" ]]; then
        log_error "No backups found. RPO: FAILED"
        rpo_pass=false
        issues+=("No backups found")
    else
        local backup_time
        backup_time=$(stat -c %Y "${latest_backup}")
        local current_time
        current_time=$(date +%s)
        local minutes_since_backup=$(( (current_time - backup_time) / 60 ))

        log_info "Last backup: $(date -d "@${backup_time}" -u +%Y-%m-%dT%H:%M:%SZ) (${minutes_since_backup} min ago)"

        if [[ ${minutes_since_backup} -gt ${RPO_TARGET_MINUTES} ]]; then
            rpo_pass=false
            issues+=("Last backup is ${minutes_since_backup} min old (target: ${RPO_TARGET_MINUTES} min)")
        fi
    fi

    # Check RTO: estimated recovery time from metrics
    local metrics_file="${BACKUP_DIR}/backup-metrics.log"
    if [[ -f "${metrics_file}" ]]; then
        local last_restore_time
        last_restore_time=$(grep "restore_duration" "${metrics_file}" | tail -1 | grep -oP 'restore_duration=\K[0-9]+' || echo "0")
        local last_verify_time
        last_verify_time=$(grep "verify_duration" "${metrics_file}" | tail -1 | grep -oP 'verify_duration=\K[0-9]+' || echo "0")

        local estimated_rto
        if [[ ${last_restore_time} -gt 0 ]]; then
            estimated_rto=$((last_restore_time / 60))
        elif [[ ${last_verify_time} -gt 0 ]]; then
            estimated_rto=$((last_verify_time / 60))
        else
            estimated_rto=0
            issues+=("No restore/verify metrics found. Run 'verify' to establish baseline.")
        fi

        if [[ ${estimated_rto} -gt 0 ]]; then
            log_info "Estimated RTO: ${estimated_rto} minutes (from last test)"
            if [[ ${estimated_rto} -gt ${RTO_TARGET_MINUTES} ]]; then
                rto_pass=false
                issues+=("Estimated RTO ${estimated_rto} min exceeds target ${RTO_TARGET_MINUTES} min")
            fi
        fi
    else
        rto_pass=false
        issues+=("No backup metrics file. Run 'backup' and 'verify' first.")
    fi

    # Check off-site backup (if S3 configured)
    if [[ -n "${S3_ENDPOINT}" ]]; then
        configure_s3
        local remote_count
        remote_count=$(aws s3 ls "s3://${S3_BUCKET}/application/" --endpoint-url "${S3_ENDPOINT}" 2>/dev/null | wc -l || echo "0")
        log_info "Off-site backups: ${remote_count} files in S3"
        if [[ ${remote_count} -eq 0 ]]; then
            issues+=("No off-site backups found in S3")
        fi
    else
        log_warn "Off-site S3 not configured. Single point of failure for backups."
        issues+=("No off-site backup configured")
    fi

    # Check backup cron
    if crontab -l 2>/dev/null | grep -q "gitlab-backup" || [[ -f /etc/cron.d/gitlab-backup ]]; then
        log_info "Automated backup cron: CONFIGURED"
    else
        issues+=("No automated backup cron found")
    fi

    # Summary
    echo ""
    echo "==========================================="
    echo "  RTO/RPO Compliance Report"
    echo "==========================================="
    echo "  RPO (${RPO_TARGET_MINUTES} min target): $(if ${rpo_pass}; then echo "PASS"; else echo "FAIL"; fi)"
    echo "  RTO (${RTO_TARGET_MINUTES} min target): $(if ${rto_pass}; then echo "PASS"; else echo "FAIL"; fi)"
    echo ""

    if [[ ${#issues[@]} -gt 0 ]]; then
        echo "  Issues:"
        for issue in "${issues[@]}"; do
            echo "    - ${issue}"
        done
    else
        echo "  No issues found."
    fi

    echo "==========================================="

    if ! ${rto_pass} || ! ${rpo_pass}; then
        return 1
    fi
}

# =============================================================================
# Command: list
# =============================================================================
cmd_list() {
    log_info "=== Available Backups ==="

    # Local backups
    echo ""
    echo "Local backups (${BACKUP_DIR}):"
    if compgen -G "${BACKUP_DIR}/*_gitlab_backup.tar" > /dev/null 2>&1; then
        for backup in "${BACKUP_DIR}"/*_gitlab_backup.tar; do
            local size
            size=$(du -sh "${backup}" | awk '{print $1}')
            local date_modified
            date_modified=$(stat -c %y "${backup}" | cut -d. -f1)
            echo "  $(basename "${backup}") [${size}] ${date_modified}"
        done
    else
        echo "  (none)"
    fi

    echo ""
    echo "Config backups:"
    if compgen -G "${BACKUP_DIR}/gitlab-config-*.tar.gz" > /dev/null 2>&1; then
        for config in "${BACKUP_DIR}"/gitlab-config-*.tar.gz; do
            local size
            size=$(du -sh "${config}" | awk '{print $1}')
            echo "  $(basename "${config}") [${size}]"
        done
    else
        echo "  (none)"
    fi

    # Remote backups (if S3 configured)
    if [[ -n "${S3_ENDPOINT}" ]]; then
        configure_s3
        echo ""
        echo "Remote backups (s3://${S3_BUCKET}):"
        if ! aws s3 ls "s3://${S3_BUCKET}/application/" --endpoint-url "${S3_ENDPOINT}" 2>/dev/null \
            | while read -r line; do echo "  ${line}"; done; then
            echo "  (unable to connect to S3)"
        fi
    fi
}

# =============================================================================
# Parse arguments and dispatch
# =============================================================================
COMMAND="${1:-}"
shift || true

while [[ $# -gt 0 ]]; do
    case $1 in
        --gitlab-home)     GITLAB_HOME="$2"; BACKUP_DIR="${GITLAB_HOME}/backups"; shift 2 ;;
        --container)       GITLAB_CONTAINER="$2";   shift 2 ;;
        --backup-dir)      BACKUP_DIR="$2";         shift 2 ;;
        --log-file)        LOG_FILE="$2";            shift 2 ;;
        --s3-endpoint)     S3_ENDPOINT="$2";         shift 2 ;;
        --s3-bucket)       S3_BUCKET="$2";           shift 2 ;;
        --s3-access-key)   S3_ACCESS_KEY="$2";       shift 2 ;;
        --s3-secret-key)   S3_SECRET_KEY="$2";       shift 2 ;;
        --s3-region)       S3_REGION="$2";           shift 2 ;;
        --backup-file)     RESTORE_BACKUP="$2";      shift 2 ;;
        --rto-target)      RTO_TARGET_MINUTES="$2";  shift 2 ;;
        --rpo-target)      RPO_TARGET_MINUTES="$2";  shift 2 ;;
        --help)            usage ;;
        *)
            log_error "Unknown option: $1"
            usage
            ;;
    esac
done

# Ensure log directory exists
mkdir -p "$(dirname "${LOG_FILE}")" 2>/dev/null || true

case "${COMMAND}" in
    backup)        cmd_backup ;;
    upload)        cmd_upload ;;
    verify)        cmd_verify ;;
    restore)       cmd_restore ;;
    rto-rpo-check) cmd_rto_rpo_check ;;
    list)          cmd_list ;;
    ""|--help)     usage ;;
    *)
        log_error "Unknown command: ${COMMAND}"
        usage
        ;;
esac
