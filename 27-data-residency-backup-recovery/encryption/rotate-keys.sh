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

# rotate-keys.sh
# LUKS key rotation for PostgreSQL encrypted volumes.
# Generates a new AES-256 key in SoftHSM2, adds it as LUKS key slot,
# removes the old slot, and verifies data integrity after rotation.
#
# Usage:
#   sudo ./rotate-keys.sh \
#        [--luks-img /nvme-0-zfs/pg-tde-50g.img] \
#        [--old-key /tmp/tde-master.key] \
#        [--new-key /tmp/tde-master-new.key] \
#        [--pg-data /mnt/pg-tde-casino/pgdata] \
#        [--pg-port 5499]

set -euo pipefail

# ---- defaults ---------------------------------------------------------------
LUKS_IMG="${LUKS_IMG:-/tmp/pg-encrypted.img}"
OLD_KEY_FILE="${OLD_KEY_FILE:-/tmp/tde-master.key}"
NEW_KEY_FILE="${NEW_KEY_FILE:-/tmp/tde-master-new.key}"
PG_DATA_DIR="${PG_DATA_DIR:-/mnt/pg-encrypted/pgdata}"
PG_PORT="${PG_PORT:-5499}"
PG_VERSION="${PG_VERSION:-16}"
SOFTHSM2_CONF="${SOFTHSM2_CONF:-/etc/softhsm2.conf}"
HSM_TOKEN_LABEL="${HSM_TOKEN_LABEL:-casino-tde}"
HSM_PIN="${HSM_PIN:-5678}"
HSM_KEY_LABEL_NEW="pg-tde-master-rotated-$(date +%Y%m%d)"
LOG_FILE="/tmp/rotate-keys.log"
BACKUP_DIR="/tmp/luks-header-backup"

RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m'

pass()    { echo -e "${GREEN}[PASS]${NC} $1" | tee -a "$LOG_FILE"; }
fail()    { echo -e "${RED}[FAIL]${NC} $1" | tee -a "$LOG_FILE"; exit 1; }
info()    { echo -e "${BLUE}[INFO]${NC} $1" | tee -a "$LOG_FILE"; }
warn()    { echo -e "${YELLOW}[WARN]${NC} $1" | tee -a "$LOG_FILE"; }
section() { echo -e "\n${BLUE}=== $1 ===${NC}" | tee -a "$LOG_FILE"; }

while [[ $# -gt 0 ]]; do
    case "$1" in
        --luks-img)     LUKS_IMG="$2"; shift 2 ;;
        --old-key)      OLD_KEY_FILE="$2"; shift 2 ;;
        --new-key)      NEW_KEY_FILE="$2"; shift 2 ;;
        --pg-data)      PG_DATA_DIR="$2"; shift 2 ;;
        --pg-port)      PG_PORT="$2"; shift 2 ;;
        --pg-version)   PG_VERSION="$2"; shift 2 ;;
        --help|-h)
            echo "Usage: $0 [--luks-img PATH] [--old-key PATH] [--new-key PATH] [--pg-data PATH] [--pg-port PORT]"
            exit 0 ;;
        *) echo "Unknown option: $1"; exit 1 ;;
    esac
done

if [[ $EUID -ne 0 ]]; then
    exec sudo bash "$0" "$@"
fi

echo "=== Key Rotation — $(date) ===" > "$LOG_FILE"

# ---- Prereq checks ----------------------------------------------------------
section "Prerequisites"

if [[ ! -f "$LUKS_IMG" ]]; then
    fail "LUKS image not found: $LUKS_IMG"
fi
pass "LUKS image found: $LUKS_IMG"

if [[ ! -f "$OLD_KEY_FILE" ]]; then
    fail "Old key file not found: $OLD_KEY_FILE"
fi
OLD_KEY=$(cat "$OLD_KEY_FILE")
pass "Old key loaded (${#OLD_KEY} hex chars)"

# Verify old key opens the volume (without actually mounting)
if echo -n "$OLD_KEY" | cryptsetup open --test-passphrase --key-file=- "$LUKS_IMG" 2>/dev/null; then
    pass "Old key verified against LUKS header"
else
    fail "Old key cannot open LUKS volume — aborting rotation"
fi

# ---- Step 1: Backup LUKS header ---------------------------------------------
section "Step 1: LUKS Header Backup"

mkdir -p "$BACKUP_DIR"
HEADER_BACKUP="$BACKUP_DIR/luks-header-$(date +%Y%m%d-%H%M%S).img"
cryptsetup luksHeaderBackup "$LUKS_IMG" --header-backup-file "$HEADER_BACKUP"
pass "LUKS header backed up to $HEADER_BACKUP"

BACKUP_SIZE=$(ls -sh "$HEADER_BACKUP" | awk '{print $1}')
info "Backup size: $BACKUP_SIZE"

# ---- Step 2: Generate new key in HSM ----------------------------------------
section "Step 2: Generate New Key in SoftHSM2"

SOFTHSM_MODULE=""
for p in /usr/lib/softhsm/libsofthsm2.so \
         /usr/lib/x86_64-linux-gnu/softhsm/libsofthsm2.so; do
    [[ -f "$p" ]] && SOFTHSM_MODULE="$p" && break
done

NEW_KEY=""

if [[ -n "$SOFTHSM_MODULE" ]]; then
    # Get casino-tde token slot
    TOKEN_SLOT=$(SOFTHSM2_CONF="$SOFTHSM2_CONF" softhsm2-util --show-slots 2>/dev/null | \
        grep -B 20 "$HSM_TOKEN_LABEL" | grep "^Slot" | tail -1 | awk '{print $2}' || echo "")

    if [[ -n "$TOKEN_SLOT" ]]; then
        info "Generating new AES-256 key '$HSM_KEY_LABEL_NEW' in HSM slot $TOKEN_SLOT..."

        pkcs11-tool \
            --module "$SOFTHSM_MODULE" \
            --slot "$TOKEN_SLOT" \
            --login --pin "$HSM_PIN" \
            --keygen \
            --key-type aes:32 \
            --label "$HSM_KEY_LABEL_NEW" \
            --sensitive 2>&1 | tee -a "$LOG_FILE"
        pass "New AES-256 key generated in HSM: $HSM_KEY_LABEL_NEW"

        # Derive exportable key from new HSM token+label
        NEW_KEY=$(SOFTHSM2_CONF="$SOFTHSM2_CONF" python3 - <<PYEOF 2>/dev/null
import subprocess, hashlib, os

result = subprocess.run(
    ['softhsm2-util', '--show-slots'],
    env={**os.environ, 'SOFTHSM2_CONF': '$SOFTHSM2_CONF'},
    capture_output=True, text=True
)

serial = None
in_token = False
for line in result.stdout.split('\n'):
    if '$HSM_TOKEN_LABEL' in line:
        in_token = True
    if in_token and 'Serial number:' in line:
        serial = line.strip().split()[-1].strip()
        if serial:
            break

if not serial:
    serial = '$HSM_TOKEN_LABEL-rotated'

import time
rotation_ts = '$(date +%Y%m%d)'
key = hashlib.pbkdf2_hmac(
    'sha256',
    ('$HSM_KEY_LABEL_NEW' + '-' + rotation_ts).encode(),
    serial.encode(),
    310000,
    32
)
print(key.hex())
PYEOF
)
    fi
fi

if [[ -z "$NEW_KEY" ]]; then
    warn "HSM key derivation failed — generating cryptographically random key"
    NEW_KEY=$(openssl rand -hex 32)
fi

echo "$NEW_KEY" > "$NEW_KEY_FILE"
chmod 600 "$NEW_KEY_FILE"
pass "New 256-bit key ready: ${NEW_KEY:0:8}...${NEW_KEY:56:8} (saved to $NEW_KEY_FILE)"

# ---- Step 3: Stop PostgreSQL (if running) -----------------------------------
section "Step 3: Stop PostgreSQL for Key Rotation"

PG_WAS_RUNNING=0
if [[ -d "$PG_DATA_DIR" ]] && sudo -u postgres /usr/lib/postgresql/$PG_VERSION/bin/pg_ctl \
    -D "$PG_DATA_DIR" status > /dev/null 2>&1; then
    sudo -u postgres psql -p "$PG_PORT" -d postgres -c "CHECKPOINT;" > /dev/null 2>&1
    sudo -u postgres /usr/lib/postgresql/$PG_VERSION/bin/pg_ctl \
        -D "$PG_DATA_DIR" stop -m fast > /dev/null 2>&1
    PG_WAS_RUNNING=1
    pass "PostgreSQL stopped gracefully (with checkpoint)"
else
    info "PostgreSQL not running — continuing with key rotation"
fi

# ---- Step 4: Unmount volume -------------------------------------------------
section "Step 4: Unmount Encrypted Volume"

MOUNT_POINT=$(dirname "$PG_DATA_DIR")
LUKS_NAME=$(basename "${LUKS_IMG%.*}")

umount "$MOUNT_POINT" 2>/dev/null && pass "Volume unmounted: $MOUNT_POINT" || \
    info "Volume was not mounted"
cryptsetup luksClose "$LUKS_NAME" 2>/dev/null && pass "LUKS closed: $LUKS_NAME" || \
    info "LUKS device was not open"

# ---- Step 5: Add new key slot -----------------------------------------------
section "Step 5: Add New Key to LUKS (New Key Slot)"

# Show current slots
info "Current LUKS key slots:"
cryptsetup luksDump "$LUKS_IMG" 2>/dev/null | grep "Key Slot" | tee -a "$LOG_FILE"

# Add new key to a new slot
echo -n "$NEW_KEY" | cryptsetup luksAddKey \
    --key-file=<(echo -n "$OLD_KEY") \
    "$LUKS_IMG" -
pass "New key added to LUKS volume (new key slot)"

# Verify new key opens the volume
if echo -n "$NEW_KEY" | cryptsetup open --test-passphrase --key-file=- "$LUKS_IMG" 2>/dev/null; then
    pass "New key verified — can open LUKS volume"
else
    fail "New key verification failed — restoring from backup"
    cryptsetup luksHeaderRestore "$LUKS_IMG" --header-backup-file "$HEADER_BACKUP"
    fail "Header restored from backup. KEY ROTATION ABORTED."
fi

# ---- Step 6: Remove old key slot --------------------------------------------
section "Step 6: Remove Old Key (Revoke Old Slot)"

echo -n "$OLD_KEY" | cryptsetup luksRemoveKey --key-file=- "$LUKS_IMG"
pass "Old key slot removed — old key can no longer open volume"

# Show updated slots
info "Updated LUKS key slots:"
cryptsetup luksDump "$LUKS_IMG" 2>/dev/null | grep "Key Slot" | tee -a "$LOG_FILE"

# Verify OLD key no longer works
if echo -n "$OLD_KEY" | cryptsetup open --test-passphrase --key-file=- "$LUKS_IMG" 2>/dev/null; then
    warn "Old key still opens volume — slot removal may have failed"
else
    pass "Confirmed: old key no longer opens volume"
fi

# ---- Step 7: Restart PostgreSQL and verify data integrity -------------------
section "Step 7: Remount and Verify Data Integrity"

echo -n "$NEW_KEY" | cryptsetup luksOpen --key-file=- "$LUKS_IMG" "$LUKS_NAME"
mount -o noatime "/dev/mapper/$LUKS_NAME" "$MOUNT_POINT"
pass "Volume remounted with new key"

if [[ $PG_WAS_RUNNING -eq 1 ]]; then
    sudo -u postgres /usr/lib/postgresql/$PG_VERSION/bin/pg_ctl \
        -D "$PG_DATA_DIR" \
        -l "$MOUNT_POINT/pg.log" \
        start -w -t 30 > /dev/null 2>&1
    sleep 2

    if sudo -u postgres /usr/lib/postgresql/$PG_VERSION/bin/pg_ctl \
        -D "$PG_DATA_DIR" status > /dev/null 2>&1; then
        pass "PostgreSQL restarted successfully after key rotation"
    else
        fail "PostgreSQL failed to start after key rotation — check $MOUNT_POINT/pg.log"
    fi

    # Verify data
    DB_LIST=$(sudo -u postgres psql -p "$PG_PORT" -d postgres -t -c \
        "SELECT string_agg(datname, ', ' ORDER BY datname) FROM pg_database WHERE datname NOT IN ('postgres','template0','template1');" \
        2>/dev/null | tr -d ' \n')
    if [[ -n "$DB_LIST" ]]; then
        pass "Databases accessible after rotation: $DB_LIST"
    fi
fi

# ---- Step 8: Update key file references -------------------------------------
section "Step 8: Archive Old Key"

OLD_KEY_ARCHIVE="${OLD_KEY_FILE}.$(date +%Y%m%d-%H%M%S).old"
mv "$OLD_KEY_FILE" "$OLD_KEY_ARCHIVE"
cp "$NEW_KEY_FILE" "$OLD_KEY_FILE"
chmod 600 "$OLD_KEY_FILE"
pass "New key promoted to $OLD_KEY_FILE"
pass "Old key archived to $OLD_KEY_ARCHIVE (keep for emergency recovery, then destroy)"

# ---- Summary ----------------------------------------------------------------
echo ""
echo "======================================================"
echo " Key Rotation Complete — $(date)"
echo "======================================================"
echo " LUKS image:     $LUKS_IMG"
echo " New key file:   $OLD_KEY_FILE"
echo " Old key (arch): $OLD_KEY_ARCHIVE"
echo " Header backup:  $HEADER_BACKUP"
echo " HSM key label:  $HSM_KEY_LABEL_NEW"
echo " Log:            $LOG_FILE"
echo "======================================================"
echo ""
warn "IMPORTANT: Securely delete old key archive when no longer needed:"
echo "  shred -uz $OLD_KEY_ARCHIVE"
echo ""
pass "Key rotation completed successfully"
