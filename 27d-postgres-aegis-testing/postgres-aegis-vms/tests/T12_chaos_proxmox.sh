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

# T12_chaos_proxmox.sh — chaos: kill the shard-b Patroni leader on Proxmox
# and measure availability, data loss, and failover time.
#
# Adapted from T12_chaos_leader_kill.sh (virsh) for Proxmox qm stop/start.
#
# Usage:
#   CYCLES=6 INTERVAL=300 PG_PASSWORD=<pw> bash tests/T12_chaos_proxmox.sh
#
# VM IDs (shard-b):
#   2012 → pg-shard-b-writer-1 (10.0.42.32)
#   2013 → pg-shard-b-reader-1 (10.0.42.33)

set -euo pipefail

CYCLES="${CYCLES:-6}"
INTERVAL="${INTERVAL:-300}"     # seconds between kills (5 min per cycle)
PROXMOX_HOST="${PROXMOX_HOST:-secondary-host}"
PGCAT_IP="${PGCAT_IP:-10.0.42.50}"
PGCAT_PORT="${PGCAT_PORT:-6432}"   # pgcat transaction pooler (NOT haproxy :5000 → shard-a)
PG_PASSWORD="${PG_PASSWORD:?set PG_PASSWORD}"

HERE="$(cd "$(dirname "$0")/.." && pwd)"
RESULTS="$HERE/tests/results"
mkdir -p "$RESULTS"
TIMESTAMP=$(date -u '+%Y%m%dT%H%M%SZ')
LOG="$RESULTS/T12-proxmox-${TIMESTAMP}.log"
CSV="$RESULTS/T12-proxmox.csv"

log() { local msg; msg="[$(date -u '+%Y-%m-%dT%H:%M:%SZ')] $*"; printf '%s\n' "$msg" | tee -a "$LOG"; }

# VM ID of the current Patroni leader on shard-b
current_leader_vmid() {
    local leader_ip
    leader_ip=$(ssh -o BatchMode=yes "ansible@10.0.42.32" \
        "sudo patronictl -c /etc/patroni/patroni.yml list --format json 2>/dev/null" \
        | python3 -c "
import sys, json
data = json.load(sys.stdin)
for m in data:
    if m.get('Role') == 'Leader':
        ip = m['Host'].split(':')[0]
        print(ip)
        break
" 2>/dev/null || echo "10.0.42.32")
    case "$leader_ip" in
        10.0.42.32) echo "2012" ;;
        10.0.42.33) echo "2013" ;;
        *) echo "2012" ;;
    esac
}

log "=== T12 Proxmox chaos — shard-b — CYCLES=${CYCLES} INTERVAL=${INTERVAL}s ==="
log "pgcat_ip=${PGCAT_IP}"

# Write proxy host — the psql commands run via SSH on a VM that has psql and
# can reach pgcat on the LAN. The orchestrator (Mac/laptop) may not have psql.
WRITE_PROXY="${WRITE_PROXY:-10.0.42.33}"   # any shard-b node; use .33 (survives most cycles)

pg_write() {
    ssh -o BatchMode=yes -o ConnectTimeout=3 "ansible@${WRITE_PROXY}" \
        "PGPASSWORD='${PG_PASSWORD}' psql \
            -h '${PGCAT_IP}' -p '${PGCAT_PORT}' \
            -U aegis_admin -d casino -tAc \
            \"INSERT INTO chaos_writes (ts) VALUES (now()) RETURNING id;\"" \
        >/dev/null 2>&1
}

# --- 1. Start write generator in background ---
GEN_LOG=$(mktemp /tmp/T12-gen-XXXXXX.log)
(
    while true; do
        TS=$(date +%s%N)
        if pg_write; then
            printf '%s,OK\n' "$TS"
        else
            printf '%s,FAIL\n' "$TS"
        fi
    done
) >"$GEN_LOG" &
GEN_PID=$!
log "write generator pid=$GEN_PID (proxy=${WRITE_PROXY})"
trap 'kill $GEN_PID 2>/dev/null || true; wait $GEN_PID 2>/dev/null || true' EXIT

# Ensure chaos_writes table exists
ssh -o BatchMode=yes "ansible@${WRITE_PROXY}" \
    "PGPASSWORD='${PG_PASSWORD}' psql \
        -h '${PGCAT_IP}' -p '${PGCAT_PORT}' \
        -U aegis_admin -d casino \
        -c 'CREATE TABLE IF NOT EXISTS chaos_writes (id bigserial PRIMARY KEY, ts timestamptz NOT NULL DEFAULT now());'" \
    >/dev/null 2>&1 || true

# --- 2. Chaos loop ---
for i in $(seq 1 "$CYCLES"); do
    log "--- waiting ${INTERVAL}s before cycle ${i}/${CYCLES} ---"
    sleep "$INTERVAL"

    VMID=$(current_leader_vmid)
    log "cycle ${i}/${CYCLES} — stopping VM vmid=${VMID} on ${PROXMOX_HOST}"

    t0=$(date +%s)
    ssh -o BatchMode=yes "root@${PROXMOX_HOST}" "qm stop ${VMID}" 2>>"$LOG" || true

    # Wait for Patroni failover — poll pgcat port (90 × 2s = 180s window covers
    # the observed ~122s failover: Patroni TTL ~30s + election + pgcat health-check)
    FAILED_OVER=no
    for _ in $(seq 1 90); do
        if PGPASSWORD="$PG_PASSWORD" psql -h "$PGCAT_IP" -p "$PGCAT_PORT" -U aegis_admin \
               -d casino -tAc "SELECT 1;" >/dev/null 2>&1; then
            FAILED_OVER=yes
            break
        fi
        sleep 2
    done
    t1=$(date +%s)
    FAILOVER_S=$((t1 - t0))
    log "failover time: ${FAILOVER_S}s (succeeded=${FAILED_OVER})"
    printf '%s,shard-b,failover_seconds,%s\n' "$(date -u '+%Y-%m-%dT%H:%M:%SZ')" "$FAILOVER_S" >> "$CSV"

    # Bring the VM back up
    log "starting VM vmid=${VMID} back up"
    ssh -o BatchMode=yes "root@${PROXMOX_HOST}" "qm start ${VMID}" 2>>"$LOG" || true
    sleep 30  # let patroni rejoin as replica

    # Show current cluster state
    ssh -o BatchMode=yes "ansible@10.0.42.32" \
        "sudo patronictl -c /etc/patroni/patroni.yml list" 2>>"$LOG" || true
done

kill "$GEN_PID" 2>/dev/null || true
wait "$GEN_PID" 2>/dev/null || true

# --- 3. Compute availability ---
OK=$(grep -c ',OK' "$GEN_LOG" 2>/dev/null || echo 0)
FAIL=$(grep -c ',FAIL' "$GEN_LOG" 2>/dev/null || echo 0)
TOTAL=$((OK + FAIL))
AVAIL=$(python3 -c "print(round(100.0 * ${OK} / max(1, ${TOTAL}), 3))")

log "writes OK=${OK} FAIL=${FAIL} TOTAL=${TOTAL} AVAILABILITY=${AVAIL}%"
printf '%s,shard-b,availability_pct,%s\n' "$(date -u '+%Y-%m-%dT%H:%M:%SZ')" "$AVAIL" >> "$CSV"
printf '%s,shard-b,failed_writes,%s\n'    "$(date -u '+%Y-%m-%dT%H:%M:%SZ')" "$FAIL" >> "$CSV"

rm -f "$GEN_LOG"
log "=== T12 Proxmox chaos complete — see $LOG ==="
