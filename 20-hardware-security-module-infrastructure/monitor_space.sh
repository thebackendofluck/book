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

# monitor_space.sh - Continuous monitoring of YubiHSM space usage
# Alerts when usage exceeds thresholds

set -euo pipefail

# Configuration
YUBIHSM_CONNECTOR="${YUBIHSM_CONNECTOR:-http://localhost:12345}"
WARNING_THRESHOLD="${WARNING_THRESHOLD:-75}"
CRITICAL_THRESHOLD="${CRITICAL_THRESHOLD:-90}"
CHECK_INTERVAL="${CHECK_INTERVAL:-300}"  # 5 minutes
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

# Get current space usage
get_space_usage() {
    if [ -f "$SCRIPT_DIR/yubihsm_lifecycle_management.sh" ]; then
        "$SCRIPT_DIR/yubihsm_lifecycle_management.sh" space 2>/dev/null | grep "Used:" | awk '{print $2}' | cut -d'/' -f1
    else
        # Fallback: try direct query
        yubihsm-shell << 'EOF' 2>/dev/null | grep "objects in use" | awk '{print $1}'
connect
session open 1 password
list objects
session close 0
disconnect
EOF
    fi
}

# Send alert (placeholder - integrate with your alerting system)
send_alert() {
    local level="$1"
    local message="$2"
    
    case "$level" in
        "WARNING")
            log_warn "$message"
            # Add your alerting logic here (email, Slack, etc.)
        ;;
        "CRITICAL")
            log_error "$message"
            # Add your alerting logic here (email, Slack, PagerDuty, etc.)
        ;;
        *)
            log_info "$message"
        ;;
    esac
}

# Main monitoring loop
main() {
    log_info "Starting YubiHSM space monitoring..."
    log_info "Warning threshold: ${WARNING_THRESHOLD}%"
    log_info "Critical threshold: ${CRITICAL_THRESHOLD}%"
    log_info "Check interval: ${CHECK_INTERVAL} seconds"
    echo ""
    
    local previous_usage=-1
    
    while true; do
        # Check if YubiHSM is accessible
        if ! curl -s "$YUBIHSM_CONNECTOR/connector/status" > /dev/null 2>&1; then
            log_error "YubiHSM connector not accessible at $YUBIHSM_CONNECTOR"
            sleep $CHECK_INTERVAL
            continue
        fi
        
        # Get current usage
        local usage
        usage=$(get_space_usage)
        local percentage=0
        
        if [ "$usage" = "Unknown" ] || [ -z "$usage" ]; then
            log_warn "Could not determine space usage"
            sleep $CHECK_INTERVAL
            continue
        fi
        
        percentage=$((usage * 100 / 256))
        
        # Check thresholds
        if [ $percentage -ge $CRITICAL_THRESHOLD ]; then
            send_alert "CRITICAL" "HSM usage at ${percentage}% (${usage}/256 objects) - IMMEDIATE ACTION REQUIRED"
            elif [ $percentage -ge $WARNING_THRESHOLD ]; then
            send_alert "WARNING" "HSM usage at ${percentage}% (${usage}/256 objects) - Consider cleanup"
        else
            if [ $previous_usage -ne $usage ]; then
                log_info "HSM usage at ${percentage}% (${usage}/256 objects)"
            fi
        fi
        
        previous_usage=$usage
        sleep $CHECK_INTERVAL
    done
}

# Handle Ctrl+C gracefully
trap 'echo ""; log_info "Space monitoring stopped."; exit 0' INT

# Run main function
main "$@"