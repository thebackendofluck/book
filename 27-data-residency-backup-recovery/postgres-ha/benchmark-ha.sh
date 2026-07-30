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

# pgbench benchmark for PostgreSQL HA cluster
# Reports: TPS, latency, replication lag, comparison vs single-node baseline.
#
# Usage: ./benchmark-ha.sh [--scale N] [--clients N] [--duration N]

set -euo pipefail

HAPROXY_HOST="${HAPROXY_HOST:-localhost}"
PRIMARY_PORT=15000
REPLICA_PORT=15001
DIRECT_PRIMARY_PORT=15432
PGPASSWORD="${POSTGRES_PASSWORD:?set POSTGRES_PASSWORD}"
export PGPASSWORD

# Defaults
SCALE_FACTOR="${SCALE_FACTOR:-50}"
BENCH_CLIENTS="${BENCH_CLIENTS:-32}"
BENCH_THREADS="${BENCH_THREADS:-8}"
BENCH_DURATION="${BENCH_DURATION:-60}"
DB="bench_$(date +%s)"
REPORT="pgbench-results.txt"

while [[ $# -gt 0 ]]; do
    case $1 in
        --scale)    SCALE_FACTOR="$2"; shift 2 ;;
        --clients)  BENCH_CLIENTS="$2"; shift 2 ;;
        --duration) BENCH_DURATION="$2"; shift 2 ;;
        --help|-h)
            echo "Usage: $0 [--scale N] [--clients N] [--duration N]"
            echo "  --scale N      pgbench scale factor (default 50, ~750MB)"
            echo "  --clients N    concurrent clients (default 32)"
            echo "  --duration N   test duration in seconds (default 60)"
            exit 0 ;;
        *) echo "Unknown flag: $1"; exit 1 ;;
    esac
done

RED='\033[0;31m'; GREEN='\033[0;32m'; YELLOW='\033[1;33m'; NC='\033[0m'
info() { echo -e "${YELLOW}[bench]${NC} $*"; }
pass() { echo -e "${GREEN}[OK]${NC} $*"; }

psql_p() { psql -h "$HAPROXY_HOST" -p "$PRIMARY_PORT" -U postgres "$@"; }

# ─── Disk check ───────────────────────────────────────────────────────────
AVAIL_GB=$(df /var/lib/docker 2>/dev/null | awk 'NR==2{printf "%.0f", $4/1024/1024}' \
    || df /tmp | awk 'NR==2{printf "%.0f", $4/1024/1024}')
NEEDED_GB=$(( SCALE_FACTOR * 15 / 100 + 1 ))
if [ "$AVAIL_GB" -lt "$NEEDED_GB" ]; then
    NEW_SCALE=$(( AVAIL_GB * 100 / 15 ))
    info "Auto-reducing scale factor: ${SCALE_FACTOR} → ${NEW_SCALE} (disk: ${AVAIL_GB}GB available)"
    SCALE_FACTOR=$NEW_SCALE
fi

info "Config: scale=${SCALE_FACTOR}, clients=${BENCH_CLIENTS}, threads=${BENCH_THREADS}, duration=${BENCH_DURATION}s"

# ─── Create benchmark database ────────────────────────────────────────────
info "Creating benchmark database: $DB"
psql_p -d postgres -c "CREATE DATABASE ${DB};" &>/dev/null

# ─── pgbench init ─────────────────────────────────────────────────────────
info "pgbench init (scale ${SCALE_FACTOR})..."
INIT_START=$(date +%s)
pgbench -i -s "$SCALE_FACTOR" -h "$HAPROXY_HOST" -p "$PRIMARY_PORT" -U postgres "$DB" 2>&1
INIT_END=$(date +%s)
pass "Init complete in $((INIT_END - INIT_START))s"

# Give replica time to sync
info "Waiting 10s for replica to catch up after init..."
sleep 10

# ─── Benchmark 1: Single-node baseline (direct, no HAProxy) ───────────────
info "=== Benchmark 1: Direct primary (baseline, no HA overhead) ==="
SINGLE_RAW=$(pgbench -c "$BENCH_CLIENTS" -j "$BENCH_THREADS" -T "$BENCH_DURATION" \
    -h "$HAPROXY_HOST" -p "$DIRECT_PRIMARY_PORT" -U postgres "$DB" 2>&1)
echo "$SINGLE_RAW"
SINGLE_TPS=$(echo "$SINGLE_RAW"  | grep -oP 'tps = \K[0-9.]+' | tail -1)
SINGLE_LAT=$(echo "$SINGLE_RAW"  | grep -oP 'latency average = \K[0-9.]+')

# ─── Benchmark 2: HA cluster via HAProxy ──────────────────────────────────
info "=== Benchmark 2: HA cluster via HAProxy port ${PRIMARY_PORT} ==="
HA_RAW=$(pgbench -c "$BENCH_CLIENTS" -j "$BENCH_THREADS" -T "$BENCH_DURATION" -P 10 \
    -h "$HAPROXY_HOST" -p "$PRIMARY_PORT" -U postgres "$DB" 2>&1)
echo "$HA_RAW"
HA_TPS=$(echo "$HA_RAW" | grep -oP 'tps = \K[0-9.]+' | tail -1)
HA_LAT=$(echo "$HA_RAW" | grep -oP 'latency average = \K[0-9.]+')

# ─── Replication lag during benchmark ─────────────────────────────────────
info "=== Replication lag after HA benchmark ==="
LAG=$(psql_p -d postgres -t -A \
    -c "SELECT pg_wal_lsn_diff(pg_current_wal_lsn(), sent_lsn)::text || ' bytes'
        FROM pg_stat_replication LIMIT 1;" 2>/dev/null || echo "replica not connected")
info "Replication lag: $LAG"

# ─── Benchmark 3: Read-only on replica ────────────────────────────────────
info "=== Benchmark 3: Read-only workload via replica port ${REPLICA_PORT} ==="
RO_RAW=$(pgbench -c "$BENCH_CLIENTS" -j "$BENCH_THREADS" -T "$BENCH_DURATION" \
    -S \
    -h "$HAPROXY_HOST" -p "$REPLICA_PORT" -U postgres "$DB" 2>&1)
echo "$RO_RAW"
RO_TPS=$(echo "$RO_RAW" | grep -oP 'tps = \K[0-9.]+' | tail -1)
RO_LAT=$(echo "$RO_RAW" | grep -oP 'latency average = \K[0-9.]+')

# ─── Cleanup ──────────────────────────────────────────────────────────────
psql_p -d postgres -c "DROP DATABASE IF EXISTS ${DB};" &>/dev/null
pass "Benchmark database dropped"

# ─── Write report ─────────────────────────────────────────────────────────
cat > "$REPORT" <<EOF
============================================================
 PostgreSQL HA pgbench Benchmark Results
 Date: $(date)
 Scale factor: ${SCALE_FACTOR}  (~$((SCALE_FACTOR * 15))MB)
 Clients: ${BENCH_CLIENTS} | Threads: ${BENCH_THREADS} | Duration: ${BENCH_DURATION}s
============================================================

Benchmark 1 — Single-node (direct, no HAProxy)
  TPS:              ${SINGLE_TPS:-N/A}
  Avg latency (ms): ${SINGLE_LAT:-N/A}

Benchmark 2 — HA cluster via HAProxy (read-write, synchronous replication)
  TPS:              ${HA_TPS:-N/A}
  Avg latency (ms): ${HA_LAT:-N/A}
  Overhead vs single: $(
    if [[ "${SINGLE_TPS:-0}" != "0" && "${HA_TPS:-0}" != "0" ]]; then
        echo "scale=1; (1 - ${HA_TPS}/${SINGLE_TPS}) * 100" | bc
    else echo "N/A"
    fi
  )%

Benchmark 3 — Replica read-only (SELECT only, via HAProxy)
  TPS:              ${RO_TPS:-N/A}
  Avg latency (ms): ${RO_LAT:-N/A}

Replication lag after write benchmark: ${LAG}

Notes:
- Synchronous replication adds RTT overhead (expected ~5–20% TPS reduction vs single-node).
- Read-only offloading to replica improves aggregate throughput for read-heavy iGaming workloads.
- For higher write TPS in production: use async replication with RPO tolerance,
  or run synchronous replication only for financial transaction tables.
============================================================
EOF

cat "$REPORT"
pass "Results saved to $REPORT"
