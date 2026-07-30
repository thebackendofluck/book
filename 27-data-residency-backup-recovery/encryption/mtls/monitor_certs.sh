#!/bin/bash
# Companion code for "The Backend of Luck" - Chapter 27, Data Residency and Backup/Recovery.
# https://thebackendofluck.com | https://github.com/thebackendofluck/book
# SPDX-License-Identifier: Apache-2.0
#
# FOR TESTING AND EVALUATION ONLY. NOT FOR PRODUCTION USE.
# Published to demonstrate the patterns explained in the book. This code is
# not certified for real-money gaming: operating a gambling platform requires
# your own licence, independent test-lab certification (GLI, eCOGRA or
# equivalent) and regulator approval.

# shellcheck disable=SC2086,SC2155
# monitor_certs.sh - Certificate Expiration Monitoring
# Continuously monitors certificate lifecycle and alerts on upcoming expirations
# Critical for compliance in regulated gambling platforms
#
# Usage: ./monitor_certs.sh
# Environment: HSM_CONNECTOR, WARNING_DAYS, CRITICAL_DAYS, CHECK_INTERVAL

set -euo pipefail

# Configuration
HSM_CONNECTOR="${HSM_CONNECTOR:-http://localhost:12345}"
WARNING_DAYS="${WARNING_DAYS:-30}"
CRITICAL_DAYS="${CRITICAL_DAYS:-7}"
CHECK_INTERVAL="${CHECK_INTERVAL:-3600}"  # 1 hour
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m'

log_info() { echo -e "${GREEN}[INFO]${NC} $(date '+%Y-%m-%d %H:%M:%S') - $1"; }
log_warn() { echo -e "${YELLOW}[WARN]${NC} $(date '+%Y-%m-%d %H:%M:%S') - $1"; }
log_error() { echo -e "${RED}[ERROR]${NC} $(date '+%Y-%m-%d %H:%M:%S') - $1"; }

# Get certificate list from HSM or local store
get_certificates() {
    # In production, query HSM for stored certificate objects
    # Fallback: scan local certificate directories
    if [ -d "$SCRIPT_DIR/certs" ]; then
        find "$SCRIPT_DIR/certs" -name "*.crt" -exec basename {} .crt \; 2>/dev/null
    else
        echo "api-gateway-ssl"
        echo "internal-api"
        echo "payment-service"
    fi
}

# Check certificate expiration date
check_cert_expiry() {
    local cert_name="$1"
    local cert_file="$SCRIPT_DIR/certs/${cert_name}.crt"

    if [ -f "$cert_file" ]; then
        # Extract expiry date from actual certificate
        openssl x509 -enddate -noout -in "$cert_file" | cut -d= -f2
    else
        # In production, extract from HSM object metadata
        echo "2025-12-31"
    fi
}

# Calculate days until expiry
days_until_expiry() {
    local expiry_date="$1"
    local current_date=$(date +%s)
    local expiry_timestamp=$(date -d "$expiry_date" +%s 2>/dev/null || echo "$current_date")
    local days=$(( (expiry_timestamp - current_date) / 86400 ))
    echo $days
}

# Send alert for expiring certificate
send_cert_alert() {
    local level="$1"
    local cert_name="$2"
    local days="$3"
    local expiry_date="$4"

    case "$level" in
        "CRITICAL")
            log_error "CERTIFICATE EXPIRING SOON: $cert_name expires in $days days ($expiry_date)"
            # Integration point: PagerDuty, OpsGenie, etc.
            ;;
        "WARNING")
            log_warn "Certificate expiring soon: $cert_name expires in $days days ($expiry_date)"
            # Integration point: Slack, email, etc.
            ;;
        "INFO")
            log_info "Certificate status: $cert_name expires in $days days ($expiry_date)"
            ;;
    esac
}

# Main monitoring function
check_certificates() {
    log_info "Checking certificate expirations..."

    local certs=$(get_certificates)
    local critical_count=0
    local warning_count=0

    while read -r cert_name; do
        if [ -z "$cert_name" ]; then continue; fi

        local expiry_date=$(check_cert_expiry "$cert_name")
        local days=$(days_until_expiry "$expiry_date")

        if [ $days -le $CRITICAL_DAYS ]; then
            send_cert_alert "CRITICAL" "$cert_name" "$days" "$expiry_date"
            ((critical_count++))
        elif [ $days -le $WARNING_DAYS ]; then
            send_cert_alert "WARNING" "$cert_name" "$days" "$expiry_date"
            ((warning_count++))
        else
            send_cert_alert "INFO" "$cert_name" "$days" "$expiry_date"
        fi
    done <<< "$certs"

    if [ $critical_count -gt 0 ]; then
        log_error "SUMMARY: $critical_count certificates expiring within $CRITICAL_DAYS days"
    fi
    if [ $warning_count -gt 0 ]; then
        log_warn "SUMMARY: $warning_count certificates expiring within $WARNING_DAYS days"
    fi
    if [ $critical_count -eq 0 ] && [ $warning_count -eq 0 ]; then
        log_info "SUMMARY: All certificates valid (no expirations within $WARNING_DAYS days)"
    fi
}

# Main loop
main() {
    log_info "Starting certificate monitoring..."
    log_info "Warning threshold: ${WARNING_DAYS} days"
    log_info "Critical threshold: ${CRITICAL_DAYS} days"
    log_info "Check interval: ${CHECK_INTERVAL} seconds"

    while true; do
        if ! curl -s "$HSM_CONNECTOR/connector/status" > /dev/null 2>&1; then
            log_error "HSM connector not accessible at $HSM_CONNECTOR"
            sleep $CHECK_INTERVAL
            continue
        fi

        check_certificates
        log_info "Next check in $CHECK_INTERVAL seconds..."
        sleep $CHECK_INTERVAL
    done
}

trap 'echo ""; log_info "Certificate monitoring stopped."; exit 0' INT
main "$@"
