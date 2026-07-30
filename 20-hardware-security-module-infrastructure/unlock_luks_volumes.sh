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

# unlock_luks_volumes.sh - Unlock all LUKS volumes using YubiHSM keys
# Used by systemd service during boot process

set -e

# Configuration
CONNECTOR_URL="http://localhost:12345"
AUTH_KEY_ID=2
AUTH_PASSWORD="${YUBIHSM_PASSWORD:?YUBIHSM_PASSWORD environment variable must be set}"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m' # No Color

log_info() {
    echo -e "${GREEN}[INFO]${NC} $1"
}

log_warn() {
    echo -e "${YELLOW}[WARN]${NC} $1"
}

log_error() {
    echo -e "${RED}[ERROR]${NC} $1"
}

unlock_volume() {
    local DEVICE=$1
    local NAME=$2
    local KEY_ID=$3
    
    log_info "Unlocking LUKS volume: $NAME (Key ID: $KEY_ID)"
    
    # Check if device exists
    if [ ! -b "$DEVICE" ]; then
        log_error "Device $DEVICE does not exist"
        return 1
    fi
    
    # Check if volume is already unlocked
    if [ -b "/dev/mapper/$NAME" ]; then
        log_warn "Volume $NAME is already unlocked"
        return 0
    fi
    
    # Retrieve key from YubiHSM
    KEY=$("$SCRIPT_DIR/get_luks_key.py" "$CONNECTOR_URL" "$AUTH_KEY_ID" "$AUTH_PASSWORD" "$KEY_ID")
    
    if [ -z "$KEY" ]; then
        log_error "Failed to retrieve key $KEY_ID from YubiHSM"
        return 1
    fi
    
    # Unlock LUKS volume
    echo -n "$KEY" | xxd -r -p | cryptsetup luksOpen "$DEVICE" "$NAME" --key-file=-
    
    luks_open_exit=$?
    if [ $luks_open_exit -eq 0 ]; then
        log_info "✓ Volume $NAME unlocked successfully"
        return 0
    else
        log_error "✗ Failed to unlock volume $NAME"
        return 1
    fi
}

# Main execution
main() {
    log_info "Starting LUKS volume unlock process..."
    
    # Check if YubiHSM connector is running
    if ! curl -s "$CONNECTOR_URL/connector/status" > /dev/null; then
        log_error "YubiHSM connector not running at $CONNECTOR_URL"
        exit 1
    fi
    
    # Unlock volumes (add your volumes here)
    # Format: unlock_volume <device> <name> <key_id>
    
    # Example volumes - modify as needed
    unlock_volume "/dev/nvme0n1p2" "root" "8000"
    unlock_volume "/dev/sdb1" "data" "8001"
    unlock_volume "/dev/sdc1" "swap" "8002"
    
    log_info "All LUKS volumes unlocked"
}

# Run main function
main "$@"