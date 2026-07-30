#!/usr/bin/env bash
# Companion code for "The Backend of Luck" - Chapter 27, Data Residency and Backup/Recovery.
# https://thebackendofluck.com | https://github.com/thebackendofluck/book
# SPDX-License-Identifier: Apache-2.0
#
# FOR TESTING AND EVALUATION ONLY. NOT FOR PRODUCTION USE.
# Published to demonstrate the patterns explained in the book. This code is
# not certified for real-money gaming: operating a gambling platform requires
# your own licence, independent test-lab certification (GLI, eCOGRA or
# equivalent) and regulator approval.

# =============================================================================
# pgBackRest Encryption Key Rotation
# =============================================================================
# Rotates the AES-256-CBC passphrase used to encrypt the backup repository.
#
# pgBackRest DOES NOT support re-encrypting existing backup files with a new
# key. The standard approach is:
#   1. Archive (and securely store) the old key for historical backup access
#   2. Run a new full backup with the new key (old backups expire naturally)
#   3. Test restore with the new key before decommissioning old backups
#
# For genuine re-encryption of all files, use: pgbackrest --repo1-cipher-pass-new
# This feature requires pgBackRest 2.40+.
#
# Usage:
#   sudo ./key-rotation.sh [--stanza casino] [--auto] [--test-restore]
#
# Options:
#   --auto          Non-interactive (generates new passphrase automatically)
#   --test-restore  Run a restore check after rotation to verify new key
# =============================================================================

set -euo pipefail

STANZA="${STANZA:-casino}"
CONF_FILE="${CONF_FILE:-/etc/pgbackrest/pgbackrest.conf}"
KEY_FILE="${KEY_FILE:-/etc/pgbackrest/.cipher_pass}"
KEY_ARCHIVE_DIR="${KEY_ARCHIVE_DIR:-/etc/pgbackrest/key-archive}"
AUTO=0
TEST_RESTORE=0
PG_PORT="${PG_PORT:-5434}"

while [[ $# -gt 0 ]]; do
    case "$1" in
        --stanza)       STANZA="$2";   shift 2 ;;
        --auto)         AUTO=1;        shift ;;
        --test-restore) TEST_RESTORE=1; shift ;;
        --pg-port)      PG_PORT="$2";  shift 2 ;;
        *) echo "Unknown: $1"; exit 1 ;;
    esac
done

log() { echo "[$(date -u +%H:%M:%S)] $*"; }

if [[ $EUID -ne 0 ]]; then
    echo "Run as root"
    exit 1
fi

# ---------------------------------------------------------------------------
# Step 1: Archive the current key
# ---------------------------------------------------------------------------
log "=== pgBackRest Key Rotation ==="
log ""
log "IMPORTANT: pgBackRest encrypts backups per-repository. Existing backup files"
log "cannot be re-encrypted — old backups remain accessible only with the old key."
log ""

CURRENT_PASS=$(cat "${KEY_FILE}" 2>/dev/null || echo "")
if [[ -z "$CURRENT_PASS" ]]; then
    log "ERROR: Cannot read current passphrase from ${KEY_FILE}"
    exit 1
fi

DATE_STAMP=$(date -u +"%Y%m%dT%H%M%SZ")

mkdir -p "${KEY_ARCHIVE_DIR}"
chmod 700 "${KEY_ARCHIVE_DIR}"

ARCHIVE_FILE="${KEY_ARCHIVE_DIR}/cipher_pass_${DATE_STAMP}"
cp "${KEY_FILE}" "${ARCHIVE_FILE}"
chmod 400 "${ARCHIVE_FILE}"
log "Current key archived to: ${ARCHIVE_FILE}"
log "  (Keep this for restoring pre-rotation backups)"

# ---------------------------------------------------------------------------
# Step 2: Generate new passphrase
# ---------------------------------------------------------------------------
if [[ "$AUTO" == "1" ]]; then
    NEW_PASS=$(openssl rand -base64 32)
    log "Auto-generated new passphrase"
else
    echo ""
    read -r -s -p "Enter new encryption passphrase (blank to auto-generate): " NEW_PASS
    echo ""
    if [[ -z "$NEW_PASS" ]]; then
        NEW_PASS=$(openssl rand -base64 32)
        log "Auto-generated new passphrase"
    fi
    read -r -s -p "Confirm new passphrase: " NEW_PASS_CONFIRM
    echo ""
    if [[ "$NEW_PASS" != "$NEW_PASS_CONFIRM" ]]; then
        log "ERROR: Passphrases do not match"
        exit 1
    fi
fi

# ---------------------------------------------------------------------------
# Step 3: Update the configuration
# ---------------------------------------------------------------------------
log "Updating ${CONF_FILE} with new passphrase..."

# Use sed to replace the cipher-pass line
ESCAPED_OLD=$(echo "$CURRENT_PASS" | sed 's/[&/\]/\\&/g')
ESCAPED_NEW=$(echo "$NEW_PASS" | sed 's/[&/\]/\\&/g')
sed -i "s|repo1-cipher-pass=${ESCAPED_OLD}|repo1-cipher-pass=${ESCAPED_NEW}|" "${CONF_FILE}"

# Save new key
echo "$NEW_PASS" > "${KEY_FILE}"
chmod 600 "${KEY_FILE}"
chown postgres:postgres "${KEY_FILE}"

log "Configuration updated"

# ---------------------------------------------------------------------------
# Step 4: Destroy existing backup repository (encrypted with old key)
# ---------------------------------------------------------------------------
log ""
log "WARNING: Existing backups are encrypted with the OLD key."
log "A new full backup must be created with the new key."

if [[ "$AUTO" != "1" ]]; then
    read -r -p "Run new full backup now with new key? (YES/no): " CONFIRM
    if [[ "$CONFIRM" != "YES" ]]; then
        log "Skipping immediate backup. Run: pgbackrest --stanza=${STANZA} --type=full backup"
        log "NOTE: The old stanza info files are still encrypted with the old key."
        log "You must run stanza-upgrade or stanza-create after the backup."
        exit 0
    fi
fi

# ---------------------------------------------------------------------------
# Step 5: Re-initialize stanza with new key and run full backup
# ---------------------------------------------------------------------------
log "Re-initializing stanza with new key..."
sudo -u postgres pgbackrest --stanza="${STANZA}" stanza-create 2>&1 | tail -3

log "Running full backup with new key..."
BACKUP_START=$(date +%s)
sudo -u postgres pgbackrest --stanza="${STANZA}" --type=full backup 2>&1 | tail -5
BACKUP_END=$(date +%s)
log "Backup completed in $((BACKUP_END - BACKUP_START)) seconds"

# ---------------------------------------------------------------------------
# Step 6: Test restore (optional)
# ---------------------------------------------------------------------------
if [[ "$TEST_RESTORE" == "1" ]]; then
    log ""
    log "Running restore check to verify new key..."
    if sudo -u postgres pgbackrest --stanza="${STANZA}" check >/dev/null 2>&1; then
        log "Restore check: PASSED"
    else
        log "Restore check: FAILED"
        exit 1
    fi
fi

# ---------------------------------------------------------------------------
# Step 7: Summary
# ---------------------------------------------------------------------------
log ""
log "=== Key Rotation Complete ==="
log "  Old key archived: ${ARCHIVE_FILE}"
log "  New key:          ${KEY_FILE}"
log "  Stanza:           ${STANZA}"
log ""
log "IMPORTANT ACTIONS:"
log "  1. Back up ${KEY_ARCHIVE_DIR}/ to a secure location (HSM/Vault)"
log "  2. Old backups (if any) require the archived key to restore"
log "  3. New backups use the new key automatically"
log ""
log "  To restore a pre-rotation backup:"
log "    OLD_KEY=\$(cat ${ARCHIVE_FILE})"
log "    PGBACKREST_REPO1_CIPHER_PASS=\$OLD_KEY pgbackrest --stanza=${STANZA} info"
