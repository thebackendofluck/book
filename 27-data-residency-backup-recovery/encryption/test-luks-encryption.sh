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

# test-luks-encryption.sh
# Tests LUKS disk encryption for PostgreSQL data directories
# Verifies: create volume, format, mount, initdb, start PG, verify ciphertext at rest
#
# Usage: sudo ./test-luks-encryption.sh [--cleanup]

set -euo pipefail

LUKS_IMG="/tmp/pg-encrypted.img"
LUKS_NAME="pg-encrypted"
LUKS_MAPPER="/dev/mapper/$LUKS_NAME"
MOUNT_POINT="/tmp/pg-tde-test"
PG_VERSION="16"
PG_LUKS_DATA="$MOUNT_POINT/pgdata"
PG_LUKS_PORT=5499
LUKS_PASS="testpass-chapter27-tde"
LOG_FILE="/tmp/test-luks-encryption.log"
RESULTS_FILE="/tmp/luks-test-results.txt"

RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m'

pass() { echo -e "${GREEN}[PASS]${NC} $1" | tee -a "$LOG_FILE"; }
fail() { echo -e "${RED}[FAIL]${NC} $1" | tee -a "$LOG_FILE"; }
info() { echo -e "${BLUE}[INFO]${NC} $1" | tee -a "$LOG_FILE"; }
warn() { echo -e "${YELLOW}[WARN]${NC} $1" | tee -a "$LOG_FILE"; }

cleanup() {
    info "Cleaning up..."
    sudo -u postgres pg_ctlcluster $PG_VERSION luks-test stop 2>/dev/null || true
    pg_ctlcluster $PG_VERSION luks-test stop 2>/dev/null || true
    # Stop any postgres process using the luks port
    pkill -f "port=$PG_LUKS_PORT" 2>/dev/null || true
    sleep 1
    umount "$MOUNT_POINT" 2>/dev/null || true
    cryptsetup luksClose "$LUKS_NAME" 2>/dev/null || true
    rm -f "$LUKS_IMG"
    rm -rf "$MOUNT_POINT"
    pg_dropcluster $PG_VERSION luks-test 2>/dev/null || true
    info "Cleanup complete"
}

if [[ "${1:-}" == "--cleanup" ]]; then
    cleanup
    exit 0
fi

if [[ $EUID -ne 0 ]]; then
    echo "Re-running with sudo..."
    exec sudo bash "$0" "$@"
fi

echo "======================================================" | tee "$LOG_FILE"
echo " LUKS Encryption Test for PostgreSQL — $(date)"        | tee -a "$LOG_FILE"
echo "======================================================" | tee -a "$LOG_FILE"

# --- Step 1: Check prerequisites ---
info "Step 1: Checking prerequisites"
for tool in cryptsetup mkfs.ext4 dd; do
    if command -v "$tool" &>/dev/null; then
        pass "Tool available: $tool"
    else
        fail "Missing tool: $tool"
        exit 1
    fi
done

if grep -q aes /proc/cpuinfo; then
    pass "AES-NI hardware acceleration available"
else
    warn "AES-NI not detected — performance will be degraded"
fi

# --- Step 2: Create LUKS-encrypted volume ---
info "Step 2: Creating 500MB LUKS-encrypted volume"
dd if=/dev/zero of="$LUKS_IMG" bs=1M count=500 2>/dev/null
pass "Created 500MB image: $LUKS_IMG"

echo -n "$LUKS_PASS" | cryptsetup luksFormat \
    --type luks2 \
    --cipher aes-xts-plain64 \
    --key-size 256 \
    --hash sha256 \
    --batch-mode \
    --key-file=- \
    "$LUKS_IMG"
pass "LUKS2 volume formatted (AES-XTS-256)"

echo -n "$LUKS_PASS" | cryptsetup luksOpen --key-file=- "$LUKS_IMG" "$LUKS_NAME"
pass "LUKS volume opened as $LUKS_MAPPER"

mkfs.ext4 -q "$LUKS_MAPPER"
pass "ext4 filesystem created on encrypted volume"

mkdir -p "$MOUNT_POINT"
mount "$LUKS_MAPPER" "$MOUNT_POINT"
pass "Encrypted volume mounted at $MOUNT_POINT"

# Show LUKS details
info "LUKS volume details:"
cryptsetup luksDump "$LUKS_IMG" 2>/dev/null | grep -E "Version:|Cipher:|Key size:|Hash:" | sed 's/^/  /' | tee -a "$LOG_FILE"

# --- Step 3: Initialize PostgreSQL on encrypted volume ---
info "Step 3: Initializing PostgreSQL data directory on encrypted volume"
mkdir -p "$PG_LUKS_DATA"
chown -R postgres:postgres "$MOUNT_POINT"

sudo -u postgres /usr/lib/postgresql/$PG_VERSION/bin/initdb \
    -D "$PG_LUKS_DATA" \
    -E UTF8 \
    --locale=en_US.UTF-8 \
    -A trust \
    > /dev/null 2>&1
pass "PostgreSQL cluster initialized on LUKS volume"

# Configure a distinct port to avoid conflict with running cluster
cat >> "$PG_LUKS_DATA/postgresql.conf" <<EOF

# LUKS test instance overrides
port = $PG_LUKS_PORT
listen_addresses = 'localhost'
log_destination = 'stderr'
logging_collector = off
EOF

# --- Step 4: Start PostgreSQL on encrypted volume ---
info "Step 4: Starting PostgreSQL on encrypted volume (port $PG_LUKS_PORT)"
sudo -u postgres /usr/lib/postgresql/$PG_VERSION/bin/pg_ctl \
    -D "$PG_LUKS_DATA" \
    -l "$MOUNT_POINT/pg.log" \
    start \
    -w \
    -t 30 > /dev/null 2>&1

sleep 2

if sudo -u postgres /usr/lib/postgresql/$PG_VERSION/bin/pg_ctl \
    -D "$PG_LUKS_DATA" status > /dev/null 2>&1; then
    pass "PostgreSQL started on LUKS-encrypted volume"
else
    fail "PostgreSQL failed to start on LUKS volume"
    cat "$MOUNT_POINT/pg.log" 2>/dev/null | tail -20
    cleanup
    exit 1
fi

# --- Step 5: Insert casino test data ---
info "Step 5: Inserting casino player/transaction test data"
sudo -u postgres psql -p $PG_LUKS_PORT -d postgres > /dev/null 2>&1 <<'EOSQL'
CREATE DATABASE casino_luks_test;
EOSQL

sudo -u postgres psql -p $PG_LUKS_PORT -d casino_luks_test > /dev/null 2>&1 <<'EOSQL'
CREATE TABLE players (
    id          SERIAL PRIMARY KEY,
    username    VARCHAR(50) NOT NULL,
    email       VARCHAR(120),
    balance     NUMERIC(12,2) DEFAULT 0.00,
    created_at  TIMESTAMPTZ DEFAULT now()
);

CREATE TABLE transactions (
    id          SERIAL PRIMARY KEY,
    player_id   INTEGER REFERENCES players(id),
    amount      NUMERIC(12,2),
    txn_type    VARCHAR(20),
    created_at  TIMESTAMPTZ DEFAULT now()
);

INSERT INTO players (username, email, balance) VALUES
  ('alice_poker',  'alice@example.com',  5000.00),
  ('bob_slots',    'bob@example.com',    1250.75),
  ('carol_roulette','carol@example.com', 8800.00);

INSERT INTO transactions (player_id, amount, txn_type) VALUES
  (1, -250.00, 'bet'),
  (1,  750.00, 'win'),
  (2,  -50.00, 'bet'),
  (3, 2000.00, 'deposit');
EOSQL
pass "Inserted casino player and transaction records"

# Verify reads
PLAYER_COUNT=$(sudo -u postgres psql -p $PG_LUKS_PORT -d casino_luks_test -t -c "SELECT COUNT(*) FROM players;" 2>/dev/null | tr -d ' ')
TXN_COUNT=$(sudo -u postgres psql -p $PG_LUKS_PORT -d casino_luks_test -t -c "SELECT COUNT(*) FROM transactions;" 2>/dev/null | tr -d ' ')

if [[ "$PLAYER_COUNT" == "3" ]] && [[ "$TXN_COUNT" == "4" ]]; then
    pass "Data readable while volume is mounted: $PLAYER_COUNT players, $TXN_COUNT transactions"
else
    fail "Unexpected row counts: players=$PLAYER_COUNT, txns=$TXN_COUNT"
fi

# --- Step 6: Verify ciphertext at rest ---
info "Step 6: Verifying ciphertext at rest (unmounted)"
sudo -u postgres /usr/lib/postgresql/$PG_VERSION/bin/pg_ctl \
    -D "$PG_LUKS_DATA" \
    stop -m fast > /dev/null 2>&1
sleep 2
pass "PostgreSQL stopped"

umount "$MOUNT_POINT"
cryptsetup luksClose "$LUKS_NAME"
pass "LUKS volume closed"

# Check for plaintext in raw image
FOUND_PLAINTEXT=0
for term in "alice_poker" "bob_slots" "carol_roulette" "alice@example.com"; do
    if strings "$LUKS_IMG" 2>/dev/null | grep -q "$term"; then
        fail "Plaintext found in raw image: '$term'"
        FOUND_PLAINTEXT=1
    fi
done

if [[ $FOUND_PLAINTEXT -eq 0 ]]; then
    pass "No plaintext data found in raw image — all data is encrypted at rest"
fi

# Verify LUKS header is present
if strings "$LUKS_IMG" 2>/dev/null | grep -q "LUKS"; then
    pass "LUKS magic header present in image"
fi

# Verify image is not all zeros (encrypted content exists)
NONZERO=$(dd if="$LUKS_IMG" bs=512 skip=8 count=16 2>/dev/null | od -A x -t x1 | grep -v " 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00" | wc -l)
if [[ "$NONZERO" -gt 0 ]]; then
    pass "Encrypted content verified: non-zero ciphertext blocks present"
fi

# --- Step 7: Verify data recoverable after re-open ---
info "Step 7: Verifying data is recoverable after re-open"
echo -n "$LUKS_PASS" | cryptsetup luksOpen --key-file=- "$LUKS_IMG" "$LUKS_NAME"
mount "$LUKS_MAPPER" "$MOUNT_POINT"

sudo -u postgres /usr/lib/postgresql/$PG_VERSION/bin/pg_ctl \
    -D "$PG_LUKS_DATA" \
    -l "$MOUNT_POINT/pg.log" \
    start \
    -w \
    -t 30 > /dev/null 2>&1
sleep 2

PLAYER_COUNT2=$(sudo -u postgres psql -p $PG_LUKS_PORT -d casino_luks_test -t -c "SELECT COUNT(*) FROM players;" 2>/dev/null | tr -d ' ')
if [[ "$PLAYER_COUNT2" == "3" ]]; then
    pass "Data fully recoverable after LUKS close/reopen: $PLAYER_COUNT2 players"
else
    fail "Data not recoverable after LUKS reopen: got $PLAYER_COUNT2 players"
fi

# --- Summary ---
sudo -u postgres /usr/lib/postgresql/$PG_VERSION/bin/pg_ctl \
    -D "$PG_LUKS_DATA" \
    stop -m fast > /dev/null 2>&1

echo ""
echo "======================================================" | tee -a "$LOG_FILE"
echo " LUKS Test Summary — $(date)"                           | tee -a "$LOG_FILE"
echo "======================================================" | tee -a "$LOG_FILE"
echo " Image:     $LUKS_IMG (500MB)"                          | tee -a "$LOG_FILE"
echo " Cipher:    AES-XTS-256 (LUKS2)"                        | tee -a "$LOG_FILE"
echo " PG Port:   $PG_LUKS_PORT"                              | tee -a "$LOG_FILE"
echo " Mount:     $MOUNT_POINT"                               | tee -a "$LOG_FILE"
echo " Log:       $LOG_FILE"                                  | tee -a "$LOG_FILE"
echo "======================================================" | tee -a "$LOG_FILE"

cleanup
pass "All LUKS encryption tests completed"
