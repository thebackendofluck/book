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

# T11 — backup + destroy + restore + measure RTO and RPO.
#
# Runs against the shadow cluster on lab-server (default) or prod on secondary-host.
# Does NOT touch the primary cluster: the restore lands on a throwaway
# container/VM. Fails loudly if the restore diverges from the expected
# row count by more than 0.1%.
#
# Metrics:
#   RTO (Recovery Time Objective) = time to fully restore and accept writes.
#   RPO (Recovery Point Objective) = time between last committed txn on
#                                    origin and the last txn visible in the
#                                    restore.

set -euo pipefail

TARGET="${1:-lab-server}"
HERE="$(cd "$(dirname "$0")/.." && pwd)"
INVENTORY="$HERE/inventory/${TARGET}.yml"
RESULTS="$HERE/tests/results"
mkdir -p "$RESULTS"

STANZA="casino-aegis"
WRITER_IP=$(python3 -c "
import yaml
inv = yaml.safe_load(open('$INVENTORY'))
g = 'shard_a_writer'
print(next(iter(inv['all']['children'][g]['hosts'].values()))['ansible_host'])
")
TEST_HOST="${TEST_HOST:-$(hostname -s)-restore-$(date +%s)}"

log()    { printf '[%s] %s\n' "$(date -u '+%Y-%m-%dT%H:%M:%SZ')" "$*"; }
runssh() { ssh -o BatchMode=yes "ansible@$1" "${@:2}"; }

log "T11 starting: target=$TARGET writer=$WRITER_IP"

# --- 1. Snapshot origin state ---
origin_count=$(runssh "$WRITER_IP" "sudo -u postgres psql -tAc 'SELECT count(*) FROM player_pii_aegis;'")
origin_last_lsn=$(runssh "$WRITER_IP" "sudo -u postgres psql -tAc 'SELECT pg_current_wal_lsn()::text;'")
log "origin_count=$origin_count origin_last_lsn=$origin_last_lsn"

# --- 2. Trigger a fresh full backup ---
log "running pgbackrest full backup"
t0_backup=$(date +%s)
runssh "$WRITER_IP" "sudo -u postgres pgbackrest --stanza=$STANZA --type=full backup --log-level-console=info" | tail -5
t1_backup=$(date +%s)
log "backup completed in $((t1_backup - t0_backup)) s"

BACKUP_INFO=$(runssh "$WRITER_IP" "sudo -u postgres pgbackrest --stanza=$STANZA info --output=json" \
  | python3 -c "import sys,json; d=json.load(sys.stdin); print(d[0]['backup'][-1]['label'])")
log "backup label: $BACKUP_INFO"

# --- 3. Provision a throwaway VM for restore ---
log "provisioning throwaway host: $TEST_HOST"
if [ "$TARGET" = "lab-server" ]; then
  TMP=$(mktemp)
  cat >"$TMP" <<EOF
all:
  children:
    tmp:
      hosts:
        $TEST_HOST: {ansible_host: 10.0.42.200, vm_cpu: 4, vm_ram_mb: 8192}
EOF
  INVENTORY="$TMP" bash "$HERE/libvirt/create-cluster.sh"
  RESTORE_IP=10.0.42.200
  rm -f "$TMP"
else
  log "TARGET=secondary-host restore host not implemented in scaffold; aborting"
  exit 2
fi

# Wait for SSH
for _ in $(seq 1 60); do
  ssh -o BatchMode=yes -o ConnectTimeout=3 "ansible@$RESTORE_IP" true 2>/dev/null && break
  sleep 3
done

# --- 4. Install pgbackrest + Postgres on throwaway + perform restore ---
t0_restore=$(date +%s)
runssh "$RESTORE_IP" "sudo apt-get -y update && sudo apt-get -y install postgresql-16 pgbackrest"
runssh "$RESTORE_IP" "sudo systemctl stop postgresql@16-main"
# Reuse origin's pgbackrest config (assumes same credentials path in BAO)
scp -q "$HERE/ansible/roles/pgbackrest/templates/pgbackrest.conf.j2" "ansible@$RESTORE_IP:/tmp/pgbackrest.conf" 2>/dev/null || true
# Restore into a fresh PGDATA
runssh "$RESTORE_IP" "sudo -u postgres pgbackrest --stanza=$STANZA --delta restore"
runssh "$RESTORE_IP" "sudo systemctl start postgresql@16-main"

# Wait for PG to accept connections
for _ in $(seq 1 30); do
  runssh "$RESTORE_IP" "pg_isready -U postgres" 2>/dev/null && break
  sleep 2
done
t1_restore=$(date +%s)
RTO=$((t1_restore - t0_restore))
log "RTO: $RTO s"

# --- 5. Verify row count + LSN ---
restore_count=$(runssh "$RESTORE_IP" "sudo -u postgres psql -tAc 'SELECT count(*) FROM player_pii_aegis;' casino")
restore_last_lsn=$(runssh "$RESTORE_IP" "sudo -u postgres psql -tAc \"SELECT pg_last_wal_replay_lsn()::text;\" casino")

log "restore_count=$restore_count vs origin_count=$origin_count"
log "restore_last_lsn=$restore_last_lsn vs origin_last_lsn=$origin_last_lsn"

# RPO: bytes of WAL between origin's current and restore's visible
RPO_BYTES=$(runssh "$WRITER_IP" "sudo -u postgres psql -tAc \"SELECT pg_wal_lsn_diff('$origin_last_lsn'::pg_lsn, '$restore_last_lsn'::pg_lsn);\"" 2>/dev/null || echo "unknown")
log "RPO: $RPO_BYTES bytes of WAL gap"

# --- 6. Drift check ---
DRIFT=$(python3 -c "print(abs($origin_count - $restore_count))")
THRESHOLD=$(python3 -c "print(int($origin_count * 0.001))")
if [ "$DRIFT" -gt "$THRESHOLD" ]; then
  log "FAIL: row drift $DRIFT > threshold $THRESHOLD"
  EXITCODE=1
else
  log "PASS: drift $DRIFT within threshold $THRESHOLD"
  EXITCODE=0
fi

# --- 7. Emit metrics ---
cat >>"$RESULTS/T11.csv" <<EOF
$(date -u '+%Y-%m-%dT%H:%M:%SZ'),$TARGET,rto_seconds,$RTO
$(date -u '+%Y-%m-%dT%H:%M:%SZ'),$TARGET,rpo_bytes,$RPO_BYTES
$(date -u '+%Y-%m-%dT%H:%M:%SZ'),$TARGET,origin_rows,$origin_count
$(date -u '+%Y-%m-%dT%H:%M:%SZ'),$TARGET,restore_rows,$restore_count
$(date -u '+%Y-%m-%dT%H:%M:%SZ'),$TARGET,drift_rows,$DRIFT
EOF

# --- 8. Cleanup throwaway host ---
log "destroying throwaway $TEST_HOST"
# shellcheck disable=SC2029  # TEST_HOST intentionally expands on client before ssh
ssh lab-server "virsh -c qemu:///system destroy '$TEST_HOST' 2>/dev/null; virsh -c qemu:///system undefine '$TEST_HOST' --remove-all-storage 2>/dev/null" || true

log "T11 complete; exitcode=$EXITCODE"
exit "$EXITCODE"
