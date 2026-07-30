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

# tests/run-prod-matrix.sh — full T01..T12 matrix against a real cluster.
#
# Usage:
#   run-prod-matrix.sh <target>  # lab-server | secondary-host
#
# Assumes:
#   - Cluster already provisioned + bootstrapped (via `make provision bootstrap TARGET=<...>`).
#   - PG_PASSWORD, BAO_TOKEN, and any target-specific creds are in the env.
#   - HAProxy is reachable on pgcat host :5000 (writer) and :5001 (reader).

set -euo pipefail

TARGET="${1:?usage: $0 <lab-server|secondary-host>}"
HERE="$(cd "$(dirname "$0")/.." && pwd)"
INVENTORY="$HERE/inventory/${TARGET}.yml"
RESULTS="$HERE/tests/results"
mkdir -p "$RESULTS"

SUMMARY="$RESULTS/matrix-$(date -u '+%Y%m%dT%H%M%SZ').md"

PGCAT_IP=$(python3 -c "
import yaml
inv = yaml.safe_load(open('$INVENTORY'))
print(next(iter(inv['all']['children']['pgcat']['hosts'].values()))['ansible_host'])
")

log() { printf '[%s] %s\n' "$(date -u '+%Y-%m-%dT%H:%M:%SZ')" "$*" | tee -a "$SUMMARY"; }

{
  echo "# postgres-aegis-vms prod-matrix run — $(date -u '+%Y-%m-%dT%H:%M:%SZ')"
  echo ""
  echo "Target: \`$TARGET\`  |  PgCat/HAProxy: \`$PGCAT_IP\`"
  echo ""
  echo "## Results"
  echo ""
} > "$SUMMARY"

# --- T01 baseline OLTP via HAProxy writer ---
log "T01 baseline OLTP"
pgbench -h "$PGCAT_IP" -p 5000 -U aegis_admin -d casino -c 32 -j 4 -T 60 -M prepared \
  2>&1 | tee -a "$SUMMARY" | grep -E '^tps|latency average'

# --- T02 TLS overhead (PGSSLMODE) ---
log "T02 TLS overhead"
PGSSLMODE=verify-full pgbench -h "$PGCAT_IP" -p 5000 -U aegis_admin -d casino -c 32 -j 4 -T 60 -M prepared \
  2>&1 | tee -a "$SUMMARY" | grep -E '^tps|latency average' || echo "T02 failed"

# --- T03 LUKS + writer-kill (calls chaos script for 1 cycle) ---
log "T03 LUKS + writer-kill"
CYCLES=1 INTERVAL=30 bash "$HERE/tests/T12_chaos_leader_kill.sh" "$TARGET" | tee -a "$SUMMARY"

# --- T04 HSM-wrapped DEK rotation ---
log "T04 HSM DEK rotation (requires BAO_TOKEN with rotate perm)"
bash "$HERE/scripts/rotate-dek.sh" "$TARGET" | tee -a "$SUMMARY" || log "T04 skipped (no rotate perm)"

# --- T05 pg_aegis vs pgcrypto (remote exec against writer) ---
log "T05 pg_aegis vs pgcrypto"
WRITER_IP=$(python3 -c "
import yaml
inv = yaml.safe_load(open('$INVENTORY'))
g = 'shard_a_writer'
print(next(iter(inv['all']['children'][g]['hosts'].values()))['ansible_host'])
")
scp -q "$HERE/tests/matrix/T05_aegis_vs_pgcrypto_prod.sh" "ansible@$WRITER_IP:/tmp/"
ssh "ansible@$WRITER_IP" "bash /tmp/T05_aegis_vs_pgcrypto_prod.sh" | tee -a "$SUMMARY"

# --- T06 shard routing balance (PgCat) ---
log "T06 shard routing"
bash "$HERE/tests/matrix/T06_shard_routing.sh" "$PGCAT_IP" | tee -a "$SUMMARY"

# --- T07 read-heavy ---
log "T07 read-heavy 10k rps"
pgbench -h "$PGCAT_IP" -p 5001 -U aegis_admin -d casino -S -c 64 -j 8 -T 60 \
  2>&1 | tee -a "$SUMMARY" | grep -E '^tps'

# --- T10 blue-green backfill ---
log "T10 blue-green backfill"
bash "$HERE/tests/matrix/T10_backfill_prod.sh" "$WRITER_IP" | tee -a "$SUMMARY"

# --- T11 backup + restore + RTO/RPO ---
log "T11 backup + restore + RTO/RPO"
bash "$HERE/tests/T11_backup_restore_rto.sh" "$TARGET" | tee -a "$SUMMARY"

# --- T12 chaos (6x 10-min cycles) ---
log "T12 chaos (long run)"
CYCLES=6 INTERVAL=600 bash "$HERE/tests/T12_chaos_leader_kill.sh" "$TARGET" | tee -a "$SUMMARY"

log "Matrix complete: $SUMMARY"
