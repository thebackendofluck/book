#!/bin/bash
# Companion code for "The Backend of Luck" - Chapter 24, Security and Compliance.
# https://thebackendofluck.com | https://github.com/thebackendofluck/book
# SPDX-License-Identifier: Apache-2.0
#
# FOR TESTING AND EVALUATION ONLY. NOT FOR PRODUCTION USE.
# Published to demonstrate the patterns explained in the book. This code is
# not certified for real-money gaming: operating a gambling platform requires
# your own licence, independent test-lab certification (GLI, eCOGRA or
# equivalent) and regulator approval.

# Automatic renewal for Let's Encrypt IP address certificates.
#
# IP address certificates use the "shortlived" profile and are valid
# for only 6 days. Certbot will attempt renewal when the cert has
# less than 30 days remaining, but since the validity is only 6 days,
# this means renewal happens every ~5 days.
#
# This script:
#   1. Runs certbot renew for all IP certs
#   2. Reloads nginx to pick up the new certificate
#   3. Logs the renewal result
#   4. Sends an alert if renewal fails
#
# Install cron job:
#   Run:  ./auto-renew-ip-cert.sh --install-cron
#   This installs a cron job that runs every 12 hours.
#
# Usage:
#   ./auto-renew-ip-cert.sh [--install-cron] [--dry-run]

set -euo pipefail

CERTBOT_BIN="${CERTBOT_BIN:-certbot}"
LOG_FILE="/var/log/letsencrypt/ip-cert-renew.log"
ALERT_EMAIL="${ALERT_EMAIL:-ops@acmetocasino.com}"
DRY_RUN=false
INSTALL_CRON=false

parse_args() {
    for arg in "$@"; do
        case "$arg" in
            --dry-run)    DRY_RUN=true ;;
            --install-cron) INSTALL_CRON=true ;;
        esac
    done
}

install_cron() {
    local SCRIPT_PATH
    SCRIPT_PATH="$(realpath "$0")"
    local CRON_ENTRY="0 */12 * * * root ${SCRIPT_PATH} >> ${LOG_FILE} 2>&1"
    local CRON_FILE="/etc/cron.d/letsencrypt-ip-renew"

    cat > "${CRON_FILE}" << EOF
# Renew Let's Encrypt IP address certificates every 12 hours.
# IP certs (shortlived profile) are valid for 6 days.
# Certbot renews when < 30 days remain, so this fires every ~5 days effectively.
SHELL=/bin/bash
PATH=/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin
${CRON_ENTRY}
EOF
    chmod 644 "${CRON_FILE}"
    echo "Cron job installed: ${CRON_FILE}"
    echo "Renewal runs every 12 hours. Check logs at: ${LOG_FILE}"
}

renew_certs() {
    local RENEW_ARGS=(
        renew
        --quiet
        --deploy-hook "systemctl reload nginx"
    )

    if [[ "${DRY_RUN}" == "true" ]]; then
        RENEW_ARGS+=(--dry-run)
        echo "$(date -Iseconds) [DRY-RUN] Simulating IP cert renewal..."
    else
        echo "$(date -Iseconds) [INFO] Starting IP cert renewal check..."
    fi

    if "$CERTBOT_BIN" "${RENEW_ARGS[@]}"; then
        echo "$(date -Iseconds) [OK] Renewal completed (or no renewal needed)"
    else
        echo "$(date -Iseconds) [ERROR] Renewal failed — check /var/log/letsencrypt/letsencrypt.log"
        send_alert "Let's Encrypt IP cert renewal FAILED on $(hostname) at $(date)"
        exit 1
    fi
}

send_alert() {
    local message="$1"
    # Use mail if available
    if command -v mail &>/dev/null; then
        echo "${message}" | mail -s "[ALERT] IP Cert Renewal Failed" "${ALERT_EMAIL}"
    fi
    # Log to syslog
    logger -t "ip-cert-renew" "${message}"
    echo "$(date -Iseconds) [ALERT] ${message}"
}

check_cert_expiry() {
    # Find all IP-based cert directories (named like an IP address)
    local cert_found=false
    for dir in /etc/letsencrypt/live/*/; do
        local name
        name=$(basename "$dir")
        # Match IPv4 pattern
        if [[ "$name" =~ ^[0-9]+\.[0-9]+\.[0-9]+\.[0-9]+$ ]]; then
            cert_found=true
            local expiry
            expiry=$(openssl x509 -in "${dir}fullchain.pem" -noout -enddate 2>/dev/null | cut -d= -f2)
            local days_left
            days_left=$(( ( $(date -d "$expiry" +%s) - $(date +%s) ) / 86400 ))
            echo "$(date -Iseconds) [INFO] IP cert ${name}: expires ${expiry} (${days_left} days)"
            if [[ $days_left -le 1 ]]; then
                send_alert "CRITICAL: IP cert for ${name} expires in ${days_left} days!"
            fi
        fi
    done
    if [[ "$cert_found" == "false" ]]; then
        echo "$(date -Iseconds) [WARN] No IP address certificates found in /etc/letsencrypt/live/"
    fi
}

main() {
    parse_args "$@"

    if [[ "${INSTALL_CRON}" == "true" ]]; then
        install_cron
        exit 0
    fi

    check_cert_expiry
    renew_certs
}

main "$@"
