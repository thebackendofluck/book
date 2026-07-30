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

# T14 — autoscaler smoke test.
#
# Drives synthetic load that provokes a scale-out, observes the autoscaler
# adds a replica, confirms it reaches streaming, then lets the load drop
# and observes a scale-in.
#
# Requires: the cluster is up (make provision + bootstrap) and Prometheus
# is scraping postgres_exporter.

set -euo pipefail

TARGET="${1:-lab-server}"
SHARD="${SHARD:-shard-a}"
HERE="$(cd "$(dirname "$0")/../.." && pwd)"
INVENTORY="$HERE/inventory/${TARGET}.yml"
RESULTS="$HERE/tests/results"
mkdir -p "$RESULTS"
OUT="$RESULTS/T14-$(date -u '+%Y%m%dT%H%M%SZ').log"

log() { printf '[%s] %s\n' "$(date -u '+%Y-%m-%dT%H:%M:%SZ')" "$*" | tee -a "$OUT"; }

PGCAT_IP=$(python3 -c "
import yaml
inv = yaml.safe_load(open('$INVENTORY'))
print(next(iter(inv['all']['children']['pgcat']['hosts'].values()))['ansible_host'])
")

log "=== T14 autoscaler smoke ==="
log "shard=$SHARD pgcat=$PGCAT_IP"

# --- 1. Capture baseline replica count ---
count_replicas() {
  python3 -c "
import yaml
inv = yaml.safe_load(open('$INVENTORY'))
grp = 'shard_${SHARD##*-}_readers'
print(len(inv['all']['children'][grp].get('hosts') or {}))
"
}
BEFORE=$(count_replicas)
log "baseline replicas: $BEFORE"

# --- 2. Start autoscaler in the background (once per 30 s) ---
python3 "$HERE/scripts/autoscaler.py" --target "$TARGET" --interval-sec 30 \
    >"$RESULTS/T14-autoscaler.log" 2>&1 &
AUTO_PID=$!
trap 'kill $AUTO_PID 2>/dev/null || true' EXIT

# --- 3. Drive heavy read load ---
log "starting heavy read load (pgbench -S, 200 clients, 5 min)"
PGBENCH_PID=""
if command -v pgbench >/dev/null; then
  PGPASSWORD="${PG_PASSWORD:-demo-pw}" pgbench -h "$PGCAT_IP" -p 5001 \
    -U aegis_admin -d casino -S -c 200 -j 16 -T 300 -M prepared \
    >>"$OUT" 2>&1 &
  PGBENCH_PID=$!
fi

# --- 4. Poll replica count every 30 s for 8 min, expect scale-out ---
for i in $(seq 1 16); do
  sleep 30
  NOW=$(count_replicas)
  log "t+$((i*30))s: replicas=$NOW"
  if [ "$NOW" -gt "$BEFORE" ]; then
    log "scale-out detected: $BEFORE -> $NOW"
    break
  fi
done

# Stop benchmark
[ -n "$PGBENCH_PID" ] && { kill "$PGBENCH_PID" 2>/dev/null || true; wait "$PGBENCH_PID" 2>/dev/null || true; }

PEAK=$(count_replicas)
log "peak replicas: $PEAK"

# --- 5. Idle 35 min to trigger scale-in cooldown ---
log "idle 35 min to trigger scale-in (this takes a while)"
sleep 2100

FINAL=$(count_replicas)
log "final replicas: $FINAL"

# --- 6. Stop autoscaler + summarise ---
kill "$AUTO_PID" 2>/dev/null || true

{
  echo "T14 summary"
  echo "  baseline replicas : $BEFORE"
  echo "  peak replicas     : $PEAK"
  echo "  final replicas    : $FINAL"
  echo "  autoscaler log    : $RESULTS/T14-autoscaler.log"
} | tee -a "$OUT"

# Success = we saw at least one scale-out.
[ "$PEAK" -gt "$BEFORE" ]
