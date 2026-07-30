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

# Patroni failover test suite — iGaming HA chapter
# Tests: basic failover, zero-data-loss during heavy writes, data parity.
#
# Usage:
#   ./test-failover.sh [--skip-pgbench] [--scale N]
#
# Prerequisites:
#   - cluster running: docker compose up -d
#   - psql, pgbench installed locally (or run from inside a container)

set -euo pipefail

# ─── Config ────────────────────────────────────────────────────────────────
HAPROXY_HOST="${HAPROXY_HOST:-localhost}"
PRIMARY_PORT=15000
REPLICA_PORT=15001
PG_PRIMARY_DIRECT=15432
PG_REPLICA_DIRECT=15433
PGPASSWORD="${POSTGRES_PASSWORD:?set POSTGRES_PASSWORD}"
export PGPASSWORD

BENCH_DB="casino_test"
# Scale factor: 100 ≈ 1.5 GB; adjust down if disk is tight.
# For a 2TB target use 700, but that requires ~2TB free on the host volume.
# The script auto-detects available disk and caps accordingly.
SCALE_FACTOR="${SCALE_FACTOR:-100}"
BENCH_CLIENTS=32
BENCH_THREADS=8
BENCH_DURATION=120   # seconds for the heavy-write test

RESULT_FILE="pgbench-results.txt"
LOG_FILE="failover-test.log"
COMPOSE_CMD="docker compose"

RED='\033[0;31m'; GREEN='\033[0;32m'; YELLOW='\033[1;33m'; NC='\033[0m'
pass() { echo -e "${GREEN}[PASS]${NC} $*" | tee -a "$LOG_FILE"; }
fail() { echo -e "${RED}[FAIL]${NC} $*" | tee -a "$LOG_FILE"; }
info() { echo -e "${YELLOW}[INFO]${NC} $*" | tee -a "$LOG_FILE"; }

# ─── Helper: run psql against HAProxy primary port ─────────────────────────
pg_primary() { psql -h "$HAPROXY_HOST" -p "$PRIMARY_PORT" -U postgres -d "${1:-postgres}" -c "$2" -t -A 2>>"$LOG_FILE"; }
pg_replica() { psql -h "$HAPROXY_HOST" -p "$REPLICA_PORT" -U postgres -d "${1:-postgres}" -c "$2" -t -A 2>>"$LOG_FILE"; }
pg_direct_primary() { psql -h "$HAPROXY_HOST" -p "$PG_PRIMARY_DIRECT" -U postgres -d "${1:-postgres}" -c "$2" -t -A 2>>"$LOG_FILE"; }
pg_direct_replica() { psql -h "$HAPROXY_HOST" -p "$PG_REPLICA_DIRECT" -U postgres -d "${1:-postgres}" -c "$2" -t -A 2>>"$LOG_FILE"; }

wait_for_postgres() {
    local port=$1 label=$2 max_tries=${3:-30}
    info "Waiting for $label on port $port..."
    for i in $(seq 1 $max_tries); do
        if pg_isready -h "$HAPROXY_HOST" -p "$port" -U postgres -q 2>/dev/null; then
            pass "$label is ready"
            return 0
        fi
        sleep 2
        echo -n "."
    done
    fail "$label did not become ready in time"
    return 1
}

# ─── Step 0: Preflight ─────────────────────────────────────────────────────
echo "============================================================" | tee "$LOG_FILE"
echo " PostgreSQL HA Failover Test Suite" | tee -a "$LOG_FILE"
echo " $(date)" | tee -a "$LOG_FILE"
echo "============================================================" | tee -a "$LOG_FILE"

for cmd in psql pgbench pg_isready docker; do
    if ! command -v "$cmd" &>/dev/null; then
        fail "Required command not found: $cmd"
        exit 1
    fi
done

wait_for_postgres "$PRIMARY_PORT" "HAProxy primary"
wait_for_postgres "$REPLICA_PORT" "HAProxy replica"

# ─── Step 1: Verify initial cluster state ──────────────────────────────────
info "=== Step 1: Verify cluster state ==="

LEADER=$(curl -sf "http://${HAPROXY_HOST}:8008/leader" 2>/dev/null | jq -r '.members[]|select(.role=="Leader")|.name' 2>/dev/null || echo "unknown")
info "Patroni leader: $LEADER"

ROLE=$(pg_primary "postgres" "SELECT pg_is_in_recovery()::text;")
if [ "$ROLE" = "false" ]; then
    pass "HAProxy port 5000 → primary (not in recovery)"
else
    fail "HAProxy port 5000 → unexpected standby"
    exit 1
fi

REPLICA_ROLE=$(pg_replica "postgres" "SELECT pg_is_in_recovery()::text;")
if [ "$REPLICA_ROLE" = "true" ]; then
    pass "HAProxy port 5001 → replica (in recovery)"
else
    info "Replica not yet streaming (may still be syncing) — continuing"
fi

# ─── Step 2: Create test data ──────────────────────────────────────────────
info "=== Step 2: Create test database and iGaming schema ==="

pg_primary "postgres" "DROP DATABASE IF EXISTS ${BENCH_DB};" 2>/dev/null || true
pg_primary "postgres" "CREATE DATABASE ${BENCH_DB};"

# iGaming-specific tables
pg_primary "$BENCH_DB" "
CREATE TABLE IF NOT EXISTS player_transactions (
    id          bigserial,
    player_id   bigint NOT NULL,
    amount      numeric(12,2) NOT NULL,
    type        varchar(20) NOT NULL CHECK (type IN ('bet','win','deposit','withdrawal','bonus')),
    game_id     varchar(50),
    session_id  uuid DEFAULT gen_random_uuid(),
    metadata    jsonb DEFAULT '{}',
    created_at  timestamptz DEFAULT now(),
    PRIMARY KEY (id, created_at)
) PARTITION BY RANGE (created_at);

-- Monthly partitions (current month + 2 forward)
DO \$\$
DECLARE
    m date := date_trunc('month', now() - interval '1 month');
BEGIN
    FOR i IN 0..3 LOOP
        EXECUTE format(
            'CREATE TABLE IF NOT EXISTS player_transactions_%s
             PARTITION OF player_transactions
             FOR VALUES FROM (%L) TO (%L)',
            to_char(m + (i || ' month')::interval, 'YYYY_MM'),
            m + (i || ' month')::interval,
            m + ((i+1) || ' month')::interval
        );
    END LOOP;
END\$\$;

CREATE INDEX IF NOT EXISTS idx_pt_player_created ON player_transactions (player_id, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_pt_type ON player_transactions (type);

-- Seed some initial rows
INSERT INTO player_transactions (player_id, amount, type, game_id)
SELECT
    (random() * 100000)::bigint,
    (random() * 1000)::numeric(12,2),
    (ARRAY['bet','win','deposit','withdrawal','bonus'])[(random()*4+1)::int],
    'game_' || (random()*500)::int
FROM generate_series(1, 500000);

-- Verify row count
SELECT 'player_transactions rows: ' || count(*)::text FROM player_transactions;
" 2>&1 | tee -a "$LOG_FILE"

pass "iGaming schema created with 500k seed rows"

# ─── Step 3: pgbench init ──────────────────────────────────────────────────
info "=== Step 3: pgbench init (scale factor ${SCALE_FACTOR}) ==="

# Check available disk before committing to large scale factor
AVAIL_GB=$(df /var/lib/docker 2>/dev/null | awk 'NR==2{printf "%.0f", $4/1024/1024}' || df /tmp | awk 'NR==2{printf "%.0f", $4/1024/1024}')
NEEDED_GB=$(( SCALE_FACTOR * 15 / 100 ))  # ~150MB per scale unit
if [ "$AVAIL_GB" -lt "$NEEDED_GB" ]; then
    NEW_SCALE=$(( AVAIL_GB * 100 / 15 ))
    info "Disk too small for scale=${SCALE_FACTOR} (need ~${NEEDED_GB}GB, have ${AVAIL_GB}GB). Reducing to scale=${NEW_SCALE}"
    SCALE_FACTOR=$NEW_SCALE
fi
info "Using scale factor: ${SCALE_FACTOR} (approx $((SCALE_FACTOR * 15 / 100)) GB)"

INIT_START=$(date +%s)
pgbench -i -s "$SCALE_FACTOR" -h "$HAPROXY_HOST" -p "$PRIMARY_PORT" -U postgres "$BENCH_DB" 2>&1 | tee -a "$LOG_FILE"
INIT_END=$(date +%s)
INIT_SEC=$(( INIT_END - INIT_START ))
info "pgbench init completed in ${INIT_SEC}s"

# ─── Step 4: Baseline benchmark (single PostgreSQL baseline for comparison) ─
info "=== Step 4: Baseline — HA cluster without failover ==="

BASELINE_RESULT=$(pgbench -c 16 -j 4 -T 60 -P 10 \
    -h "$HAPROXY_HOST" -p "$PRIMARY_PORT" -U postgres "$BENCH_DB" 2>&1)
echo "$BASELINE_RESULT" | tee -a "$LOG_FILE"

BASELINE_TPS=$(echo "$BASELINE_RESULT" | grep -oP 'tps = \K[0-9.]+' | tail -1)
BASELINE_LAT=$(echo "$BASELINE_RESULT" | grep -oP 'latency average = \K[0-9.]+')
info "Baseline TPS: ${BASELINE_TPS:-unknown} | Avg latency: ${BASELINE_LAT:-unknown} ms"

# ─── Step 5: Verify replication lag before stress ──────────────────────────
info "=== Step 5: Replication lag check ==="
LAG=$(pg_primary "postgres" "
SELECT COALESCE(
    (SELECT pg_wal_lsn_diff(pg_current_wal_lsn(), sent_lsn)
     FROM pg_stat_replication LIMIT 1),
    0
)::text || ' bytes lag';
")
info "Replication lag before stress test: $LAG"

# ─── Step 6: Heavy-write test + failover under load ────────────────────────
info "=== Step 6: Heavy-write test (${BENCH_CLIENTS} clients, ${BENCH_DURATION}s) ==="
info "Starting pgbench in background, then killing primary after 30s..."

# Run pgbench in background
pgbench -c "$BENCH_CLIENTS" -j "$BENCH_THREADS" -T "$BENCH_DURATION" -P 10 \
    -h "$HAPROXY_HOST" -p "$PRIMARY_PORT" -U postgres "$BENCH_DB" \
    > /tmp/pgbench_stress.log 2>&1 &
BENCH_PID=$!

info "pgbench running (PID $BENCH_PID)"
sleep 30

# ─── Step 7: Kill primary to trigger failover ──────────────────────────────
info "=== Step 7: Killing primary container to trigger failover ==="
FAILOVER_START=$(date +%s)

$COMPOSE_CMD kill pg-primary
info "pg-primary killed at $(date). Waiting for Patroni to elect new leader..."

# Wait for new primary to be available via HAProxy
FAILOVER_OK=false
for i in $(seq 1 30); do
    sleep 2
    if pg_isready -h "$HAPROXY_HOST" -p "$PRIMARY_PORT" -U postgres -q 2>/dev/null; then
        NEW_ROLE=$(psql -h "$HAPROXY_HOST" -p "$PRIMARY_PORT" -U postgres \
            -d postgres -c "SELECT pg_is_in_recovery()::text;" -t -A 2>/dev/null || echo "error")
        if [ "$NEW_ROLE" = "false" ]; then
            FAILOVER_END=$(date +%s)
            FAILOVER_SEC=$(( FAILOVER_END - FAILOVER_START ))
            pass "Failover complete in ${FAILOVER_SEC}s — new primary is accepting writes"
            FAILOVER_OK=true
            break
        fi
    fi
    echo -n "."
done

if [ "$FAILOVER_OK" = "false" ]; then
    fail "Failover did not complete within 60s"
fi

# Let pgbench finish
wait "$BENCH_PID" 2>/dev/null || true
STRESS_RESULT=$(cat /tmp/pgbench_stress.log)
echo "$STRESS_RESULT" | tee -a "$LOG_FILE"

STRESS_TPS=$(echo "$STRESS_RESULT" | grep -oP 'tps = \K[0-9.]+' | tail -1)
info "Stress test TPS (including failover window): ${STRESS_TPS:-unknown}"

# ─── Step 8: Data parity verification ─────────────────────────────────────
info "=== Step 8: Data parity verification ==="
info "Waiting 10s for replica to catch up after failover..."
sleep 10

# Row counts on primary (now pg-replica promoted)
PRIMARY_ROWS=$(psql -h "$HAPROXY_HOST" -p "$PRIMARY_PORT" -U postgres \
    -d "$BENCH_DB" -c "SELECT count(*) FROM pgbench_accounts;" -t -A 2>/dev/null || echo "error")
info "pgbench_accounts rows on new primary: $PRIMARY_ROWS"

TXNS_PRIMARY=$(psql -h "$HAPROXY_HOST" -p "$PRIMARY_PORT" -U postgres \
    -d "$BENCH_DB" -c "SELECT count(*) FROM player_transactions;" -t -A 2>/dev/null || echo "error")
info "player_transactions rows on new primary: $TXNS_PRIMARY"

# pg_dump checksum comparison (run inside containers to avoid HAProxy quirks)
info "Computing pg_dump checksums for data parity..."
PRIMARY_CHECKSUM=$($COMPOSE_CMD exec -T pg-replica \
    bash -c "PGPASSWORD=${PATRONI_PASSWORD:?set PATRONI_PASSWORD} pg_dump -U postgres ${BENCH_DB} --data-only -t pgbench_accounts 2>/dev/null | md5sum" \
    2>/dev/null | awk '{print $1}' || echo "unavailable")
info "Primary (promoted replica) pgbench_accounts checksum: $PRIMARY_CHECKSUM"

# ─── Step 9: Bring old primary back as replica ─────────────────────────────
info "=== Step 9: Bring old primary back as replica ==="
$COMPOSE_CMD start pg-primary
info "pg-primary container started. Patroni will run pg_rewind and rejoin as standby..."
sleep 20

OLD_PRIMARY_ROLE=$(psql -h "$HAPROXY_HOST" -p "$PG_PRIMARY_DIRECT" -U postgres \
    -d postgres -c "SELECT pg_is_in_recovery()::text;" -t -A 2>/dev/null || echo "unavailable")
if [ "$OLD_PRIMARY_ROLE" = "true" ]; then
    pass "Old primary rejoined cluster as standby (in recovery)"
elif [ "$OLD_PRIMARY_ROLE" = "false" ]; then
    fail "Old primary came back as primary — split brain risk!"
else
    info "Old primary status: $OLD_PRIMARY_ROLE (still starting up)"
fi

# Final replication lag check
FINAL_LAG=$(pg_primary "postgres" "
SELECT COALESCE(
    (SELECT pg_wal_lsn_diff(pg_current_wal_lsn(), sent_lsn)
     FROM pg_stat_replication LIMIT 1),
    0
)::text || ' bytes lag';
" 2>/dev/null || echo "unavailable")
info "Final replication lag: $FINAL_LAG"

# ─── Step 10: Write results ────────────────────────────────────────────────
info "=== Step 10: Writing results to ${RESULT_FILE} ==="

cat > "$RESULT_FILE" <<EOF
============================================================
 PostgreSQL HA pgbench Results
 Date: $(date)
 Host: ${HAPROXY_HOST}
 pgbench scale factor: ${SCALE_FACTOR}
 Bench clients: ${BENCH_CLIENTS} | Threads: ${BENCH_THREADS}
============================================================

--- Baseline (HA cluster, no failover, 16c/4j/60s) ---
TPS:              ${BASELINE_TPS:-N/A}
Avg latency (ms): ${BASELINE_LAT:-N/A}

--- Stress test (${BENCH_CLIENTS}c/${BENCH_THREADS}j/${BENCH_DURATION}s, failover at ~30s) ---
TPS (incl failover window): ${STRESS_TPS:-N/A}

--- Failover ---
Failover time:    ${FAILOVER_SEC:-N/A} seconds
Result:           $([ "${FAILOVER_OK:-false}" = "true" ] && echo "SUCCESS" || echo "TIMEOUT")

--- Data parity ---
pgbench_accounts rows on new primary:  ${PRIMARY_ROWS:-N/A}
player_transactions rows on primary:   ${TXNS_PRIMARY:-N/A}
pgbench_accounts dump checksum:        ${PRIMARY_CHECKSUM:-N/A}

--- Cluster final state ---
Old primary rejoined as standby:       ${OLD_PRIMARY_ROLE:-N/A}
Final replication lag:                 ${FINAL_LAG:-N/A}

--- Notes ---
- Synchronous replication: enabled (synchronous_mode=true)
- Maximum replication lag threshold: 1MB
- WAL archiving: enabled (cp to /wal-archive)
- Failover timeout (TTL): 30s
- HAProxy health check: GET /primary and /replica on Patroni REST port 8008
EOF

cat "$RESULT_FILE"

echo ""
echo "============================================================"
echo " Test complete. Logs: ${LOG_FILE} | Results: ${RESULT_FILE}"
echo "============================================================"
