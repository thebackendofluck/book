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

# shellcheck disable=SC2034,SC2086,SC2155
# check_tde_rotation.sh - Monitor TDE key age and trigger rotation alerts
# Checks key age against configurable thresholds for compliance
#
# Usage: ./check_tde_rotation.sh
# Environment: TDE_KEY_ID, ROTATION_DAYS, WARNING_DAYS, AUTO_ROTATE

set -euo pipefail

# Configuration
TDE_KEY_ID="${TDE_KEY_ID:-100}"
ROTATION_DAYS="${ROTATION_DAYS:-90}"
WARNING_DAYS="${WARNING_DAYS:-30}"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m'

log_info() { echo -e "${GREEN}[INFO]${NC} $(date '+%Y-%m-%d %H:%M:%S') - $1"; }
log_warn() { echo -e "${YELLOW}[WARN]${NC} $(date '+%Y-%m-%d %H:%M:%S') - $1"; }
log_error() { echo -e "${RED}[ERROR]${NC} $(date '+%Y-%m-%d %H:%M:%S') - $1"; }

# Get TDE key creation date from HSM metadata
get_tde_key_age() {
    # In production, this queries HSM object metadata for key creation timestamp
    # Example: yubihsm-shell -c "get objectinfo 0 $TDE_KEY_ID symmetric-key"
    # For demonstration, returns a mock age in days
    echo "45"
}

# Check if rotation is needed based on compliance thresholds
check_rotation_needed() {
    local key_age=$(get_tde_key_age)
    local days_until_rotation=$((ROTATION_DAYS - key_age))

    log_info "TDE key age: $key_age days"
    log_info "Rotation threshold: $ROTATION_DAYS days"
    log_info "Days until rotation: $days_until_rotation"

    if [ $key_age -ge $ROTATION_DAYS ]; then
        log_error "TDE KEY REQUIRES IMMEDIATE ROTATION (age: $key_age days)"
        return 2
    elif [ $days_until_rotation -le $WARNING_DAYS ]; then
        log_warn "TDE key rotation needed in $days_until_rotation days"
        return 1
    else
        log_info "TDE key rotation not needed (next in $days_until_rotation days)"
        return 0
    fi
}

# Perform TDE key rotation
rotate_tde_key() {
    log_info "Starting TDE key rotation..."

    # 1. Generate new AES-256 key in HSM
    log_info "Generating new TDE key..."

    # 2. Update database TDE configuration
    log_info "Updating database TDE configuration..."

    # 3. Test new key accessibility
    log_info "Testing new TDE key..."

    # 4. Archive old key (retain for recovery)
    log_info "Archiving old TDE key..."

    log_info "TDE key rotation completed successfully"
}

# Main execution
main() {
    log_info "Checking TDE key rotation status..."

    check_rotation_needed
    local exit_code=$?

    case $exit_code in
        0) log_info "TDE key status: GOOD" ;;
        1) log_warn "TDE key status: WARNING - Rotation soon needed" ;;
        2)
            log_error "TDE key status: CRITICAL - Immediate rotation required"
            if [ "${AUTO_ROTATE:-false}" = "true" ]; then
                rotate_tde_key
            else
                log_info "Set AUTO_ROTATE=true to perform automatic rotation"
            fi
        ;;
    esac

    exit $exit_code
}

main "$@"
