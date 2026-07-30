#!/bin/bash
# Companion code for "The Backend of Luck" - Chapter 20, Hardware Security Module Infrastructure.
# https://thebackendofluck.com | https://github.com/thebackendofluck/book
# SPDX-License-Identifier: Apache-2.0
#
# FOR TESTING AND EVALUATION ONLY. NOT FOR PRODUCTION USE.
# Published to demonstrate the patterns explained in the book. This code is
# not certified for real-money gaming: operating a gambling platform requires
# your own licence, independent test-lab certification (GLI, eCOGRA or
# equivalent) and regulator approval.

# shellcheck disable=SC2034  # Config and color constants
# monitor_certs.sh - Monitor certificate expiration and rotation needs
# Checks all certificates stored in YubiHSM and alerts on upcoming expirations

set -euo pipefail

# Configuration
YUBIHSM_CONNECTOR="${YUBIHSM_CONNECTOR:-http://localhost:12345}"
WARNING_DAYS="${WARNING_DAYS:-30}"
CRITICAL_DAYS="${CRITICAL_DAYS:-7}"
CHECK_INTERVAL="${CHECK_INTERVAL:-3600}"  # 1 hour
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m' # No Color

log_info() {
    echo -e "${GREEN}[INFO]${NC} $(date '+%Y-%m-%d %H:%M:%S') - $1"
}

log_warn() {
    echo -e "${YELLOW}[WARN]${NC} $(date '+%Y-%m-%d %H:%M:%S') - $1"
}

log_error() {
    echo -e "${RED}[ERROR]${NC} $(date '+%Y-%m-%d %H:%M:%S') - $1"
}

# Get certificate list from YubiHSM
get_certificates() {
    if [ -f "$SCRIPT_DIR/yubihsm_complete_storage.sh" ]; then
        "$SCRIPT_DIR/yubihsm_complete_storage.sh" list 2>/dev/null | grep "Certificate" | awk '{print $2}'
    else
        # Fallback: mock certificate list
        echo "production-ssl"
        echo "internal-api"
        echo "vaultwarden-ca"
    fi
}

# Check certificate expiration (mock implementation)
check_cert_expiry() {
    local cert_name="$1"
    local cert_file="/tmp/cert_${cert_name}.pem"

    # In real implementation, this would extract cert from HSM and check expiry
    # For demo, we'll use mock expiry dates
    case "$cert_name" in
        "production-ssl")
            echo "2024-12-15"  # Expires in 30 days
            ;;
        "internal-api")
            echo "2024-11-20"  # Expires in 5 days - CRITICAL
            ;;
        "vaultwarden-ca")
            echo "2025-10-23"  # Expires in 11 months
            ;;
        *)
            echo "2025-01-01"  # Default future date
            ;;
    esac
}

# Calculate days until expiry
days_until_expiry() {
    local expiry_date="$1"
    local current_date
    current_date=$(date +%s)
    local expiry_timestamp
    expiry_timestamp=$(date -d "$expiry_date" +%s 2>/dev/null || echo "$current_date")

    local days

    days=$(( (expiry_timestamp - current_date) / 86400 ))
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
            # Add critical alerting logic here (PagerDuty, etc.)
            ;;
        "WARNING")
            log_warn "Certificate expiring soon: $cert_name expires in $days days ($expiry_date)"
            # Add warning alerting logic here (email, Slack, etc.)
            ;;
        "INFO")
            log_info "Certificate status: $cert_name expires in $days days ($expiry_date)"
            ;;
    esac
}

# Main monitoring function
check_certificates() {
    log_info "Checking certificate expirations..."

    local certs

    certs=$(get_certificates)
    local critical_count=0
    local warning_count=0

    while read -r cert_name; do
        if [ -z "$cert_name" ]; then
            continue
        fi

        local expiry_date

        expiry_date=$(check_cert_expiry "$cert_name")
        local days
        days=$(days_until_expiry "$expiry_date")

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

    # Summary
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
    echo ""

    while true; do
        # Check if YubiHSM is accessible
        if ! curl -s "$YUBIHSM_CONNECTOR/connector/status" > /dev/null 2>&1; then
            log_error "YubiHSM connector not accessible at $YUBIHSM_CONNECTOR"
            sleep $CHECK_INTERVAL
            continue
        fi

        check_certificates
        echo ""
        log_info "Next check in $CHECK_INTERVAL seconds..."
        sleep $CHECK_INTERVAL
    done
}

# Handle Ctrl+C gracefully
trap 'echo ""; log_info "Certificate monitoring stopped."; exit 0' INT

# Run main function
main "$@"