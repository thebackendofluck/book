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

# benchmark-encryption.sh
# Compares pgbench performance: LUKS-encrypted vs unencrypted volume.
# Reports TPS, latency overhead, and pgcrypto column encryption cost.
#
# Usage:
#   sudo ./benchmark-encryption.sh \
#        [--enc-port 5499] [--plain-port 5434] \
#        [--scale 100] [--duration 60] \
#        [--output /tmp/benchmark-results.txt]

set -euo pipefail

# ---- defaults ---------------------------------------------------------------
ENC_PORT="${ENC_PORT:-5499}"
PLAIN_PORT="${PLAIN_PORT:-5434}"
PG_VERSION="${PG_VERSION:-16}"
BENCH_SCALE="${BENCH_SCALE:-100}"     # scale factor (100 = ~15MB base)
BENCH_DURATION="${BENCH_DURATION:-60}" # seconds per run
BENCH_CLIENTS="${BENCH_CLIENTS:-8}"
BENCH_JOBS="${BENCH_JOBS:-4}"
PGCRYPTO_KEY="${PGCRYPTO_KEY:-casino-tde-pgcrypto-2024}"
OUTPUT_FILE="${OUTPUT_FILE:-/tmp/benchmark-results.txt}"
LOG_FILE="/tmp/benchmark-encryption.log"

RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m'

info()    { echo -e "${BLUE}[INFO]${NC} $1" | tee -a "$LOG_FILE"; }
pass()    { echo -e "${GREEN}[PASS]${NC} $1" | tee -a "$LOG_FILE"; }
warn()    { echo -e "${YELLOW}[WARN]${NC} $1" | tee -a "$LOG_FILE"; }
section() { echo -e "\n${BLUE}=== $1 ===${NC}" | tee -a "$LOG_FILE"; }

while [[ $# -gt 0 ]]; do
    case "$1" in
        --enc-port)    ENC_PORT="$2"; shift 2 ;;
        --plain-port)  PLAIN_PORT="$2"; shift 2 ;;
        --scale)       BENCH_SCALE="$2"; shift 2 ;;
        --duration)    BENCH_DURATION="$2"; shift 2 ;;
        --clients)     BENCH_CLIENTS="$2"; shift 2 ;;
        --jobs)        BENCH_JOBS="$2"; shift 2 ;;
        --output)      OUTPUT_FILE="$2"; shift 2 ;;
        --help|-h)
            echo "Usage: $0 [--enc-port PORT] [--plain-port PORT] [--scale N] [--duration SEC] [--output FILE]"
            exit 0 ;;
        *) echo "Unknown option: $1"; exit 1 ;;
    esac
done

if [[ $EUID -ne 0 ]]; then
    exec sudo bash "$0" "$@"
fi

echo "=== Benchmark — $(date) ===" > "$LOG_FILE"
echo "=== PostgreSQL Encryption Benchmark ===" > "$OUTPUT_FILE"
echo "Date: $(date)" >> "$OUTPUT_FILE"
echo "Host: $(hostname)" >> "$OUTPUT_FILE"
echo "CPU: $(grep 'model name' /proc/cpuinfo | head -1 | cut -d: -f2 | xargs)" >> "$OUTPUT_FILE"
echo "AES-NI: $(grep -c aes /proc/cpuinfo) cores" >> "$OUTPUT_FILE"
echo "RAM: $(free -h | grep Mem | awk '{print $2}')" >> "$OUTPUT_FILE"
echo "Scale: $BENCH_SCALE | Duration: ${BENCH_DURATION}s | Clients: $BENCH_CLIENTS | Jobs: $BENCH_JOBS" >> "$OUTPUT_FILE"
echo "" >> "$OUTPUT_FILE"

# Helper: check PG is running on a port
check_pg() {
    local port=$1
    sudo -u postgres psql -p "$port" -d postgres -c "SELECT 1;" > /dev/null 2>&1
}

# ============================================================
# PART 1: pgbench — encrypted vs unencrypted volume
# ============================================================
section "Part 1: pgbench on Encrypted vs Unencrypted Volume"

run_pgbench() {
    local port=$1
    local label=$2
    local db="bench_${label}"

    info "Initializing pgbench (scale=$BENCH_SCALE) on $label (port $port)..."
    sudo -u postgres psql -p "$port" -d postgres -c "DROP DATABASE IF EXISTS $db;" > /dev/null 2>&1
    sudo -u postgres psql -p "$port" -d postgres -c "CREATE DATABASE $db;" > /dev/null 2>&1

    sudo -u postgres pgbench \
        -p "$port" \
        -i -s "$BENCH_SCALE" \
        "$db" 2>&1 | grep -E "done|creating|vacuuming" | tee -a "$LOG_FILE"

    # Warm-up run (30s, discarded)
    info "Warming up $label..."
    sudo -u postgres pgbench \
        -p "$port" \
        -T 30 \
        -c "$BENCH_CLIENTS" \
        -j "$BENCH_JOBS" \
        "$db" > /dev/null 2>&1

    # Benchmark run
    info "Benchmarking $label for ${BENCH_DURATION}s..."
    local result
    result=$(sudo -u postgres pgbench \
        -p "$port" \
        -T "$BENCH_DURATION" \
        -c "$BENCH_CLIENTS" \
        -j "$BENCH_JOBS" \
        -r \
        "$db" 2>&1)

    echo "$result" | tee -a "$LOG_FILE"

    # Extract key metrics
    local tps lat_avg lat_stddev
    tps=$(echo "$result"       | grep "^tps"     | head -1 | grep -oP '[0-9]+\.[0-9]+' | head -1)
    lat_avg=$(echo "$result"   | grep "latency"  | grep -oP '[0-9]+\.[0-9]+' | head -1)
    lat_stddev=$(echo "$result"| grep "stddev"   | grep -oP '[0-9]+\.[0-9]+' | head -1)

    echo "$tps $lat_avg $lat_stddev"
}

ENC_METRICS="" PLAIN_METRICS=""

if check_pg "$PLAIN_PORT"; then
    PLAIN_METRICS=$(run_pgbench "$PLAIN_PORT" "plain")
    PLAIN_TPS=$(echo "$PLAIN_METRICS" | awk '{print $1}')
    PLAIN_LAT=$(echo "$PLAIN_METRICS" | awk '{print $2}')
    pass "Unencrypted baseline: ${PLAIN_TPS} TPS, ${PLAIN_LAT}ms avg latency"
else
    warn "PostgreSQL not running on port $PLAIN_PORT — skipping unencrypted benchmark"
    PLAIN_TPS=0
    PLAIN_LAT=0
fi

if check_pg "$ENC_PORT"; then
    ENC_METRICS=$(run_pgbench "$ENC_PORT" "enc")
    ENC_TPS=$(echo "$ENC_METRICS" | awk '{print $1}')
    ENC_LAT=$(echo "$ENC_METRICS" | awk '{print $2}')
    pass "LUKS-encrypted: ${ENC_TPS} TPS, ${ENC_LAT}ms avg latency"
else
    warn "PostgreSQL not running on port $ENC_PORT — skipping encrypted benchmark"
    ENC_TPS=0
    ENC_LAT=0
fi

# Calculate overhead
if [[ "$PLAIN_TPS" != "0" ]] && [[ "$ENC_TPS" != "0" ]]; then
    TPS_OVERHEAD=$(python3 -c "
plain=${PLAIN_TPS}; enc=${ENC_TPS}
if plain > 0:
    overhead = (plain - enc) / plain * 100
    print(f'{overhead:.1f}')
else:
    print('N/A')
" 2>/dev/null || echo "N/A")
    LAT_OVERHEAD=$(python3 -c "
plain=${PLAIN_LAT}; enc=${ENC_LAT}
if plain > 0:
    overhead = (enc - plain) / plain * 100
    print(f'{overhead:.1f}')
else:
    print('N/A')
" 2>/dev/null || echo "N/A")
fi

# ============================================================
# PART 2: pgcrypto Column Encryption Overhead
# ============================================================
section "Part 2: pgcrypto Column Encryption Overhead"

PGCRYPTO_PORT="${ENC_PORT}"
if ! check_pg "$PGCRYPTO_PORT"; then
    PGCRYPTO_PORT="${PLAIN_PORT}"
fi

if check_pg "$PGCRYPTO_PORT"; then
    sudo -u postgres psql -p "$PGCRYPTO_PORT" -d postgres \
        -c "DROP DATABASE IF EXISTS pgcrypto_bench;" > /dev/null 2>&1
    sudo -u postgres psql -p "$PGCRYPTO_PORT" -d postgres \
        -c "CREATE DATABASE pgcrypto_bench;" > /dev/null 2>&1

    sudo -u postgres psql -p "$PGCRYPTO_PORT" -d pgcrypto_bench > /dev/null 2>&1 <<EOSQL
CREATE EXTENSION IF NOT EXISTS pgcrypto;

CREATE TABLE bench_plain (
    id SERIAL PRIMARY KEY,
    email TEXT,
    full_name TEXT,
    ssn TEXT
);

CREATE TABLE bench_encrypted (
    id SERIAL PRIMARY KEY,
    email_enc BYTEA,
    full_name_enc BYTEA,
    ssn_enc BYTEA
);

INSERT INTO bench_plain (email, full_name, ssn)
SELECT
    'user' || g || '@casino.com',
    'Player Number ' || g,
    lpad(g::text, 9, '0')
FROM generate_series(1, 10000) g;

INSERT INTO bench_encrypted (email_enc, full_name_enc, ssn_enc)
SELECT
    pgp_sym_encrypt('user' || g || '@casino.com',    '$PGCRYPTO_KEY', 'cipher-algo=aes256'),
    pgp_sym_encrypt('Player Number ' || g,            '$PGCRYPTO_KEY', 'cipher-algo=aes256'),
    pgp_sym_encrypt(lpad(g::text, 9, '0'),            '$PGCRYPTO_KEY', 'cipher-algo=aes256')
FROM generate_series(1, 10000) g;
EOSQL

    # Benchmark plain read
    PLAIN_READ_TIME=$(sudo -u postgres psql -p "$PGCRYPTO_PORT" -d pgcrypto_bench -t -c "
\timing on
EXPLAIN ANALYZE SELECT email, full_name, ssn FROM bench_plain WHERE id <= 5000;" 2>/dev/null | \
        grep "Execution Time" | grep -oP '[0-9]+\.[0-9]+' | head -1)

    # Benchmark encrypted read (with decrypt)
    ENC_READ_TIME=$(sudo -u postgres psql -p "$PGCRYPTO_PORT" -d pgcrypto_bench -t -c "
\timing on
EXPLAIN ANALYZE SELECT
    pgp_sym_decrypt(email_enc,     '$PGCRYPTO_KEY'),
    pgp_sym_decrypt(full_name_enc, '$PGCRYPTO_KEY'),
    pgp_sym_decrypt(ssn_enc,       '$PGCRYPTO_KEY')
FROM bench_encrypted WHERE id <= 5000;" 2>/dev/null | \
        grep "Execution Time" | grep -oP '[0-9]+\.[0-9]+' | head -1)

    # Bulk encrypt benchmark
    BULK_ENC_TIME=$(sudo -u postgres psql -p "$PGCRYPTO_PORT" -d pgcrypto_bench -t -c "
\timing on
EXPLAIN ANALYZE
INSERT INTO bench_encrypted (email_enc, full_name_enc, ssn_enc)
SELECT
    pgp_sym_encrypt('test' || g || '@bench.com', '$PGCRYPTO_KEY', 'cipher-algo=aes256'),
    pgp_sym_encrypt('Bench User ' || g,           '$PGCRYPTO_KEY', 'cipher-algo=aes256'),
    pgp_sym_encrypt(lpad(g::text, 9, '0'),        '$PGCRYPTO_KEY', 'cipher-algo=aes256')
FROM generate_series(10001, 11000) g;" 2>/dev/null | \
        grep "Execution Time" | grep -oP '[0-9]+\.[0-9]+' | head -1)

    pass "pgcrypto benchmarks complete"
    info "Plain SELECT 5000 rows:    ${PLAIN_READ_TIME:-N/A}ms"
    info "Encrypted SELECT 5000 rows: ${ENC_READ_TIME:-N/A}ms"
    info "Bulk INSERT 1000 encrypted: ${BULK_ENC_TIME:-N/A}ms"
fi

# ============================================================
# Write results file
# ============================================================
section "Writing Results"

cat >> "$OUTPUT_FILE" <<RESULTS
=== PART 1: Disk-Level LUKS Encryption Overhead ===
pgbench scale factor:   $BENCH_SCALE
Test duration:          ${BENCH_DURATION}s
Concurrent clients:     $BENCH_CLIENTS
Parallel jobs:          $BENCH_JOBS

Unencrypted volume:
  TPS:               ${PLAIN_TPS:-N/A}
  Avg latency:       ${PLAIN_LAT:-N/A}ms

LUKS-encrypted volume (AES-XTS-512):
  TPS:               ${ENC_TPS:-N/A}
  Avg latency:       ${ENC_LAT:-N/A}ms

LUKS Overhead:
  TPS reduction:     ${TPS_OVERHEAD:-N/A}%
  Latency increase:  ${LAT_OVERHEAD:-N/A}%

Note: AES-XTS with AES-NI hardware typically adds <3% overhead.
Values >10% indicate missing AES-NI or storage I/O bottleneck.

=== PART 2: pgcrypto Column-Level Encryption Overhead ===
Dataset:            10,000 rows with 3 encrypted columns each

Plain SELECT 5000 rows:      ${PLAIN_READ_TIME:-N/A}ms
Encrypted SELECT 5000 rows:  ${ENC_READ_TIME:-N/A}ms
Bulk INSERT 1000 encrypted:  ${BULK_ENC_TIME:-N/A}ms

pgcrypto Overhead per row (decrypt):
$(python3 -c "
plain = float('${PLAIN_READ_TIME:-0}') if '${PLAIN_READ_TIME:-0}' != 'N/A' else 0
enc   = float('${ENC_READ_TIME:-0}')   if '${ENC_READ_TIME:-0}'   != 'N/A' else 0
if plain > 0 and enc > 0:
    overhead_ms = enc - plain
    overhead_pct = (enc - plain) / plain * 100
    per_row_us = overhead_ms / 5000 * 1000
    print(f'  Total overhead: {overhead_ms:.1f}ms ({overhead_pct:.1f}%)')
    print(f'  Per-row overhead: {per_row_us:.2f}µs')
else:
    print('  N/A — one or both benchmarks could not run')
" 2>/dev/null || echo "  N/A")

Note: pgcrypto (OpenPGP) has higher overhead than native column encryption.
For production use AES-256 CBC via pgp_sym_encrypt with cipher-algo=aes256.
Consider pg_tde (table-level) for lower per-row overhead.

=== RECOMMENDATION ===
LUKS disk encryption:        USE — minimal overhead with AES-NI, strong data-at-rest protection
pgcrypto column encryption:  USE for PCI-DSS PAN/PII — accept ~2-5x query overhead on encrypted columns
pg_tde (PostgreSQL 17+):     PREFER when available — transparent with <2% overhead

RESULTS

pass "Results written to $OUTPUT_FILE"
info "Full log: $LOG_FILE"
echo ""
cat "$OUTPUT_FILE"
