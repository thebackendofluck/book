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

# LUKS-encrypted volume setup for PostgreSQL HA data directories
# In production: run BEFORE docker compose up, then mount /dev/mapper/pg-* into containers.
#
# Usage:
#   sudo ./luks-setup.sh --create   # first time: create + format LUKS volumes
#   sudo ./luks-setup.sh --open     # on reboot: decrypt and mount
#   sudo ./luks-setup.sh --close    # before shutdown: unmount and close
#   sudo ./luks-setup.sh --status   # show current status
#
# For Docker volumes: after --open, the mapped devices appear at
#   /dev/mapper/pg-primary-data  and  /dev/mapper/pg-replica-data
# Use bind-mounts in docker-compose.yml to point containers at these.

set -euo pipefail

LUKS_KEY_FILE="/etc/postgresql-ha/luks.key"   # store in secure location (TPM, HSM)
IMG_DIR="/var/lib/postgresql-ha-volumes"
PRIMARY_IMG="${IMG_DIR}/pg-primary.img"
REPLICA_IMG="${IMG_DIR}/pg-replica.img"
PRIMARY_MAP="pg-primary-data"
REPLICA_MAP="pg-replica-data"
VOLUME_SIZE_GB="${VOLUME_SIZE_GB:-20}"         # adjust for production

RED='\033[0;31m'; GREEN='\033[0;32m'; YELLOW='\033[1;33m'; NC='\033[0m'
info()  { echo -e "${YELLOW}[LUKS]${NC} $*"; }
pass()  { echo -e "${GREEN}[OK]${NC} $*"; }
fail()  { echo -e "${RED}[ERR]${NC} $*"; exit 1; }

check_root() { [ "$(id -u)" -eq 0 ] || fail "LUKS setup requires root. Run with sudo."; }

install_deps() {
    if ! command -v cryptsetup &>/dev/null; then
        info "Installing cryptsetup..."
        apt-get update -qq && apt-get install -y cryptsetup
    fi
}

generate_key() {
    if [ ! -f "$LUKS_KEY_FILE" ]; then
        mkdir -p "$(dirname $LUKS_KEY_FILE)"
        chmod 700 "$(dirname $LUKS_KEY_FILE)"
        # In production: fetch from YubiHSM / SoftHSM via PKCS#11
        # Here we generate a random key and store it — secure the file via HSM in prod
        dd if=/dev/urandom bs=64 count=1 of="$LUKS_KEY_FILE" status=none
        chmod 400 "$LUKS_KEY_FILE"
        pass "LUKS key generated at $LUKS_KEY_FILE"
        info "WARNING: In production, protect this key with YubiHSM or TPM 2.0"
    else
        info "LUKS key already exists at $LUKS_KEY_FILE"
    fi
}

create_volume() {
    local IMG=$1 MAP=$2
    if [ -f "$IMG" ]; then
        info "Image already exists: $IMG — skipping creation"
        return
    fi
    info "Creating ${VOLUME_SIZE_GB}GB image at $IMG..."
    fallocate -l "${VOLUME_SIZE_GB}G" "$IMG"

    info "LUKS formatting $IMG (AES-256-XTS, SHA-512 PBKDF2)..."
    cryptsetup luksFormat \
        --type luks2 \
        --cipher aes-xts-plain64 \
        --key-size 512 \
        --hash sha512 \
        --pbkdf argon2id \
        --pbkdf-memory 131072 \
        --pbkdf-parallel 4 \
        --batch-mode \
        --key-file "$LUKS_KEY_FILE" \
        "$IMG"

    info "Opening and formatting ext4 on $MAP..."
    cryptsetup open --key-file "$LUKS_KEY_FILE" "$IMG" "$MAP"
    mkfs.ext4 -L "$MAP" "/dev/mapper/$MAP"
    cryptsetup close "$MAP"
    pass "Volume $MAP created and formatted"
}

open_volume() {
    local IMG=$1 MAP=$2
    if [ -e "/dev/mapper/$MAP" ]; then
        info "Already open: /dev/mapper/$MAP"
        return
    fi
    [ -f "$IMG" ] || fail "Image not found: $IMG — run --create first"
    cryptsetup open --key-file "$LUKS_KEY_FILE" "$IMG" "$MAP"
    pass "Opened: /dev/mapper/$MAP"
}

close_volume() {
    local MAP=$1
    if [ -e "/dev/mapper/$MAP" ]; then
        cryptsetup close "$MAP"
        pass "Closed: $MAP"
    else
        info "$MAP is not open"
    fi
}

cmd_create() {
    check_root
    install_deps
    mkdir -p "$IMG_DIR"
    generate_key
    create_volume "$PRIMARY_IMG" "$PRIMARY_MAP"
    create_volume "$REPLICA_IMG"  "$REPLICA_MAP"
    info ""
    info "LUKS volumes created. To use them:"
    info "  1. sudo ./luks-setup.sh --open"
    info "  2. Mount /dev/mapper/${PRIMARY_MAP} into the pg-primary container"
    info "     (update docker-compose.yml volumes section)"
    info "  3. docker compose up -d"
}

cmd_open() {
    check_root
    install_deps
    [ -f "$LUKS_KEY_FILE" ] || fail "Key not found: $LUKS_KEY_FILE"
    open_volume "$PRIMARY_IMG" "$PRIMARY_MAP"
    open_volume "$REPLICA_IMG"  "$REPLICA_MAP"
    pass "Both volumes open. Devices: /dev/mapper/${PRIMARY_MAP}, /dev/mapper/${REPLICA_MAP}"
}

cmd_close() {
    check_root
    info "Stopping Docker containers first..."
    docker compose down 2>/dev/null || true
    close_volume "$PRIMARY_MAP"
    close_volume "$REPLICA_MAP"
}

cmd_status() {
    info "=== LUKS volume status ==="
    for MAP in "$PRIMARY_MAP" "$REPLICA_MAP"; do
        if [ -e "/dev/mapper/$MAP" ]; then
            echo -e "${GREEN}OPEN${NC}   /dev/mapper/$MAP"
            cryptsetup status "$MAP" 2>/dev/null | grep -E 'cipher|keysize|device' | sed 's/^/         /'
        else
            echo -e "${YELLOW}CLOSED${NC} $MAP"
        fi
    done
    echo ""
    info "=== Key file ==="
    [ -f "$LUKS_KEY_FILE" ] && ls -la "$LUKS_KEY_FILE" || echo "NOT FOUND: $LUKS_KEY_FILE"
}

MODE="${1:---status}"
case "$MODE" in
    --create) cmd_create ;;
    --open)   cmd_open ;;
    --close)  cmd_close ;;
    --status) cmd_status ;;
    *) echo "Usage: $0 [--create|--open|--close|--status]"; exit 1 ;;
esac
