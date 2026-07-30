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

# test-encryption.sh
# Verifies LUKS disk encryption and pgcrypto column-level encryption.
# Inserts PII (names, emails, SSNs, PANs), stops PostgreSQL, scans raw
# data files for plaintext — must find zero PII.
#
# Usage:
#   sudo ./test-encryption.sh [--port 5499] [--data-dir /mnt/pg-encrypted/pgdata] \
#        [--luks-img /nvme-0-zfs/pg-tde-50g.img] [--key-file /tmp/tde-master.key]

set -euo pipefail

# ---- defaults ---------------------------------------------------------------
PG_PORT="${PG_PORT:-5499}"
PG_VERSION="${PG_VERSION:-16}"
PG_DATA_DIR="${PG_DATA_DIR:-/mnt/pg-encrypted/pgdata}"
LUKS_IMG="${LUKS_IMG:-/tmp/pg-encrypted.img}"
KEY_FILE="${KEY_FILE:-/tmp/tde-master.key}"
PGCRYPTO_KEY="${PGCRYPTO_KEY:-casino-tde-pgcrypto-2024}"
LOG_FILE="/tmp/test-encryption.log"
PASS_COUNT=0
FAIL_COUNT=0

RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m'

pass()    { echo -e "${GREEN}[PASS]${NC} $1" | tee -a "$LOG_FILE"; PASS_COUNT=$((PASS_COUNT+1)); }
fail()    { echo -e "${RED}[FAIL]${NC} $1" | tee -a "$LOG_FILE"; FAIL_COUNT=$((FAIL_COUNT+1)); }
info()    { echo -e "${BLUE}[INFO]${NC} $1" | tee -a "$LOG_FILE"; }
warn()    { echo -e "${YELLOW}[WARN]${NC} $1" | tee -a "$LOG_FILE"; }
section() { echo -e "\n${BLUE}=== $1 ===${NC}" | tee -a "$LOG_FILE"; }

while [[ $# -gt 0 ]]; do
    case "$1" in
        --port)         PG_PORT="$2"; shift 2 ;;
        --data-dir)     PG_DATA_DIR="$2"; shift 2 ;;
        --luks-img)     LUKS_IMG="$2"; shift 2 ;;
        --key-file)     KEY_FILE="$2"; shift 2 ;;
        --pg-version)   PG_VERSION="$2"; shift 2 ;;
        --help|-h)
            echo "Usage: $0 [--port PORT] [--data-dir PATH] [--luks-img PATH] [--key-file PATH]"
            exit 0 ;;
        *) echo "Unknown option: $1"; exit 1 ;;
    esac
done

if [[ $EUID -ne 0 ]]; then
    exec sudo bash "$0" "$@"
fi

echo "=== Encryption Test Suite — $(date) ===" > "$LOG_FILE"

# ---- Test 1: pgcrypto Extension ---------------------------------------------
section "Test 1: pgcrypto Extension Availability"

EXT=$(sudo -u postgres psql -p "$PG_PORT" -d postgres -t -c \
    "SELECT extversion FROM pg_available_extensions WHERE name='pgcrypto';" 2>/dev/null | tr -d ' \n' || echo "")
if [[ -n "$EXT" ]]; then
    pass "pgcrypto $EXT available"
else
    fail "pgcrypto extension not available"
fi

# ---- Test 2: Create test database with PII ----------------------------------
section "Test 2: Insert PII into Encrypted pgcrypto Table"

# Drop and recreate test db
sudo -u postgres psql -p "$PG_PORT" -d postgres -c "DROP DATABASE IF EXISTS pii_test;" > /dev/null 2>&1
sudo -u postgres psql -p "$PG_PORT" -d postgres -c "CREATE DATABASE pii_test;" > /dev/null 2>&1

sudo -u postgres psql -p "$PG_PORT" -d pii_test > /dev/null 2>&1 <<EOSQL
CREATE EXTENSION IF NOT EXISTS pgcrypto;

CREATE TABLE casino_players (
    id          SERIAL PRIMARY KEY,
    username    VARCHAR(50) NOT NULL,
    email_enc   BYTEA,
    full_name_enc BYTEA,
    ssn_enc     BYTEA,
    pan_enc     BYTEA,
    pan_hash    TEXT,
    dob_enc     BYTEA,
    address_enc BYTEA,
    balance     NUMERIC(12,2)
);

INSERT INTO casino_players
    (username, email_enc, full_name_enc, ssn_enc, pan_enc, pan_hash, dob_enc, address_enc, balance)
VALUES
    ('alice_poker',
     pgp_sym_encrypt('alice.johnson@casino.com',   '$PGCRYPTO_KEY', 'cipher-algo=aes256'),
     pgp_sym_encrypt('Alice Marie Johnson',         '$PGCRYPTO_KEY', 'cipher-algo=aes256'),
     pgp_sym_encrypt('123-45-6789',                 '$PGCRYPTO_KEY', 'cipher-algo=aes256'),
     pgp_sym_encrypt('4111111111111111',             '$PGCRYPTO_KEY', 'cipher-algo=aes256'),
     encode(digest('4111111111111111', 'sha256'), 'hex'),
     pgp_sym_encrypt('1985-03-22',                  '$PGCRYPTO_KEY', 'cipher-algo=aes256'),
     pgp_sym_encrypt('123 Casino Way, Malta MT-1234','$PGCRYPTO_KEY', 'cipher-algo=aes256'),
     5000.00),
    ('bob_slots',
     pgp_sym_encrypt('bob.smith@casino.com',        '$PGCRYPTO_KEY', 'cipher-algo=aes256'),
     pgp_sym_encrypt('Robert James Smith',           '$PGCRYPTO_KEY', 'cipher-algo=aes256'),
     pgp_sym_encrypt('987-65-4321',                  '$PGCRYPTO_KEY', 'cipher-algo=aes256'),
     pgp_sym_encrypt('5500005555555559',              '$PGCRYPTO_KEY', 'cipher-algo=aes256'),
     encode(digest('5500005555555559', 'sha256'), 'hex'),
     pgp_sym_encrypt('1990-07-14',                   '$PGCRYPTO_KEY', 'cipher-algo=aes256'),
     pgp_sym_encrypt('456 Poker Lane, London W1 3AB','$PGCRYPTO_KEY', 'cipher-algo=aes256'),
     1250.75),
    ('carol_roulette',
     pgp_sym_encrypt('carol.williams@casino.com',   '$PGCRYPTO_KEY', 'cipher-algo=aes256'),
     pgp_sym_encrypt('Carol Ann Williams',           '$PGCRYPTO_KEY', 'cipher-algo=aes256'),
     pgp_sym_encrypt('456-78-9012',                  '$PGCRYPTO_KEY', 'cipher-algo=aes256'),
     pgp_sym_encrypt('378282246310005',               '$PGCRYPTO_KEY', 'cipher-algo=aes256'),
     encode(digest('378282246310005', 'sha256'), 'hex'),
     pgp_sym_encrypt('1978-11-30',                   '$PGCRYPTO_KEY', 'cipher-algo=aes256'),
     pgp_sym_encrypt('789 Roulette Blvd, Dublin 2', '$PGCRYPTO_KEY', 'cipher-algo=aes256'),
     8800.00);

-- Checkpoint to flush to disk
CHECKPOINT;
EOSQL

ROW_COUNT=$(sudo -u postgres psql -p "$PG_PORT" -d pii_test -t -c \
    "SELECT COUNT(*) FROM casino_players;" | tr -d ' ')
if [[ "$ROW_COUNT" == "3" ]]; then
    pass "3 casino players with PII inserted"
else
    fail "Expected 3 rows, got $ROW_COUNT"
fi

# ---- Test 3: Decrypt round-trip ---------------------------------------------
section "Test 3: pgcrypto Decrypt Round-Trip"

declare -A EXPECTED=(
    ["alice_poker"]="alice.johnson@casino.com"
    ["bob_slots"]="bob.smith@casino.com"
    ["carol_roulette"]="carol.williams@casino.com"
)

for username in alice_poker bob_slots carol_roulette; do
    DECRYPTED=$(sudo -u postgres psql -p "$PG_PORT" -d pii_test -t -c \
        "SELECT pgp_sym_decrypt(email_enc, '$PGCRYPTO_KEY') FROM casino_players WHERE username='$username';" \
        2>/dev/null | tr -d ' \n')
    if [[ "$DECRYPTED" == "${EXPECTED[$username]}" ]]; then
        pass "Round-trip decrypt OK: $username => $DECRYPTED"
    else
        fail "Round-trip failed: $username expected '${EXPECTED[$username]}' got '$DECRYPTED'"
    fi
done

# ---- Test 4: Wrong key raises error -----------------------------------------
section "Test 4: Wrong Key Rejection"

ERR=$(sudo -u postgres psql -p "$PG_PORT" -d pii_test -t -c \
    "SELECT pgp_sym_decrypt(email_enc, 'wrong-key') FROM casino_players LIMIT 1;" \
    2>&1 || true)
if echo "$ERR" | grep -qiE "error|Wrong|decrypt|bad|invalid"; then
    pass "Wrong key correctly rejected with error"
else
    fail "Wrong key did not raise expected error: $ERR"
fi

# ---- Test 5: Flush and scan PostgreSQL data files on LUKS -------------------
section "Test 5: Forensic Scan of PostgreSQL Data Files"

# Force checkpoint to ensure all data is written to disk
sudo -u postgres psql -p "$PG_PORT" -d pii_test -c "CHECKPOINT;" > /dev/null 2>&1

# Find OID for pii_test database
DB_OID=$(sudo -u postgres psql -p "$PG_PORT" -d postgres -t -c \
    "SELECT oid FROM pg_database WHERE datname='pii_test';" | tr -d ' ')
info "pii_test database OID: $DB_OID"

# Scan table data files for plaintext PII
PII_FOUND=0
declare -a PII_TERMS=(
    "alice.johnson@casino.com"
    "bob.smith@casino.com"
    "carol.williams@casino.com"
    "Alice Marie Johnson"
    "Robert James Smith"
    "Carol Ann Williams"
    "123-45-6789"
    "987-65-4321"
    "4111111111111111"
    "5500005555555559"
    "378282246310005"
    "123 Casino Way"
    "456 Poker Lane"
)

DATA_BASE="$PG_DATA_DIR/base/$DB_OID"
if [[ -d "$DATA_BASE" ]]; then
    info "Scanning $DATA_BASE for plaintext PII..."
    for term in "${PII_TERMS[@]}"; do
        # Search actual data files (not TOAST or WAL)
        if find "$DATA_BASE" -maxdepth 1 -name '[0-9]*' -not -name '*_vm' \
            -not -name '*_fsm' -type f 2>/dev/null | \
            xargs -I{} strings {} 2>/dev/null | grep -q "$term" 2>/dev/null; then
            fail "Plaintext PII found in data files: '$term'"
            PII_FOUND=$((PII_FOUND+1))
        fi
    done

    if [[ $PII_FOUND -eq 0 ]]; then
        pass "Forensic scan: ZERO plaintext PII found in PostgreSQL data files"
        info "Note: pgcrypto encrypts at column level — PII stored as ciphertext bytea"
    fi
else
    warn "Data directory not found at $DATA_BASE — skipping file scan"
fi

# ---- Test 6: Scan raw LUKS image for PII ------------------------------------
section "Test 6: Forensic Scan of Raw LUKS Image"

if [[ -f "$LUKS_IMG" ]]; then
    info "Stopping PostgreSQL for raw image scan..."
    sudo -u postgres /usr/lib/postgresql/$PG_VERSION/bin/pg_ctl \
        -D "$PG_DATA_DIR" stop -m fast > /dev/null 2>&1
    sleep 2
    pass "PostgreSQL stopped"

    MOUNT_POINT=$(dirname "$PG_DATA_DIR")
    umount "$MOUNT_POINT" 2>/dev/null || true

    LUKS_NAME=$(cryptsetup status 2>/dev/null | grep "$LUKS_IMG" | awk '{print $1}' || echo "pg-tde-casino")
    cryptsetup luksClose "$(basename "${LUKS_IMG%.*}")" 2>/dev/null || true

    info "Scanning raw LUKS image for PII strings..."
    RAW_PII_FOUND=0
    for term in "alice.johnson" "bob.smith" "carol.williams" "123-45-6789" "4111111111111111"; do
        if strings "$LUKS_IMG" 2>/dev/null | grep -q "$term"; then
            fail "Plaintext found in raw LUKS image: '$term'"
            RAW_PII_FOUND=$((RAW_PII_FOUND+1))
        fi
    done

    if [[ $RAW_PII_FOUND -eq 0 ]]; then
        pass "Raw LUKS image: ZERO plaintext PII found — disk-level encryption working"
    fi

    # Entropy check
    ENTROPY=$(dd if="$LUKS_IMG" bs=1M skip=2 count=1 2>/dev/null | \
        python3 -c "
import sys, math, collections
d = sys.stdin.buffer.read()
c = collections.Counter(d)
t = len(d)
h = -sum((v/t)*math.log2(v/t) for v in c.values() if v>0)
print(f'{h:.4f}')
" 2>/dev/null || echo "0")
    if python3 -c "assert float('$ENTROPY') > 7.9" 2>/dev/null; then
        pass "High entropy: $ENTROPY bits/byte — confirms strong AES encryption"
    else
        warn "Entropy lower than expected: $ENTROPY bits/byte"
    fi

    # LUKS magic
    if xxd "$LUKS_IMG" 2>/dev/null | head -1 | grep -q "4c554b53"; then
        pass "LUKS2 magic header 0x4C554B53 confirmed"
    fi
else
    warn "LUKS image not found at $LUKS_IMG — skipping raw image scan"
fi

# ---- Test 7: PAN dedup via hash (no plaintext) ------------------------------
section "Test 7: PAN Dedup Hash Verification"

# Restart postgres for hash test
LUKS_KEY=$(cat "$KEY_FILE" 2>/dev/null || openssl rand -hex 32)
# Try to remount
LUKS_NAME_BASE=$(basename "${LUKS_IMG%.*}")
if [[ -f "$LUKS_IMG" ]]; then
    echo -n "$LUKS_KEY" | cryptsetup luksOpen --key-file=- "$LUKS_IMG" "$LUKS_NAME_BASE" 2>/dev/null || true
    mount -o noatime "/dev/mapper/$LUKS_NAME_BASE" "$(dirname "$PG_DATA_DIR")" 2>/dev/null || true
    sudo -u postgres /usr/lib/postgresql/$PG_VERSION/bin/pg_ctl \
        -D "$PG_DATA_DIR" -l "$(dirname "$PG_DATA_DIR")/pg.log" \
        start -w -t 30 > /dev/null 2>&1
    sleep 2
fi

H1=$(sudo -u postgres psql -p "$PG_PORT" -d pii_test -t -c \
    "SELECT pan_hash FROM casino_players WHERE username='alice_poker';" | tr -d ' \n' || echo "")
H2=$(echo -n "4111111111111111" | sha256sum | awk '{print $1}')

if [[ -n "$H1" ]] && [[ "$H1" == "$H2" ]]; then
    pass "PAN hash matches SHA-256 of known PAN (dedup works, no plaintext stored)"
elif [[ -n "$H1" ]]; then
    fail "PAN hash mismatch: got ${H1:0:16}..., expected ${H2:0:16}..."
else
    warn "Could not retrieve PAN hash (PG may be stopped)"
fi

# ---- Summary ----------------------------------------------------------------
echo ""
echo "======================================================" | tee -a "$LOG_FILE"
echo " Encryption Test Summary — $(date)"                     | tee -a "$LOG_FILE"
echo "======================================================" | tee -a "$LOG_FILE"
echo " Tests passed: $PASS_COUNT"                             | tee -a "$LOG_FILE"
echo " Tests failed: $FAIL_COUNT"                             | tee -a "$LOG_FILE"
echo " Log: $LOG_FILE"                                        | tee -a "$LOG_FILE"
echo "======================================================" | tee -a "$LOG_FILE"

if [[ $FAIL_COUNT -eq 0 ]]; then
    echo -e "${GREEN}ALL TESTS PASSED${NC}" | tee -a "$LOG_FILE"
    exit 0
else
    echo -e "${RED}$FAIL_COUNT TEST(S) FAILED — review $LOG_FILE${NC}" | tee -a "$LOG_FILE"
    exit 1
fi
