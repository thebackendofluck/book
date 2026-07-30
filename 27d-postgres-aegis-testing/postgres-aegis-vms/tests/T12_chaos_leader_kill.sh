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

# T12 — chaos: kill the Patroni leader every N minutes and measure
# availability, data loss, and failover time.

set -euo pipefail

TARGET="${1:-lab-server}"
CYCLES="${CYCLES:-6}"
INTERVAL="${INTERVAL:-600}"   # 10 min between kills
SHARD="${SHARD:-shard-a}"

HERE="$(cd "$(dirname "$0")/.." && pwd)"
INVENTORY="$HERE/inventory/${TARGET}.yml"
RESULTS="$HERE/tests/results"
mkdir -p "$RESULTS"

log() { printf '[%s] %s\n' "$(date -u '+%Y-%m-%dT%H:%M:%SZ')" "$*"; }

# Drive a constant INSERT workload through HAProxy writer
PGCAT_IP=$(python3 -c "
import yaml
inv = yaml.safe_load(open('$INVENTORY'))
print(next(iter(inv['all']['children']['pgcat']['hosts'].values()))['ansible_host'])
")
log "pgcat/haproxy endpoint: $PGCAT_IP"

# --- 1. Start write generator in background ---
GEN_LOG=$(mktemp)
(
  while true; do
    T=$(date +%s%N)
    if PGPASSWORD="${PG_PASSWORD:?set PG_PASSWORD}" psql -h "$PGCAT_IP" -p 5000 -U aegis_admin -d casino \
         -c "INSERT INTO chaos_writes (ts) VALUES (now()) RETURNING id;" >/dev/null 2>&1; then
      echo "$T,OK"
    else
      echo "$T,FAIL"
    fi
  done
) >"$GEN_LOG" &
GEN_PID=$!
log "write generator pid=$GEN_PID"

trap 'kill $GEN_PID 2>/dev/null || true' EXIT

# --- 2. Chaos loop ---
for i in $(seq 1 "$CYCLES"); do
  sleep "$INTERVAL"
  CURRENT_LEADER=$(curl -sf "http://$PGCAT_IP:7000/" | grep -oE 'writer_[a-z]+_[a-z]+_[a-z0-9-]+' | head -1 || echo "unknown")
  log "cycle $i/$CYCLES — current leader: $CURRENT_LEADER"

  # Pick the real VM name (pg-shard-a-writer-X)
  LEADER_VM=$(python3 - "$INVENTORY" "$SHARD" <<'PY'
import sys, yaml
inv = yaml.safe_load(open(sys.argv[1]))
g = f"{sys.argv[2].replace('-', '_')}_writer"
print(next(iter(inv['all']['children'][g]['hosts'].keys())))
PY
)
  t0=$(date +%s)
  log "virsh destroy $LEADER_VM"
  # shellcheck disable=SC2029  # LEADER_VM intentionally expands on client before ssh
  ssh lab-server "virsh -c qemu:///system destroy '$LEADER_VM'" || true

  # Wait for failover
  for _ in $(seq 1 60); do
    # Check if a replica promoted (Patroni REST /master returns 200)
    if curl -sf -o /dev/null "http://$PGCAT_IP:5000/" 2>/dev/null; then break; fi
    sleep 2
  done
  t1=$(date +%s)
  log "failover time: $((t1 - t0)) s"
  echo "$(date -u '+%Y-%m-%dT%H:%M:%SZ'),$TARGET,failover_seconds,$((t1 - t0))" >> "$RESULTS/T12.csv"

  # Bring killed leader back
  # shellcheck disable=SC2029  # LEADER_VM intentionally expands on client before ssh
  ssh lab-server "virsh -c qemu:///system start '$LEADER_VM'" || true
done

kill "$GEN_PID" 2>/dev/null || true
wait "$GEN_PID" 2>/dev/null || true

# --- 3. Compute availability ---
OK=$(grep -c ',OK' "$GEN_LOG" || true)
FAIL=$(grep -c ',FAIL' "$GEN_LOG" || true)
TOTAL=$((OK + FAIL))
AVAIL=$(python3 -c "print(round(100.0 * $OK / max(1, $TOTAL), 3))")
log "writes OK=$OK FAIL=$FAIL TOTAL=$TOTAL AVAILABILITY=${AVAIL}%"
echo "$(date -u '+%Y-%m-%dT%H:%M:%SZ'),$TARGET,availability_pct,$AVAIL" >> "$RESULTS/T12.csv"
echo "$(date -u '+%Y-%m-%dT%H:%M:%SZ'),$TARGET,failed_writes,$FAIL" >> "$RESULTS/T12.csv"

rm -f "$GEN_LOG"
log "T12 complete"
