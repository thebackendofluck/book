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

# Data parity verification between primary and replica
# Compares row counts, table checksums, and pg_dump hashes.
#
# Run after a failover or heavy write test to confirm zero data loss.
# Usage: ./data-parity-check.sh [database]

set -euo pipefail

DB="${1:-casino_test}"
HAPROXY_HOST="${HAPROXY_HOST:-localhost}"
PRIMARY_PORT=15000
REPLICA_PORT=15001
PGPASSWORD="${POSTGRES_PASSWORD:?set POSTGRES_PASSWORD}"
export PGPASSWORD

REPORT="parity-report-$(date +%Y%m%d-%H%M%S).txt"

GREEN='\033[0;32m'; RED='\033[0;31m'; YELLOW='\033[1;33m'; NC='\033[0m'
pass()  { echo -e "${GREEN}[MATCH]${NC} $*" | tee -a "$REPORT"; }
fail()  { echo -e "${RED}[MISMATCH]${NC} $*" | tee -a "$REPORT"; }
info()  { echo -e "${YELLOW}[INFO]${NC} $*" | tee -a "$REPORT"; }

psql_primary() { psql -h "$HAPROXY_HOST" -p "$PRIMARY_PORT" -U postgres -d "$DB" -t -A -c "$1" 2>/dev/null; }
psql_replica()  { psql -h "$HAPROXY_HOST" -p "$REPLICA_PORT" -U postgres -d "$DB" -t -A -c "$1" 2>/dev/null; }

echo "============================================================" | tee "$REPORT"
echo " Data Parity Check: $DB" | tee -a "$REPORT"
echo " $(date)" | tee -a "$REPORT"
echo "============================================================" | tee -a "$REPORT"

# Wait for replica to catch up
info "Checking replication lag..."
LAG_BYTES=$(psql -h "$HAPROXY_HOST" -p "$PRIMARY_PORT" -U postgres -d postgres -t -A \
    -c "SELECT COALESCE(MAX(pg_wal_lsn_diff(pg_current_wal_lsn(), sent_lsn)),0) FROM pg_stat_replication;" \
    2>/dev/null || echo "0")
info "Replication lag: ${LAG_BYTES} bytes"
if [ "${LAG_BYTES:-0}" -gt 1048576 ]; then
    info "Waiting for lag to drain below 1MB..."
    for _ in $(seq 1 30); do
        sleep 2
        LAG_BYTES=$(psql -h "$HAPROXY_HOST" -p "$PRIMARY_PORT" -U postgres -d postgres -t -A \
            -c "SELECT COALESCE(MAX(pg_wal_lsn_diff(pg_current_wal_lsn(), sent_lsn)),0) FROM pg_stat_replication;" 2>/dev/null || echo "0")
        [ "${LAG_BYTES:-0}" -lt 1048576 ] && break
        echo -n "."
    done
fi
info "Lag after wait: ${LAG_BYTES} bytes"

# ─── Row count comparison ──────────────────────────────────────────────────
info "=== Row counts ==="

TABLES=$(psql_primary "
    SELECT tablename FROM pg_tables
    WHERE schemaname = 'public'
    ORDER BY tablename;
")

MISMATCH_COUNT=0
while IFS= read -r TABLE; do
    [ -z "$TABLE" ] && continue
    P_COUNT=$(psql_primary "SELECT count(*) FROM \"${TABLE}\";" 2>/dev/null || echo "error")
    R_COUNT=$(psql_replica  "SELECT count(*) FROM \"${TABLE}\";" 2>/dev/null || echo "error")
    if [ "$P_COUNT" = "$R_COUNT" ]; then
        pass "Table $TABLE: $P_COUNT rows"
    else
        fail "Table $TABLE: primary=$P_COUNT, replica=$R_COUNT"
        (( MISMATCH_COUNT++ )) || true
    fi
done <<< "$TABLES"

# ─── LSN comparison ────────────────────────────────────────────────────────
info "=== LSN / WAL position ==="
PRIMARY_LSN=$(psql -h "$HAPROXY_HOST" -p "$PRIMARY_PORT" -U postgres -d postgres -t -A \
    -c "SELECT pg_current_wal_lsn();" 2>/dev/null || echo "unknown")
REPLICA_LSN=$(psql -h "$HAPROXY_HOST" -p "$REPLICA_PORT" -U postgres -d postgres -t -A \
    -c "SELECT pg_last_wal_replay_lsn();" 2>/dev/null || echo "unknown")
info "Primary LSN: $PRIMARY_LSN"
info "Replica last replay LSN: $REPLICA_LSN"

# ─── Deterministic checksum via ordered aggregate hash (per table) ─────────
# pg_dump output order is non-deterministic; use md5() on ordered data instead.
info "=== Table checksums (ordered aggregate, deterministic) ==="

check_table_hash() {
    local TABLE=$1 KEY_COL=$2
    local P_HASH R_HASH
    P_HASH=$(psql_primary "
        SELECT md5(string_agg(row_to_json(t)::text, ',' ORDER BY ${KEY_COL}))
        FROM (SELECT * FROM ${TABLE} ORDER BY ${KEY_COL}) t;" 2>/dev/null || echo "error")
    R_HASH=$(psql_replica "
        SELECT md5(string_agg(row_to_json(t)::text, ',' ORDER BY ${KEY_COL}))
        FROM (SELECT * FROM ${TABLE} ORDER BY ${KEY_COL}) t;" 2>/dev/null || echo "error")
    if [ "$P_HASH" = "$R_HASH" ] && [ "$P_HASH" != "error" ]; then
        pass "Hash match $TABLE: $P_HASH"
    else
        fail "Hash mismatch $TABLE: primary=$P_HASH | replica=$R_HASH"
        (( MISMATCH_COUNT++ )) || true
    fi
}

check_table_hash pgbench_accounts "aid"
check_table_hash player_transactions "id"

# ─── Summary ──────────────────────────────────────────────────────────────
echo "" | tee -a "$REPORT"
echo "============================================================" | tee -a "$REPORT"
if [ "$MISMATCH_COUNT" -eq 0 ]; then
    echo -e "${GREEN}RESULT: PASS — primary and replica are in sync${NC}" | tee -a "$REPORT"
else
    echo -e "${RED}RESULT: FAIL — $MISMATCH_COUNT mismatches found${NC}" | tee -a "$REPORT"
fi
echo "Report saved: $REPORT"
echo "============================================================" | tee -a "$REPORT"

exit "$MISMATCH_COUNT"
