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
# postgres_key_fetch.sh - Retrieve PostgreSQL TDE master key from YubiHSM
# Used by PostgreSQL cluster_encryption_key_command for Transparent Data Encryption

set -euo pipefail

# Configuration
YUBIHSM_CONNECTOR="${YUBIHSM_CONNECTOR:-http://localhost:12345}"
AUTH_KEY_ID="${YUBIHSM_AUTH_KEY_ID:-2}"
AUTH_PASSWORD="${YUBIHSM_PASSWORD:?YUBIHSM_PASSWORD environment variable must be set}"
TDE_KEY_ID="${POSTGRES_TDE_KEY_ID:-100}"
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

# Retrieve TDE key from YubiHSM
get_tde_key() {
    log_info "Retrieving PostgreSQL TDE key (ID: $TDE_KEY_ID)"
    
    # Check if YubiHSM connector is accessible
    if ! curl -s "$YUBIHSM_CONNECTOR/connector/status" > /dev/null 2>&1; then
        log_error "YubiHSM connector not accessible at $YUBIHSM_CONNECTOR"
        exit 1
    fi
    
    # Use Python script to retrieve key
    if [ -f "$SCRIPT_DIR/get_luks_key.py" ]; then
        "$SCRIPT_DIR/get_luks_key.py" "$YUBIHSM_CONNECTOR" "$AUTH_KEY_ID" "$AUTH_PASSWORD" "$TDE_KEY_ID"
    else
        # Fallback: try direct yubihsm-shell command
        log_info "Using yubihsm-shell fallback"
        yubihsm-shell << EOF 2>/dev/null | grep -A 1 "Opaque object" | tail -1 | cut -d' ' -f3-
connect
session open 1 $AUTH_PASSWORD
get opaque $TDE_KEY_ID
session close 0
disconnect
EOF
    fi
}

# Main execution - PostgreSQL expects key output on stdout
main() {
    key=$(get_tde_key)
    
    if [ -z "$key" ]; then
        log_error "Failed to retrieve PostgreSQL TDE key"
        exit 1
    fi
    
    # PostgreSQL expects the key as hex string
    echo "$key"
}

# Run main function
main "$@"