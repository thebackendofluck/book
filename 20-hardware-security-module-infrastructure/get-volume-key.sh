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
# get-volume-key.sh - Retrieve Docker volume encryption key from YubiHSM
# Used by Docker volume plugin for encrypted container storage

set -euo pipefail

# Configuration
YUBIHSM_CONNECTOR="${YUBIHSM_CONNECTOR:-http://localhost:12345}"
AUTH_KEY_ID="${YUBIHSM_AUTH_KEY_ID:-2}"
AUTH_PASSWORD="${YUBIHSM_PASSWORD:?YUBIHSM_PASSWORD environment variable must be set}"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m' # No Color

log_info() {
    echo -e "${GREEN}[INFO]${NC} $1" >&2
}

log_error() {
    echo -e "${RED}[ERROR]${NC} $1" >&2
}

# Get volume key ID from volume name (deterministic)
get_volume_key_id() {
    local volume_name="$1"
    # Create deterministic key ID from volume name hash
    echo "$((2000 + $(echo "$volume_name" | cksum | cut -d' ' -f1) % 1000))"
}

# Retrieve key from YubiHSM
get_volume_key() {
    local volume_name="$1"
    local key_id
    key_id=$(get_volume_key_id "$volume_name")
    
    log_info "Retrieving key for volume: $volume_name (Key ID: $key_id)"
    
    # Use Python script to get key from YubiHSM
    if [ -f "$SCRIPT_DIR/get_luks_key.py" ]; then
        "$SCRIPT_DIR/get_luks_key.py" "$YUBIHSM_CONNECTOR" "$AUTH_KEY_ID" "$AUTH_PASSWORD" "$key_id"
    else
        # Fallback: try direct yubihsm-shell command
        log_info "Using yubihsm-shell fallback"
        yubihsm-shell << EOF | grep -A 1 "Opaque object" | tail -1 | cut -d' ' -f3-
connect
session open 1 $AUTH_PASSWORD
get opaque $key_id
session close 0
disconnect
EOF
    fi
}

# Main execution
main() {
    local volume_name="$1"
    
    if [ -z "$volume_name" ]; then
        log_error "Usage: $0 <volume_name>"
        exit 1
    fi
    
    # Check if YubiHSM connector is accessible
    if ! curl -s "$YUBIHSM_CONNECTOR/connector/status" > /dev/null 2>&1; then
        log_error "YubiHSM connector not accessible at $YUBIHSM_CONNECTOR"
        exit 1
    fi
    
    # Get and output the key
    key=$(get_volume_key "$volume_name")
    
    if [ -z "$key" ]; then
        log_error "Failed to retrieve key for volume: $volume_name"
        exit 1
    fi
    
    # Output key in hex format for cryptsetup
    echo "$key"
}

# Run main function
main "$@"