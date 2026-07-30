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
# On-Premises Deployment Script for iGaming Platform
# =============================================================================
# Ansible-based rolling deployment to bare metal or VM infrastructure.
# Handles database migrations, service ordering, health checks, automatic
# rollback, and compliance audit trail.
#
# Usage:
#   ./deploy-on-premises.sh --env <staging|production> --version <tag> \
#       [--inventory /path/to/inventory] [--dry-run] [--skip-migrations] \
#       [--services pam,wallet,gal] [--rollback-on-failure] \
#       [--notify slack|jira|both]
#
# Requirements:
#   - Ansible 2.15+ installed
#   - SSH access to target hosts
#   - Ansible inventory configured
#   - Docker on target hosts
#
# iGaming context:
#   Regulated gambling platforms require auditable deployments with rollback
#   capability. Every deployment action is logged to an immutable audit trail.
#   Service dependencies must be respected: PAM before Wallet, Wallet before
#   GAL, etc.
# =============================================================================

# -- Defaults --
ENVIRONMENT=""
VERSION=""
INVENTORY=""
DRY_RUN=false
SKIP_MIGRATIONS=false
ROLLBACK_ON_FAILURE=true
SERVICES="pam,wallet,gal,compliance,responsible-gaming,game-control"
NOTIFY=""
AUDIT_LOG="/var/log/igaming/deploy-audit.log"
DEPLOY_TIMEOUT=600
HEALTH_CHECK_RETRIES=10
HEALTH_CHECK_INTERVAL=15
SLACK_WEBHOOK_URL="${SLACK_WEBHOOK_URL:-}"
JIRA_URL="${JIRA_URL:-}"
JIRA_USER="${JIRA_USER:-}"
JIRA_TOKEN="${JIRA_TOKEN:-}"

# Service deployment order (respects dependencies)
# PAM (auth) -> Wallet (funds) -> GAL (games) -> Compliance -> RG -> Game Control
declare -a SERVICE_ORDER=(
    "pam"
    "wallet"
    "gal"
    "compliance"
    "responsible-gaming"
    "game-control"
)

# =============================================================================
# Functions
# =============================================================================

usage() {
    cat <<EOF
Usage: $(basename "$0") [OPTIONS]

Deploy iGaming platform to on-premises infrastructure.

Required:
  --env <environment>         Target environment: staging, production
  --version <tag>             Docker image tag to deploy

Optional:
  --inventory <path>          Ansible inventory file (default: deploy/ansible/inventory/<env>)
  --dry-run                   Show what would be deployed without making changes
  --skip-migrations           Skip database migrations
  --services <list>           Comma-separated services to deploy (default: all)
  --no-rollback               Disable automatic rollback on failure
  --notify <target>           Notification target: slack, jira, both
  --timeout <seconds>         Deployment timeout per service (default: 600)
  --help                      Show this help

Service order (dependency chain):
  pam -> wallet -> gal -> compliance -> responsible-gaming -> game-control

Examples:
  # Deploy all services to staging
  $(basename "$0") --env staging --version abc1234

  # Production with notifications
  $(basename "$0") --env production --version v2.1.0 --notify both

  # Deploy specific services only
  $(basename "$0") --env staging --version abc1234 --services pam,wallet

  # Dry run
  $(basename "$0") --env production --version v2.1.0 --dry-run
EOF
    exit 0
}

log_info() {
    local msg
    msg="[INFO]  $(date -u +%Y-%m-%dT%H:%M:%SZ) $*"
    echo "${msg}"
    echo "${msg}" >> "${AUDIT_LOG}" 2>/dev/null || true
}

log_warn() {
    local msg
    msg="[WARN]  $(date -u +%Y-%m-%dT%H:%M:%SZ) $*"
    echo "${msg}" >&2
    echo "${msg}" >> "${AUDIT_LOG}" 2>/dev/null || true
}

log_error() {
    local msg
    msg="[ERROR] $(date -u +%Y-%m-%dT%H:%M:%SZ) $*"
    echo "${msg}" >&2
    echo "${msg}" >> "${AUDIT_LOG}" 2>/dev/null || true
}

audit_log() {
    local msg
    msg="[AUDIT] $(date -u +%Y-%m-%dT%H:%M:%SZ) deployer=$(whoami) env=${ENVIRONMENT} version=${VERSION} $*"
    echo "${msg}" >> "${AUDIT_LOG}" 2>/dev/null || true
}

check_prerequisites() {
    local missing=()

    if ! command -v ansible-playbook &>/dev/null; then
        missing+=("ansible")
    fi

    if ! command -v docker &>/dev/null; then
        missing+=("docker")
    fi

    if ! command -v curl &>/dev/null; then
        missing+=("curl")
    fi

    if [[ ${#missing[@]} -gt 0 ]]; then
        log_error "Missing prerequisites: ${missing[*]}"
        exit 1
    fi
}

# Send notification to Slack
notify_slack() {
    local status="$1"
    local message="$2"

    if [[ -z "${SLACK_WEBHOOK_URL}" ]]; then
        log_warn "SLACK_WEBHOOK_URL not set; skipping Slack notification"
        return 0
    fi

    local color
    case "${status}" in
        success) color="#36a64f" ;;
        failure) color="#ff0000" ;;
        *)       color="#ffaa00" ;;
    esac

    curl -fsSL -X POST "${SLACK_WEBHOOK_URL}" \
        -H "Content-Type: application/json" \
        -d "{
            \"attachments\": [{
                \"color\": \"${color}\",
                \"title\": \"iGaming Deployment: ${status^^}\",
                \"text\": \"${message}\",
                \"fields\": [
                    {\"title\": \"Environment\", \"value\": \"${ENVIRONMENT}\", \"short\": true},
                    {\"title\": \"Version\", \"value\": \"${VERSION}\", \"short\": true}
                ],
                \"ts\": $(date +%s)
            }]
        }" || log_warn "Failed to send Slack notification"
}

# Send notification to Jira
notify_jira() {
    local status="$1"
    local message="$2"

    if [[ -z "${JIRA_URL}" || -z "${JIRA_USER}" || -z "${JIRA_TOKEN}" ]]; then
        log_warn "JIRA_URL/JIRA_USER/JIRA_TOKEN not set; skipping Jira notification"
        return 0
    fi

    curl -fsSL -X POST "${JIRA_URL}/rest/api/2/issue" \
        -H "Content-Type: application/json" \
        -u "${JIRA_USER}:${JIRA_TOKEN}" \
        -d "{
            \"fields\": {
                \"project\": {\"key\": \"DEPLOY\"},
                \"summary\": \"[${status^^}] Deploy ${VERSION} to ${ENVIRONMENT}\",
                \"description\": \"${message}\",
                \"issuetype\": {\"name\": \"Task\"}
            }
        }" || log_warn "Failed to create Jira ticket"
}

send_notification() {
    local status="$1"
    local message="$2"

    case "${NOTIFY}" in
        slack)  notify_slack "${status}" "${message}" ;;
        jira)   notify_jira "${status}" "${message}" ;;
        both)   notify_slack "${status}" "${message}"; notify_jira "${status}" "${message}" ;;
    esac
}

# Health check a service
health_check() {
    local service="$1"
    local host="$2"
    local port="$3"
    local attempt=0

    log_info "Health checking ${service} at ${host}:${port}..."

    while [[ ${attempt} -lt ${HEALTH_CHECK_RETRIES} ]]; do
        attempt=$((attempt + 1))
        if curl -fsSL --max-time 5 "http://${host}:${port}/health" > /dev/null 2>&1; then
            log_info "Health check PASSED for ${service} (attempt ${attempt})"
            return 0
        fi
        log_warn "Health check attempt ${attempt}/${HEALTH_CHECK_RETRIES} failed for ${service}"
        sleep "${HEALTH_CHECK_INTERVAL}"
    done

    log_error "Health check FAILED for ${service} after ${HEALTH_CHECK_RETRIES} attempts"
    return 1
}

# Run smoke tests for a service
smoke_test() {
    local service="$1"
    local host="$2"
    local port="$3"

    log_info "Running smoke tests for ${service}..."

    case "${service}" in
        pam)
            curl -fsSL --max-time 10 "http://${host}:${port}/api/v1/health" > /dev/null
            curl -fsSL --max-time 10 "http://${host}:${port}/api/v1/auth/status" > /dev/null
            ;;
        wallet)
            curl -fsSL --max-time 10 "http://${host}:${port}/api/v1/health" > /dev/null
            curl -fsSL --max-time 10 "http://${host}:${port}/api/v1/wallet/status" > /dev/null
            ;;
        gal)
            curl -fsSL --max-time 10 "http://${host}:${port}/api/v1/health" > /dev/null
            curl -fsSL --max-time 10 "http://${host}:${port}/api/v1/games/status" > /dev/null
            ;;
        *)
            curl -fsSL --max-time 10 "http://${host}:${port}/api/v1/health" > /dev/null
            ;;
    esac

    log_info "Smoke tests PASSED for ${service}"
}

# Get the current running version for rollback
get_current_version() {
    local service="$1"
    local inv="$2"

    ansible -i "${inv}" "${service}" -m shell \
        -a "docker inspect --format='{{index .Config.Image}}' igaming-${service} 2>/dev/null | grep -oP ':\K.*' || echo 'none'" \
        2>/dev/null | tail -1 || echo "unknown"
}

# Rollback a service to previous version
rollback_service() {
    local service="$1"
    local previous_version="$2"
    local inv="$3"

    log_warn "Rolling back ${service} to version ${previous_version}..."
    audit_log "action=rollback service=${service} from=${VERSION} to=${previous_version}"

    if [[ "${previous_version}" == "none" || "${previous_version}" == "unknown" ]]; then
        log_error "Cannot rollback ${service}: no previous version found"
        return 1
    fi

    ansible-playbook -i "${inv}" "deploy/ansible/playbooks/deploy-service.yml" \
        --extra-vars "service=${service} version=${previous_version} environment=${ENVIRONMENT}" \
        --timeout "${DEPLOY_TIMEOUT}" || {
        log_error "Rollback FAILED for ${service}"
        return 1
    }

    log_info "Rollback complete for ${service} to ${previous_version}"
}

# Run database migrations
run_migrations() {
    local inv="$1"

    log_info "Running database migrations..."
    audit_log "action=migrate version=${VERSION}"

    ansible-playbook -i "${inv}" "deploy/ansible/playbooks/migrate-database.yml" \
        --extra-vars "version=${VERSION} environment=${ENVIRONMENT}" \
        --timeout "${DEPLOY_TIMEOUT}" || {
        log_error "Database migration failed"
        return 1
    }

    log_info "Database migrations completed successfully"
}

# =============================================================================
# Parse arguments
# =============================================================================
while [[ $# -gt 0 ]]; do
    case $1 in
        --env)             ENVIRONMENT="$2";        shift 2 ;;
        --version)         VERSION="$2";            shift 2 ;;
        --inventory)       INVENTORY="$2";          shift 2 ;;
        --dry-run)         DRY_RUN=true;            shift   ;;
        --skip-migrations) SKIP_MIGRATIONS=true;    shift   ;;
        --services)        SERVICES="$2";           shift 2 ;;
        --no-rollback)     ROLLBACK_ON_FAILURE=false; shift ;;
        --notify)          NOTIFY="$2";             shift 2 ;;
        --timeout)         DEPLOY_TIMEOUT="$2";     shift 2 ;;
        --help)            usage ;;
        *)
            log_error "Unknown option: $1"
            usage
            ;;
    esac
done

if [[ -z "${ENVIRONMENT}" || -z "${VERSION}" ]]; then
    log_error "Both --env and --version are required."
    usage
fi

# Set default inventory if not provided
INVENTORY="${INVENTORY:-deploy/ansible/inventory/${ENVIRONMENT}}"

# Parse selected services
IFS=',' read -ra SELECTED_SERVICES <<< "${SERVICES}"

# Filter SERVICE_ORDER to only include selected services (preserving order)
DEPLOY_SERVICES=()
for svc in "${SERVICE_ORDER[@]}"; do
    for sel in "${SELECTED_SERVICES[@]}"; do
        if [[ "${svc}" == "${sel}" ]]; then
            DEPLOY_SERVICES+=("${svc}")
        fi
    done
done

# =============================================================================
# Main deployment
# =============================================================================

check_prerequisites

# Initialize audit log
mkdir -p "$(dirname "${AUDIT_LOG}")"
touch "${AUDIT_LOG}"

DEPLOY_START=$(date +%s)
audit_log "action=deploy-start services=${SERVICES}"

log_info "=========================================="
log_info "  iGaming Platform Deployment"
log_info "=========================================="
log_info "Environment:  ${ENVIRONMENT}"
log_info "Version:      ${VERSION}"
log_info "Inventory:    ${INVENTORY}"
log_info "Services:     ${DEPLOY_SERVICES[*]}"
log_info "Dry run:      ${DRY_RUN}"
log_info "Rollback:     ${ROLLBACK_ON_FAILURE}"
log_info "=========================================="

if [[ "${DRY_RUN}" == "true" ]]; then
    log_info "DRY RUN: No changes will be made"
fi

send_notification "info" "Deployment started: ${VERSION} to ${ENVIRONMENT}"

# -- Verify inventory and connectivity --
log_info "Verifying Ansible connectivity..."
if [[ "${DRY_RUN}" == "false" ]]; then
    ansible -i "${INVENTORY}" all -m ping --one-line || {
        log_error "Cannot reach all hosts in inventory"
        send_notification "failure" "Deployment failed: cannot reach hosts"
        exit 1
    }
fi

# -- Record current versions (for rollback) --
declare -A PREVIOUS_VERSIONS
for service in "${DEPLOY_SERVICES[@]}"; do
    if [[ "${DRY_RUN}" == "false" ]]; then
        PREVIOUS_VERSIONS["${service}"]=$(get_current_version "${service}" "${INVENTORY}")
        log_info "Current version of ${service}: ${PREVIOUS_VERSIONS[${service}]}"
    fi
done

# -- Database migrations --
if [[ "${SKIP_MIGRATIONS}" == "false" ]]; then
    if [[ "${DRY_RUN}" == "true" ]]; then
        log_info "[DRY RUN] Would run database migrations for version ${VERSION}"
    else
        run_migrations "${INVENTORY}" || {
            log_error "Migration failed. Aborting deployment."
            send_notification "failure" "Deployment failed: database migration error"
            exit 1
        }
    fi
else
    log_info "Skipping database migrations (--skip-migrations)"
fi

# -- Deploy services in order --
DEPLOYED_SERVICES=()
FAILED_SERVICE=""

for service in "${DEPLOY_SERVICES[@]}"; do
    log_info "----------------------------------------"
    log_info "Deploying ${service} (version: ${VERSION})..."
    log_info "----------------------------------------"
    audit_log "action=deploy-service service=${service} version=${VERSION}"

    if [[ "${DRY_RUN}" == "true" ]]; then
        log_info "[DRY RUN] Would deploy ${service} version ${VERSION}"
        DEPLOYED_SERVICES+=("${service}")
        continue
    fi

    # Deploy the service
    if ansible-playbook -i "${INVENTORY}" "deploy/ansible/playbooks/deploy-service.yml" \
        --extra-vars "service=${service} version=${VERSION} environment=${ENVIRONMENT}" \
        --timeout "${DEPLOY_TIMEOUT}"; then

        # Health check
        SERVICE_HOST=$(ansible -i "${INVENTORY}" "${service}" --list-hosts 2>/dev/null | tail -1 | tr -d ' ')
        SERVICE_PORT=$(ansible -i "${INVENTORY}" "${service}" -m shell -a "docker port igaming-${service} 2>/dev/null | head -1 | cut -d: -f2" 2>/dev/null | tail -1 || echo "8080")

        if health_check "${service}" "${SERVICE_HOST}" "${SERVICE_PORT}"; then
            smoke_test "${service}" "${SERVICE_HOST}" "${SERVICE_PORT}" || {
                log_warn "Smoke test failed for ${service}"
                FAILED_SERVICE="${service}"
                break
            }
            DEPLOYED_SERVICES+=("${service}")
            audit_log "action=deploy-success service=${service}"
            log_info "Successfully deployed ${service}"
        else
            FAILED_SERVICE="${service}"
            audit_log "action=deploy-failed service=${service} reason=health-check"
            break
        fi
    else
        FAILED_SERVICE="${service}"
        audit_log "action=deploy-failed service=${service} reason=ansible-error"
        break
    fi
done

# -- Handle failure and rollback --
if [[ -n "${FAILED_SERVICE}" ]]; then
    log_error "Deployment failed at service: ${FAILED_SERVICE}"

    if [[ "${ROLLBACK_ON_FAILURE}" == "true" && "${DRY_RUN}" == "false" ]]; then
        log_warn "Starting automatic rollback..."
        audit_log "action=rollback-start reason=deploy-failure"

        # Rollback in reverse order
        for ((i=${#DEPLOYED_SERVICES[@]}-1; i>=0; i--)); do
            svc="${DEPLOYED_SERVICES[${i}]}"
            prev="${PREVIOUS_VERSIONS[${svc}]:-unknown}"
            rollback_service "${svc}" "${prev}" "${INVENTORY}" || true
        done

        # Rollback the failed service if it partially deployed
        prev="${PREVIOUS_VERSIONS[${FAILED_SERVICE}]:-unknown}"
        rollback_service "${FAILED_SERVICE}" "${prev}" "${INVENTORY}" || true

        audit_log "action=rollback-complete"
        log_warn "Rollback complete"
    fi

    DEPLOY_END=$(date +%s)
    DURATION=$((DEPLOY_END - DEPLOY_START))

    send_notification "failure" "Deployment FAILED at ${FAILED_SERVICE}. Rolled back after ${DURATION}s."
    audit_log "action=deploy-end status=failed duration=${DURATION}s"

    log_error "Deployment failed. Duration: ${DURATION}s"
    exit 1
fi

# -- Success --
DEPLOY_END=$(date +%s)
DURATION=$((DEPLOY_END - DEPLOY_START))

audit_log "action=deploy-end status=success duration=${DURATION}s services=${DEPLOYED_SERVICES[*]}"

send_notification "success" "Deployed ${VERSION} to ${ENVIRONMENT}. Services: ${DEPLOYED_SERVICES[*]}. Duration: ${DURATION}s."

log_info "=========================================="
log_info "  Deployment Complete"
log_info "=========================================="
log_info "Environment:      ${ENVIRONMENT}"
log_info "Version:          ${VERSION}"
log_info "Services deployed: ${DEPLOYED_SERVICES[*]}"
log_info "Duration:         ${DURATION}s"
log_info "Audit log:        ${AUDIT_LOG}"
log_info "=========================================="
