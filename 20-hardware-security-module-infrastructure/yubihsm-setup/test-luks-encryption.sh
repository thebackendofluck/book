#!/usr/bin/env bash
# Companion code for "The Backend of Luck" - Chapter 20, Hardware Security Module Infrastructure.
# https://thebackendofluck.com | https://github.com/thebackendofluck/book
# SPDX-License-Identifier: Apache-2.0
#
# FOR TESTING AND EVALUATION ONLY. NOT FOR PRODUCTION USE.
# Published to demonstrate the patterns explained in the book. This code is
# not certified for real-money gaming: operating a gambling platform requires
# your own licence, independent test-lab certification (GLI, eCOGRA or
# equivalent) and regulator approval.

# test-luks-encryption.sh
# LUKS2 disk encryption test using OpenBao Transit for key wrapping.
# Validates the full encrypt -> close -> unwrap -> open cycle.
# Prerequisites: OpenBao running at https://127.0.0.1:8200, Transit engine enabled,
#                cryptsetup installed, root/sudo access.
# Usage: bash test-luks-encryption.sh

set -euo pipefail

BAO_ADDR="${BAO_ADDR:-https://127.0.0.1:8200}"
TRANSIT_KEY="${TRANSIT_KEY:-luks-master}"
IMAGE_SIZE="${IMAGE_SIZE:-64M}"
IMAGE_PATH="${IMAGE_PATH:-/tmp/luks-hsm-test.img}"
EVIDENCE_DIR="${EVIDENCE_DIR:-/opt/yubihsm-evidence}"
MAPPER_NAME="luks-hsm-test"
MOUNT_DIR="/mnt/luks-hsm-test"

log()  { echo "[$(date '+%H:%M:%S')] $*"; }
pass() { echo "[PASS] $*"; }
fail() { echo "[FAIL] $*" >&2; exit 1; }

cleanup() {
    sudo umount "$MOUNT_DIR" 2>/dev/null || true
    sudo cryptsetup close "$MAPPER_NAME" 2>/dev/null || true
    if [ -n "${LOOP_DEV:-}" ]; then
        sudo losetup -d "$LOOP_DEV" 2>/dev/null || true
    fi
    rm -f "$IMAGE_PATH"
}
trap cleanup EXIT

log "=== LUKS2 + OpenBao Transit Key Wrapping Test ==="

# Require BAO_TOKEN
if [ -z "${BAO_TOKEN:-}" ]; then
    fail "BAO_TOKEN environment variable is required"
fi

log "Creating ${IMAGE_SIZE} file-backed block device..."
sudo fallocate -l "$IMAGE_SIZE" "$IMAGE_PATH"
LOOP_DEV="$(sudo losetup -f --show "$IMAGE_PATH")"
log "Loop device: $LOOP_DEV"

# Generate LUKS passphrase
LUKS_PASS="$(openssl rand -hex 32)"
log "Passphrase generated (${#LUKS_PASS} chars)"

# Wrap passphrase with Transit (HSM-backed key)
PASS_B64="$(echo -n "$LUKS_PASS" | base64 -w0)"
WRAPPED="$(bao write -tls-skip-verify -field=ciphertext \
    "transit/encrypt/${TRANSIT_KEY}" plaintext="$PASS_B64" 2>/dev/null)"
if [ -z "$WRAPPED" ]; then
    fail "Failed to encrypt LUKS passphrase via OpenBao Transit"
fi
log "LUKS passphrase wrapped: ${WRAPPED:0:30}..."

# Format LUKS2
log "Formatting LUKS2 volume..."
echo -n "$LUKS_PASS" | sudo cryptsetup luksFormat \
    --type luks2 --key-size 512 --hash sha512 --batch-mode "$LOOP_DEV" -
log "LUKS2 formatted (exit: $?)"

# Open and write test data
log "Opening volume and writing test data..."
echo -n "$LUKS_PASS" | sudo cryptsetup open "$LOOP_DEV" "$MAPPER_NAME" -
sudo mkfs.ext4 -q "/dev/mapper/${MAPPER_NAME}"
sudo mkdir -p "$MOUNT_DIR"
sudo mount "/dev/mapper/${MAPPER_NAME}" "$MOUNT_DIR"
echo "PCI DSS test: $(date -u) | key_wrapped_by=openbao_transit" | sudo tee "$MOUNT_DIR/test.txt" > /dev/null
sudo umount "$MOUNT_DIR"
sudo cryptsetup close "$MAPPER_NAME"
log "Volume closed."

# Re-open using Transit-unwrapped key
log "Unwrapping key from OpenBao Transit..."
UNWRAPPED_B64="$(bao write -tls-skip-verify -field=plaintext \
    "transit/decrypt/${TRANSIT_KEY}" ciphertext="$WRAPPED" 2>/dev/null)"
RECOVERED_PASS="$(echo "$UNWRAPPED_B64" | base64 -d)"

if [ "$RECOVERED_PASS" != "$LUKS_PASS" ]; then
    fail "Recovered passphrase does not match original"
fi
log "Passphrase recovered correctly from Transit."

echo -n "$RECOVERED_PASS" | sudo cryptsetup open "$LOOP_DEV" "$MAPPER_NAME" -
sudo mount "/dev/mapper/${MAPPER_NAME}" "$MOUNT_DIR"
DATA="$(sudo cat "$MOUNT_DIR/test.txt")"
log "Data read: $DATA"

if echo "$DATA" | grep -q "PCI DSS test"; then
    pass "LUKS2 encrypt/close/unwrap/open cycle complete. Data intact."
else
    fail "Data verification failed after key unwrap"
fi

# Save evidence
mkdir -p "$EVIDENCE_DIR"
{
    echo "LUKS Test Result: PASS"
    echo "Date: $(date -u)"
    echo "LUKS version: LUKS2"
    echo "Key wrapping: OpenBao Transit (${TRANSIT_KEY})"
    echo "Cycle: format -> open -> write -> close -> unwrap -> reopen -> verify"
} >> "${EVIDENCE_DIR}/luks-test-result.txt"

log "Evidence saved to ${EVIDENCE_DIR}/luks-test-result.txt"
