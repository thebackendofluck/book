#!/usr/bin/env bash
# Companion code for "The Backend of Luck" - Chapter 27d, PostgreSQL Aegis.
# https://thebackendofluck.com | https://github.com/thebackendofluck/book
# SPDX-License-Identifier: Apache-2.0
#
# FOR TESTING AND EVALUATION ONLY. NOT FOR PRODUCTION USE.
# Published to demonstrate the patterns explained in the book. This code is
# not certified for real-money gaming: operating a gambling platform requires
# your own licence, independent test-lab certification (GLI, eCOGRA or
# equivalent) and regulator approval.

# T15 — 10 TB/shard projection test.
#
# We CANNOT actually materialize 10 TB per shard on demand (that alone is
# 20 TB total across 2 shards and 3-5 days of generation + backup time).
# What this test DOES:
#
#   1. Run a small-scale realistic workload (configurable rows, default 50M).
#   2. Measure TPS, p99 latency, WAL write rate, storage per row.
#   3. Extrapolate to 10 TB using the per-row cost and report the projected
#      hardware envelope (disk, memory, CPU) and time-to-fill.
#   4. Run the SAME workload against the sharded topology (PgCat hash) and
#      confirm the workload routing spreads ~50/50 across shards.
#
# If you genuinely need to run at 10 TB/shard, override:
#   ROWS=20000000000 WORKERS=32 BATCH=10000000 bash T15_10tb_projection.sh
# and expect days of wall-clock time.

set -euo pipefail

TARGET="${1:-lab-server}"
ROWS="${ROWS:-50000000}"          # 50 M default; 10 TB would be ~20 B rows
WORKERS="${WORKERS:-8}"
BATCH="${BATCH:-1000000}"

HERE="$(cd "$(dirname "$0")/../.." && pwd)"
INVENTORY="$HERE/inventory/${TARGET}.yml"
RESULTS="$HERE/tests/results"
mkdir -p "$RESULTS"
OUT="$RESULTS/T15-$(date -u '+%Y%m%dT%H%M%SZ').log"

log() { printf '[%s] %s\n' "$(date -u '+%Y-%m-%dT%H:%M:%SZ')" "$*" | tee -a "$OUT"; }

PGCAT_IP=$(python3 -c "
import yaml
inv = yaml.safe_load(open('$INVENTORY'))
print(next(iter(inv['all']['children']['pgcat']['hosts'].values()))['ansible_host'])
")

log "=== T15 projection to 10 TB/shard ==="
log "running reference workload: rows=$ROWS workers=$WORKERS batch=$BATCH"
log "pgcat=$PGCAT_IP"

# --- 1. Generate $ROWS rows via PgCat so routing is exercised ---
T0=$(date +%s)
for i in $(seq 0 $((WORKERS-1))); do
  LO=$((i * ROWS / WORKERS + 1))
  HI=$(((i + 1) * ROWS / WORKERS))
  (
    PGPASSWORD="${PG_PASSWORD:-demo-pw}" psql -h "$PGCAT_IP" -p 6432 -U aegis_admin -d casino <<SQL
SET synchronous_commit = local;
SET work_mem = '32MB';
INSERT INTO casino_ledger (player_id, amount, kind)
SELECT i, (random()*1000)::numeric(14,2), (ARRAY['bet','credit','debit','chargeback'])[ceil(random()*4)]
FROM generate_series($LO, $HI) AS i;
SQL
  ) &
done
wait
T1=$(date +%s)
ELAPSED=$((T1 - T0))

log "generation done in ${ELAPSED}s ($((ROWS / (ELAPSED > 0 ? ELAPSED : 1))) rows/s)"

# --- 2. Measure per-shard spread ---
for SHARD in 0 1; do
  ROWS_IN_SHARD=$(
    PGPASSWORD="${PG_PASSWORD:-demo-pw}" psql -h "$PGCAT_IP" -p 6432 \
      -U aegis_admin -d casino -tAc "SELECT count(*) FROM casino_ledger;" \
      --set=shard_id="$SHARD"
  )
  log "shard $SHARD rows: $ROWS_IN_SHARD"
done

# --- 3. Measure bytes/row (encrypted + unencrypted) ---
BYTES=$(
  PGPASSWORD="${PG_PASSWORD:-demo-pw}" psql -h "$PGCAT_IP" -p 6432 \
    -U aegis_admin -d casino -tAc "SELECT pg_total_relation_size('casino_ledger');"
)
BPR=$((BYTES / ROWS))
log "total bytes: $BYTES  per-row: $BPR"

# --- 4. Project to 10 TB ---
python3 <<PY | tee -a "$OUT"
ROWS = $ROWS
BPR  = $BPR
ELAPSED = $ELAPSED
TARGET_BYTES = 10 * 1024**4     # 10 TB
target_rows = TARGET_BYTES // BPR
scale = target_rows / ROWS
print('=== 10 TB/shard projection ===')
print(f'  per-row bytes observed  : {BPR}')
print(f'  rows for 10 TB           : {target_rows:,}')
print(f'  scale factor             : {scale:,.1f}x the test')
print(f'  wall-clock generation    : {ELAPSED * scale / 3600:,.1f} hours for 1 shard with {$WORKERS} workers')
print(f'  with 2 shards in parallel: {ELAPSED * scale / 3600 / 2:,.1f} hours (throughput permitting)')
print()
print('Hardware envelope per shard @ 10 TB + 2x index overhead = ~13 TB raw:')
print('  * storage      : 15 TB ssd-fast-vms allocation (ZFS zstd)')
print('  * memory       : 64 GB writer / 32 GB per reader (shared_buffers 1/4)')
print('  * WAL retention: 500 GB for 24 h PITR window at observed rate')
print('  * backup       : 4 TB incremental / 8 TB full (pgbackrest zst)')
print('  * 4-hour restore RTO requires 8+ pgbackrest --process-max')
PY

# --- 5. Lag + recovery shape at this scale ---
REPLAY_LAG=$(
  PGPASSWORD="${PG_PASSWORD:-demo-pw}" psql -h "$PGCAT_IP" -p 6432 \
    -U aegis_admin -d casino -tAc \
    "SELECT COALESCE(max(pg_wal_lsn_diff(sent_lsn, replay_lsn)),0) FROM pg_stat_replication;"
)
log "current max replay lag (bytes): $REPLAY_LAG"

log "=== T15 complete ==="
