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


# SED AES-256 Setup Script for Samsung MZQL27T6HBLA-00A07 on Ubuntu Linux
# This script configures TCG Opal SED encryption on the specified NVMe drive

set -e

# Configuration - MODIFY THESE VALUES
DEVICE="/dev/nvme0n1"                    # NVMe device path
PASSWORD="${SED_PASSWORD:?SED_PASSWORD environment variable must be set}"  # SED authentication password
LOG_FILE="/var/log/sed_setup.log"        # Log file for operations

# Logging function
log() {
    echo "$(date '+%Y-%m-%d %H:%M:%S') - $*" | tee -a "$LOG_FILE"
}

# Error handling
error_exit() {
    log "ERROR: $1"
    exit 1
}

# Check root privileges
if [[ $EUID -ne 0 ]]; then
    error_exit "This script must be run as root (sudo)"
fi

log "Starting SED AES-256 setup for Samsung MZQL27T6HBLA-00A07"
log "Device: $DEVICE"
log "WARNING: This will enable hardware encryption. Ensure you have data backups!"

# Validate device exists
if [[ ! -b "$DEVICE" ]]; then
    error_exit "Device $DEVICE does not exist or is not a block device"
fi

# Check if sedutil is installed
if ! command -v sedutil-cli &> /dev/null; then
    log "Installing sedutil package..."
    apt update || error_exit "Failed to update package list"
    apt install -y sedutil || error_exit "Failed to install sedutil"
fi

# Verify sedutil version
SEDUTIL_VERSION=$(sedutil-cli --version 2>/dev/null | head -n1 || echo "unknown")
log "Using sedutil version: $SEDUTIL_VERSION"

# Scan for SED devices
log "Scanning for SED-capable devices..."
sedutil-cli --scan >> "$LOG_FILE" 2>&1 || log "Scan completed with warnings"

# Query device capabilities
log "Querying device capabilities..."
if ! sedutil-cli --query "$DEVICE" >> "$LOG_FILE" 2>&1; then
    error_exit "Failed to query device capabilities. Is this an SED drive?"
fi

# Check if device supports TCG Opal
if ! grep -q "Locking supported" "$LOG_FILE"; then
    error_exit "Device does not appear to support TCG Opal locking"
fi

# Initial setup - takes ownership and sets admin password
log "Performing initial TCG Opal setup..."
if ! sedutil-cli --initialSetup "$PASSWORD" "$DEVICE" >> "$LOG_FILE" 2>&1; then
    error_exit "Initial setup failed"
fi

# Enable locking range 0 (global locking)
log "Enabling locking range 0..."
if ! sedutil-cli --enableLockingRange 0 "$PASSWORD" "$DEVICE" >> "$LOG_FILE" 2>&1; then
    error_exit "Failed to enable locking range"
fi

# Set MBR done to prevent legacy boot attacks
log "Setting MBR done flag..."
if ! sedutil-cli --setMbrDone on "$PASSWORD" "$DEVICE" >> "$LOG_FILE" 2>&1; then
    error_exit "Failed to set MBR done"
fi

# Lock the range to enable encryption
log "Locking range 0 to enable AES-256 encryption..."
if ! sedutil-cli --setLockingRange 0 LK "$PASSWORD" "$DEVICE" >> "$LOG_FILE" 2>&1; then
    error_exit "Failed to lock range"
fi

# Verify encryption is active
log "Verifying encryption status..."
sedutil-cli --query "$DEVICE" | grep -E "(Locking|Range)" >> "$LOG_FILE" 2>&1

log "SED AES-256 setup completed successfully!"
log "The drive is now encrypted with hardware-accelerated AES-256 encryption"
log ""
log "IMPORTANT: Save the password '$PASSWORD' securely!"
log "To unlock the drive: sudo sedutil-cli --unlockRange 0 '$PASSWORD' $DEVICE"
log "To check status: sudo sedutil-cli --query $DEVICE"
log ""
log "Setup log saved to: $LOG_FILE"

# Optional: Create systemd service for automatic unlocking (if configured)
# This would require additional setup for boot-time unlocking

exit 0