#!/usr/bin/env bash
# Companion code for "The Backend of Luck" - Chapter 24, Security and Compliance.
# https://thebackendofluck.com | https://github.com/thebackendofluck/book
# SPDX-License-Identifier: Apache-2.0
#
# FOR TESTING AND EVALUATION ONLY. NOT FOR PRODUCTION USE.
# Published to demonstrate the patterns explained in the book. This code is
# not certified for real-money gaming: operating a gambling platform requires
# your own licence, independent test-lab certification (GLI, eCOGRA or
# equivalent) and regulator approval.

# =============================================================================
# deploy-coraza.sh — Deploy Coraza WAF as a native nginx module
# =============================================================================
# Supports two deployment modes:
#
#   1. Docker mode (default for new deployments):
#      Runs nginx-coraza Docker image — WAF baked into the container.
#      Use this for any Docker Compose, K3s, or containerised deployment.
#
#   2. Bare-metal mode (--bare-metal):
#      Compiles and installs the ModSecurity v3 dynamic module directly into
#      the existing nginx on the host. No Docker required.
#      Use this for production (203.0.113.1) and ops-host bare-metal nginx.
#
# Architecture (both modes):
#   Internet → nginx + ModSecurity v3 WAF module → upstream
#   (No sidecar. No Caddy. No iptables redirects.)
#
# Usage:
#   ./deploy-coraza.sh --env staging
#   ./deploy-coraza.sh --env production    # targets 203.0.113.1
#   ./deploy-coraza.sh --env ops-host       # targets ops-host
#   ./deploy-coraza.sh --env production --bare-metal
#   ./deploy-coraza.sh --rollback          # undo bare-metal install
#
# =============================================================================
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
LOG_FILE="/tmp/coraza-deploy-$(date '+%Y%m%d-%H%M%S').log"

# Server targets
PROD_HOST="root@203.0.113.1"
OPS_HOST="admin@ops-server"
REMOTE_DEPLOY_PATH="/opt/coraza-waf"

# Module install path (Ubuntu nginx from apt) — used by build-coraza-nginx-module.sh
# These are printed in the deployment summary and passed to the build script.
# shellcheck disable=SC2034
NGINX_MODULES_DIR="/usr/lib/nginx/modules"
# shellcheck disable=SC2034
CORAZA_CONF_DIR="/etc/nginx/coraza"

# ---------------------------------------------------------------------------
# CLI argument parsing
# ---------------------------------------------------------------------------
ENV=""
ROLLBACK=false
BARE_METAL=false

while [[ $# -gt 0 ]]; do
    case "$1" in
        --env)         ENV="$2"; shift 2 ;;
        --rollback)    ROLLBACK=true; shift ;;
        --bare-metal)  BARE_METAL=true; shift ;;
        --help|-h)
            echo "Usage: $0 --env [staging|production|ops-host] [--bare-metal]"
            echo "       $0 --rollback"
            exit 0
            ;;
        *)
            echo "Unknown argument: $1" >&2
            exit 1
            ;;
    esac
done

# ---------------------------------------------------------------------------
# Utility functions
# ---------------------------------------------------------------------------
log()     { echo "[$(date '+%H:%M:%S')] $*" | tee -a "${LOG_FILE}"; }
log_err() { echo "[$(date '+%H:%M:%S')] ERROR: $*" | tee -a "${LOG_FILE}" >&2; }
confirm() {
    local prompt="$1"
    read -r -p "${prompt} [y/N] " response
    [[ "${response}" =~ ^[Yy]$ ]]
}

# ---------------------------------------------------------------------------
# Determine if we're running locally and need to deploy remotely
# ---------------------------------------------------------------------------
is_remote_host() {
    case "${ENV}" in
        production) [[ "$(hostname -I 2>/dev/null)" != *"203.0.113.1"* ]] ;;
        ops-host)    [[ "$(hostname)" != "ops-host"* ]] ;;
        *)          return 1 ;;
    esac
}

remote_deploy() {
    local ssh_target="$1"
    log "Deploying to ${ssh_target}..."

    log "Syncing files to ${ssh_target}:${REMOTE_DEPLOY_PATH}..."
    # shellcheck disable=SC2029
    ssh "${ssh_target}" "mkdir -p ${REMOTE_DEPLOY_PATH}"
    rsync -avz --delete \
        --exclude='*.log' \
        --exclude='__pycache__' \
        "${SCRIPT_DIR}/" \
        "${ssh_target}:${REMOTE_DEPLOY_PATH}/"

    local bare_metal_flag=""
    if [[ "${BARE_METAL}" == true ]]; then
        bare_metal_flag="--bare-metal"
    fi

    log "Executing deploy script on ${ssh_target}..."
    # shellcheck disable=SC2029
    ssh "${ssh_target}" "bash ${REMOTE_DEPLOY_PATH}/deploy-coraza.sh --env local ${bare_metal_flag}"
}

# ---------------------------------------------------------------------------
# ROLLBACK: remove the bare-metal module (Docker mode: docker compose down)
# ---------------------------------------------------------------------------
rollback() {
    log "=== ROLLBACK: Removing Coraza WAF ==="

    if [[ "${BARE_METAL}" == false ]]; then
        log "Stopping nginx-coraza container..."
        docker compose -f "${SCRIPT_DIR}/docker-compose.yml" down 2>/dev/null || true
        log "Container stopped. nginx-coraza image still available for re-deploy."
    else
        # Remove load_module line from nginx.conf
        log "Removing load_module from nginx.conf..."
        sed -i '/load_module.*modsecurity_module\|load_module.*coraza_module/d' \
            /etc/nginx/nginx.conf 2>/dev/null || true

        # Remove the conf.d snippet
        rm -f /etc/nginx/conf.d/coraza-waf.conf

        # Test and reload
        log "Testing nginx config..."
        nginx -t
        log "Reloading nginx..."
        systemctl reload nginx
        log "nginx is running without WAF"
    fi

    log "=== ROLLBACK COMPLETE ==="
}

# ---------------------------------------------------------------------------
# Pre-deployment validation
# ---------------------------------------------------------------------------
preflight_checks() {
    log "Running pre-flight checks..."
    local errors=0

    # CRS rules
    local rule_count
    rule_count="$(find "${SCRIPT_DIR}/crs-rules" -name "*.conf" 2>/dev/null | wc -l | tr -d ' ')"
    if [[ "${rule_count}" -lt 10 ]]; then
        log_err "CRS rules not found in ${SCRIPT_DIR}/crs-rules/ (found ${rule_count} files)"
        log_err "Run: ./download-crs-rules.sh"
        ((errors++))
    else
        log "CRS rules: ${rule_count} files found"
    fi

    # Required config files
    for f in coraza.conf crs-setup.conf; do
        if [[ ! -f "${SCRIPT_DIR}/${f}" ]]; then
            log_err "Missing required file: ${SCRIPT_DIR}/${f}"
            ((errors++))
        fi
    done

    if [[ "${BARE_METAL}" == false ]]; then
        # Docker mode
        if ! command -v docker &>/dev/null; then
            log_err "Docker not installed"
            ((errors++))
        fi
        if ! docker compose version &>/dev/null; then
            log_err "Docker Compose not available"
            ((errors++))
        fi
        if [[ ! -f "${SCRIPT_DIR}/Dockerfile.nginx-coraza" ]]; then
            log_err "Missing Dockerfile.nginx-coraza"
            ((errors++))
        fi
    else
        # Bare-metal mode
        if ! command -v nginx &>/dev/null; then
            log_err "nginx not found — is this the correct host?"
            ((errors++))
        fi
        if [[ ! -f "${SCRIPT_DIR}/build-coraza-nginx-module.sh" ]]; then
            log_err "Missing build-coraza-nginx-module.sh"
            ((errors++))
        fi
    fi

    if [[ ${errors} -gt 0 ]]; then
        log_err "${errors} pre-flight check(s) failed. Aborting."
        exit 1
    fi

    log "Pre-flight checks passed."
}

# ---------------------------------------------------------------------------
# Deploy: Docker mode
# ---------------------------------------------------------------------------
deploy_docker() {
    log "Building nginx-coraza Docker image..."
    docker build \
        -f "${SCRIPT_DIR}/Dockerfile.nginx-coraza" \
        -t nginx-coraza:latest \
        "${SCRIPT_DIR}"

    log "Starting nginx-coraza container..."
    docker compose -f "${SCRIPT_DIR}/docker-compose.yml" up -d

    # Wait for healthy
    log "Waiting for nginx-coraza to become healthy..."
    local retries=0
    while [[ ${retries} -lt 30 ]]; do
        local health
        health="$(docker inspect --format='{{.State.Health.Status}}' nginx-coraza 2>/dev/null || echo 'starting')"
        if [[ "${health}" == "healthy" ]]; then
            log "Container is healthy."
            return 0
        fi
        log "  Health: ${health} (${retries}/30)..."
        sleep 3
        ((retries++))
    done

    log_err "Container did not become healthy within 90 seconds"
    docker logs nginx-coraza | tail -30
    return 1
}

# ---------------------------------------------------------------------------
# Deploy: Bare-metal mode
# ---------------------------------------------------------------------------
deploy_bare_metal() {
    log "Running bare-metal module build and install..."
    bash "${SCRIPT_DIR}/build-coraza-nginx-module.sh"
}

# ---------------------------------------------------------------------------
# Run test suite
# ---------------------------------------------------------------------------
run_tests() {
    local host="${1:-localhost}"
    local port="${2:-80}"
    log "Running WAF test suite against ${host}:${port}..."
    if bash "${SCRIPT_DIR}/test-coraza.sh" "${host}" "${port}"; then
        log "All tests passed."
    else
        log_err "Test suite failed — initiating rollback"
        rollback
        exit 1
    fi
}

# ---------------------------------------------------------------------------
# Post-deployment verification
# ---------------------------------------------------------------------------
post_deploy_verify() {
    log "Post-deployment verification..."

    if [[ "${BARE_METAL}" == false ]]; then
        if docker ps | grep nginx-coraza | grep -qv "Exited"; then
            log "nginx-coraza container: running"
        else
            log_err "nginx-coraza container not running"
            return 1
        fi
    else
        if systemctl is-active nginx &>/dev/null; then
            log "nginx: running"
        else
            log_err "nginx is not running"
            return 1
        fi

        # Verify module is loaded
        if nginx -V 2>&1 | grep -q 'modsecurity\|coraza'; then
            log "WAF module: loaded"
        else
            # Also check the running process modules
            if grep -q 'modsecurity\|coraza' /etc/nginx/nginx.conf 2>/dev/null; then
                log "WAF module: configured"
            else
                log_err "WAF module does not appear to be loaded"
                return 1
            fi
        fi
    fi

    # Verify traffic is flowing
    local http_status
    http_status="$(curl -s -o /dev/null -w '%{http_code}' --max-time 5 http://localhost/ || echo 000)"
    log "HTTP status via nginx: ${http_status}"

    log "Post-deployment verification complete."
}

# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------
main() {
    log "=== Coraza WAF Deploy (native nginx module) ==="
    log "Environment  : ${ENV:-local}"
    log "Bare-metal   : ${BARE_METAL}"
    log "Log file     : ${LOG_FILE}"
    echo

    # Handle rollback
    if [[ "${ROLLBACK}" == true ]]; then
        if ! confirm "This will remove the Coraza WAF from nginx. Continue?"; then
            log "Rollback cancelled."
            exit 0
        fi
        rollback
        exit 0
    fi

    # Validate environment
    if [[ -z "${ENV}" ]]; then
        echo "ERROR: --env is required. Use: --env staging | --env production | --env ops-host" >&2
        exit 1
    fi

    # Remote deployment
    if is_remote_host 2>/dev/null; then
        case "${ENV}" in
            production) remote_deploy "${PROD_HOST}" ;;
            ops-host)    remote_deploy "${OPS_HOST}" ;;
        esac
        exit 0
    fi

    # Production confirmation
    if [[ "${ENV}" == "production" ]]; then
        echo
        echo "================================================================"
        echo "  WARNING: You are about to deploy to PRODUCTION (203.0.113.1)"
        echo "================================================================"
        echo "  - Ensure ./test-coraza.sh passed on staging"
        echo "  - Ensure on-call engineer has been notified"
        echo "  - Rollback: ./deploy-coraza.sh --rollback"
        echo
        if ! confirm "Proceed with production deployment?"; then
            log "Deployment cancelled."
            exit 0
        fi
    fi

    preflight_checks

    if [[ "${BARE_METAL}" == false ]]; then
        deploy_docker
        run_tests localhost 80
    else
        deploy_bare_metal
        run_tests localhost 80
    fi

    post_deploy_verify

    # Summary
    local mode_desc
    if [[ "${BARE_METAL}" == false ]]; then
        mode_desc="Docker (nginx-coraza image)"
    else
        mode_desc="Bare-metal (dynamic .so module)"
    fi

    cat <<EOF

================================================================
  Coraza WAF deployment complete
================================================================
  Environment  : ${ENV}
  Mode         : ${mode_desc}
  Log file     : ${LOG_FILE}

  Useful commands:
EOF

    if [[ "${BARE_METAL}" == false ]]; then
        cat <<EOF
    docker logs -f nginx-coraza                   # live nginx+WAF logs
    docker exec nginx-coraza tail -f /var/log/coraza/audit.log
    docker compose -f docker-compose.yml down     # stop
    ./deploy-coraza.sh --rollback                 # remove container
EOF
    else
        cat <<EOF
    tail -f /var/log/coraza/audit.log             # WAF audit log
    tail -f /var/log/nginx/error.log              # nginx error log
    nginx -t && systemctl reload nginx            # reload after config changes
    ./deploy-coraza.sh --rollback --bare-metal    # remove module
EOF
    fi

    cat <<EOF

  Run tests:
    ./test-coraza.sh localhost 80

  Monitor for false positives for 72 hours before raising PL to 2.
================================================================
EOF
}

main "$@"
