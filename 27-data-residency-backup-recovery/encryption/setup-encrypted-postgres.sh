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

# setup-encrypted-postgres.sh
# One-shot script: creates LUKS volume, initializes PostgreSQL on it,
# enables pgcrypto, and creates sample encrypted casino tables.
#
# Usage:
#   sudo ./setup-encrypted-postgres.sh [--size 10G] [--mount /mnt/pg-encrypted] \
#        [--port 5499] [--key-file /tmp/tde-master.key] [--image /nvme-0-zfs/pg-enc.img]

set -euo pipefail

# ---- defaults ---------------------------------------------------------------
VOLUME_SIZE_MB="${VOLUME_SIZE_MB:-10240}"     # 10GB default
MOUNT_POINT="${MOUNT_POINT:-/mnt/pg-encrypted}"
PG_PORT="${PG_PORT:-5499}"
PG_VERSION="${PG_VERSION:-16}"
LUKS_NAME="${LUKS_NAME:-pg-encrypted}"
LUKS_IMG="${LUKS_IMG:-/tmp/pg-encrypted.img}"
KEY_FILE="${KEY_FILE:-/tmp/tde-master.key}"
PGCRYPTO_ENC_KEY="${PGCRYPTO_ENC_KEY:-casino-tde-pgcrypto-2024}"
LOG_FILE="/tmp/setup-encrypted-postgres.log"

LUKS_MAPPER="/dev/mapper/$LUKS_NAME"
PG_DATA_DIR="$MOUNT_POINT/pgdata"

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

# ---- argument parsing -------------------------------------------------------
while [[ $# -gt 0 ]]; do
    case "$1" in
        --size)
            # Accept "10G", "50G", or raw MB integer
            ARG="$2"
            if [[ "$ARG" =~ ^([0-9]+)[Gg]$ ]]; then
                VOLUME_SIZE_MB=$(( ${BASH_REMATCH[1]} * 1024 ))
            elif [[ "$ARG" =~ ^([0-9]+)[Mm]$ ]]; then
                VOLUME_SIZE_MB="${BASH_REMATCH[1]}"
            else
                VOLUME_SIZE_MB="$ARG"
            fi
            shift 2 ;;
        --mount)        MOUNT_POINT="$2"; PG_DATA_DIR="$MOUNT_POINT/pgdata"; shift 2 ;;
        --port)         PG_PORT="$2"; shift 2 ;;
        --key-file)     KEY_FILE="$2"; shift 2 ;;
        --image)        LUKS_IMG="$2"; shift 2 ;;
        --pg-version)   PG_VERSION="$2"; shift 2 ;;
        --cleanup)
            sudo -u postgres /usr/lib/postgresql/$PG_VERSION/bin/pg_ctl \
                -D "$PG_DATA_DIR" stop -m fast 2>/dev/null || true
            sleep 1
            umount "$MOUNT_POINT" 2>/dev/null || true
            cryptsetup luksClose "$LUKS_NAME" 2>/dev/null || true
            rm -f "$LUKS_IMG"
            rm -rf "$MOUNT_POINT"
            echo "Cleanup done."
            exit 0 ;;
        --help|-h)
            cat <<HELP
Usage: $0 [OPTIONS]

Options:
  --size SIZE          Volume size (e.g. 10G, 50G, or MB integer). Default: 10G
  --mount PATH         Mount point for encrypted volume. Default: /mnt/pg-encrypted
  --port PORT          PostgreSQL port. Default: 5499
  --key-file PATH      Path to LUKS key file (hex). Default: /tmp/tde-master.key
  --image PATH         Path for LUKS image file. Default: /tmp/pg-encrypted.img
  --pg-version VER     PostgreSQL version. Default: 16
  --cleanup            Tear down and remove all created resources

Examples:
  sudo $0 --size 10G --mount /mnt/pg-enc --port 5500
  sudo $0 --image /nvme-0-zfs/pg-tde.img --size 50G
HELP
            exit 0 ;;
        *) echo "Unknown option: $1"; exit 1 ;;
    esac
done

if [[ $EUID -ne 0 ]]; then
    echo "Re-running with sudo..."
    exec sudo bash "$0" "$@"
fi

echo "=== Encrypted PostgreSQL Setup — $(date) ===" > "$LOG_FILE"
info "Volume: ${VOLUME_SIZE_MB}MB at $LUKS_IMG"
info "Mount:  $MOUNT_POINT"
info "PG:     $PG_VERSION on port $PG_PORT"

# ---- Step 1: Prerequisites --------------------------------------------------
section "Step 1: Prerequisites"

for tool in cryptsetup mkfs.ext4 dd; do
    command -v "$tool" &>/dev/null && pass "Available: $tool" || fail "Missing: $tool"
done

if [[ ! -f "/usr/lib/postgresql/$PG_VERSION/bin/initdb" ]]; then
    fail "PostgreSQL $PG_VERSION not installed at /usr/lib/postgresql/$PG_VERSION/bin/"
fi
pass "PostgreSQL $PG_VERSION binaries found"

# ---- Step 2: Get or generate LUKS passphrase --------------------------------
section "Step 2: LUKS Passphrase"

if [[ -f "$KEY_FILE" ]] && [[ $(wc -c < "$KEY_FILE") -eq 64 ]]; then
    LUKS_KEY=$(cat "$KEY_FILE")
    pass "Using existing key from $KEY_FILE"
else
    warn "Key file not found or invalid, generating new random key"
    LUKS_KEY=$(openssl rand -hex 32)
    echo "$LUKS_KEY" > "$KEY_FILE"
    chmod 600 "$KEY_FILE"
    pass "Generated new 256-bit key, saved to $KEY_FILE"
fi

# ---- Step 3: Create LUKS volume ---------------------------------------------
section "Step 3: LUKS Volume Creation"

# Tear down if exists
cryptsetup luksClose "$LUKS_NAME" 2>/dev/null || true
rm -f "$LUKS_IMG"

info "Creating ${VOLUME_SIZE_MB}MB image..."
dd if=/dev/zero of="$LUKS_IMG" bs=1M count="$VOLUME_SIZE_MB" status=progress 2>&1 | \
    tail -2 | tee -a "$LOG_FILE"
pass "Image created: $LUKS_IMG (${VOLUME_SIZE_MB}MB)"

info "Formatting with LUKS2 (AES-XTS-512)..."
echo -n "$LUKS_KEY" | cryptsetup luksFormat \
    --type luks2 \
    --cipher aes-xts-plain64 \
    --key-size 512 \
    --hash sha512 \
    --pbkdf pbkdf2 \
    --pbkdf-force-iterations 300000 \
    --batch-mode \
    --key-file=- \
    "$LUKS_IMG"
pass "LUKS2 formatted (AES-XTS-512, SHA-512, PBKDF2 300k iterations)"

info "LUKS header:"
cryptsetup luksDump "$LUKS_IMG" 2>/dev/null | \
    grep -E "Version:|Cipher|Key size|Hash spec" | sed 's/^/  /' | tee -a "$LOG_FILE"

echo -n "$LUKS_KEY" | cryptsetup luksOpen --key-file=- "$LUKS_IMG" "$LUKS_NAME"
pass "LUKS volume opened: $LUKS_MAPPER"

mkfs.ext4 -q -b 4096 "$LUKS_MAPPER"
pass "ext4 filesystem created"

mkdir -p "$MOUNT_POINT"
mount -o noatime "$LUKS_MAPPER" "$MOUNT_POINT"
pass "Mounted at $MOUNT_POINT"

AVAIL_MB=$(df -m "$MOUNT_POINT" | tail -1 | awk '{print $4}')
pass "Available on encrypted volume: ${AVAIL_MB}MB"

# ---- Step 4: Initialize PostgreSQL ------------------------------------------
section "Step 4: PostgreSQL Initialization"

mkdir -p "$PG_DATA_DIR"
chown -R postgres:postgres "$MOUNT_POINT"

sudo -u postgres /usr/lib/postgresql/$PG_VERSION/bin/initdb \
    -D "$PG_DATA_DIR" \
    -E UTF8 \
    --locale=en_US.UTF-8 \
    -A trust \
    > /dev/null 2>&1
pass "PostgreSQL cluster initialized on LUKS volume"

cat >> "$PG_DATA_DIR/postgresql.conf" <<EOF

# Encrypted PostgreSQL instance
port = $PG_PORT
listen_addresses = 'localhost'
logging_collector = off
shared_buffers = '256MB'
effective_cache_size = '1GB'
work_mem = '16MB'
maintenance_work_mem = '128MB'
max_connections = 100
EOF

sudo -u postgres /usr/lib/postgresql/$PG_VERSION/bin/pg_ctl \
    -D "$PG_DATA_DIR" \
    -l "$MOUNT_POINT/pg.log" \
    start -w -t 30 > /dev/null 2>&1
sleep 2

if sudo -u postgres /usr/lib/postgresql/$PG_VERSION/bin/pg_ctl \
    -D "$PG_DATA_DIR" status > /dev/null 2>&1; then
    pass "PostgreSQL running on LUKS volume, port $PG_PORT"
else
    fail "PostgreSQL failed to start — see $MOUNT_POINT/pg.log"
fi

# ---- Step 5: Enable pgcrypto and create encrypted schema --------------------
section "Step 5: pgcrypto Extension + Encrypted Schema"

sudo -u postgres psql -p "$PG_PORT" -d postgres -c "CREATE DATABASE casino;" > /dev/null 2>&1
sudo -u postgres psql -p "$PG_PORT" -d casino > /dev/null 2>&1 <<EOSQL
CREATE EXTENSION IF NOT EXISTS pgcrypto;

CREATE SCHEMA IF NOT EXISTS crypto;

-- Helper functions
CREATE OR REPLACE FUNCTION crypto.encrypt_pii(plaintext TEXT, enc_key TEXT)
RETURNS BYTEA LANGUAGE sql IMMUTABLE STRICT AS \$\$
    SELECT pgp_sym_encrypt(plaintext, enc_key, 'compress-algo=1, cipher-algo=aes256')::BYTEA;
\$\$;

CREATE OR REPLACE FUNCTION crypto.decrypt_pii(ciphertext BYTEA, enc_key TEXT)
RETURNS TEXT LANGUAGE sql IMMUTABLE STRICT AS \$\$
    SELECT pgp_sym_decrypt(ciphertext, enc_key);
\$\$;

-- Casino players table with column-level encryption
CREATE TABLE crypto.players (
    id              SERIAL PRIMARY KEY,
    username        VARCHAR(50)  NOT NULL UNIQUE,
    email_enc       BYTEA,
    full_name_enc   BYTEA,
    ssn_enc         BYTEA,
    pan_hash        TEXT,
    pan_enc         BYTEA,
    balance         NUMERIC(12,2) DEFAULT 0.00,
    jurisdiction    VARCHAR(10),
    created_at      TIMESTAMPTZ DEFAULT now()
);

CREATE TABLE crypto.transactions (
    id          SERIAL PRIMARY KEY,
    player_id   INTEGER REFERENCES crypto.players(id),
    amount      NUMERIC(12,2),
    txn_type    VARCHAR(20),
    ref_enc     BYTEA,
    created_at  TIMESTAMPTZ DEFAULT now()
);

-- Insert sample encrypted data
INSERT INTO crypto.players
    (username, email_enc, full_name_enc, ssn_enc, pan_hash, pan_enc, balance, jurisdiction)
VALUES
    ('alice_poker',
     crypto.encrypt_pii('alice@example.com',  '$PGCRYPTO_ENC_KEY'),
     crypto.encrypt_pii('Alice Johnson',       '$PGCRYPTO_ENC_KEY'),
     crypto.encrypt_pii('123-45-6789',         '$PGCRYPTO_ENC_KEY'),
     encode(digest('4111111111111111', 'sha256'), 'hex'),
     crypto.encrypt_pii('4111111111111111',    '$PGCRYPTO_ENC_KEY'),
     5000.00, 'MT'),
    ('bob_slots',
     crypto.encrypt_pii('bob@example.com',     '$PGCRYPTO_ENC_KEY'),
     crypto.encrypt_pii('Robert Smith',        '$PGCRYPTO_ENC_KEY'),
     crypto.encrypt_pii('987-65-4321',         '$PGCRYPTO_ENC_KEY'),
     encode(digest('5500005555555559', 'sha256'), 'hex'),
     crypto.encrypt_pii('5500005555555559',    '$PGCRYPTO_ENC_KEY'),
     1250.75, 'GB'),
    ('carol_roulette',
     crypto.encrypt_pii('carol@example.com',   '$PGCRYPTO_ENC_KEY'),
     crypto.encrypt_pii('Carol Williams',      '$PGCRYPTO_ENC_KEY'),
     crypto.encrypt_pii('456-78-9012',         '$PGCRYPTO_ENC_KEY'),
     encode(digest('378282246310005', 'sha256'), 'hex'),
     crypto.encrypt_pii('378282246310005',     '$PGCRYPTO_ENC_KEY'),
     8800.00, 'MGA');

EOSQL
pass "pgcrypto enabled, encrypted schema created, sample data inserted"

# Verify round-trip decryption
DECRYPTED=$(sudo -u postgres psql -p "$PG_PORT" -d casino -t -c \
    "SELECT crypto.decrypt_pii(email_enc, '$PGCRYPTO_ENC_KEY') FROM crypto.players WHERE username='alice_poker';" | \
    tr -d ' \n')
if [[ "$DECRYPTED" == "alice@example.com" ]]; then
    pass "pgcrypto round-trip: alice's email decrypts correctly"
else
    fail "pgcrypto round-trip failed: got '$DECRYPTED'"
fi

# ---- Summary ----------------------------------------------------------------
echo ""
echo "======================================================"
echo " Encrypted PostgreSQL Setup Complete"
echo "======================================================"
echo " Volume:       $LUKS_IMG (${VOLUME_SIZE_MB}MB, AES-XTS-512)"
echo " Mount:        $MOUNT_POINT"
echo " PG Data:      $PG_DATA_DIR"
echo " Port:         $PG_PORT"
echo " Database:     casino"
echo " Schema:       crypto"
echo " LUKS key:     $KEY_FILE"
echo " Log:          $LOG_FILE"
echo "======================================================"
echo ""
echo "Connect:"
echo "  psql -h localhost -p $PG_PORT -U postgres -d casino"
echo ""
echo "Decrypt example:"
echo "  SELECT crypto.decrypt_pii(email_enc, 'KEY') FROM crypto.players;"
echo ""
echo "Cleanup:"
echo "  sudo $0 --cleanup"
