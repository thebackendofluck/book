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
# check_tde_rotation.sh - Check if PostgreSQL TDE key rotation is needed
# Monitors key age and triggers rotation alerts

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

# Get TDE key creation date (mock implementation)
get_tde_key_age() {
    # In real implementation, this would check HSM object metadata
    # For demo, return mock age in days
    echo "45"  # 45 days old
}

# Check if rotation is needed
check_rotation_needed() {
    local key_age
    key_age=$(get_tde_key_age)
    local days_until_rotation
    days_until_rotation=$((ROTATION_DAYS - key_age))
    local days_until_warning
    days_until_warning=$((WARNING_DAYS - days_until_rotation))
    
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
    
    # 1. Generate new TDE key
    log_info "Generating new TDE key..."
    # In real implementation: generate new AES-256 key in HSM
    
    # 2. Update PostgreSQL configuration
    log_info "Updating PostgreSQL TDE configuration..."
    # In real implementation: update postgresql.conf and reload
    
    # 3. Test new key
    log_info "Testing new TDE key..."
    # In real implementation: verify PostgreSQL can access new key
    
    # 4. Archive old key
    log_info "Archiving old TDE key..."
    # In real implementation: mark old key as archived
    
    log_info "TDE key rotation completed successfully"
}

# Main execution
main() {
    log_info "Checking PostgreSQL TDE key rotation status..."
    
    local status
    
    status=$(check_rotation_needed)
    local exit_code=$?
    
    case $exit_code in
        0)
            log_info "TDE key status: GOOD"
        ;;
        1)
            log_warn "TDE key status: WARNING - Rotation soon needed"
        ;;
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

# Run main function
main "$@"