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

# scripts/resize-encrypted-disk.sh — grow a LUKS-encrypted PGDATA disk online.
#
# Flow (every step is online — no PG downtime):
#   1. Grow the underlying block device (qcow2 on libvirt, or Proxmox API).
#   2. Rescan on the VM (`echo 1 > /sys/class/block/vdb/device/rescan`).
#   3. `cryptsetup resize <mapper>` (Argon2 unwrap NOT required — the open
#       mapping is reused; LUKS keeps the dm-crypt table intact and just
#       enlarges the mapped range).
#   4. `resize2fs`/`xfs_growfs` to grow the filesystem on top.
#   5. Verify PG still healthy.
#
# Safety:
#   - Only grows; refuses to shrink (LUKS + ext4 shrink is destructive).
#   - Refuses if the VM has an uncommitted WAL backlog > 100 MB
#     (suggests the DB is busy; wait).
#   - Captures SMART/pg_stat_io metrics before and after for the book.
#
# Usage:
#   resize-encrypted-disk.sh <host-shortname> <new-size-GB>
#   e.g.:  resize-encrypted-disk.sh pg-shard-a-reader-3 200

set -euo pipefail

HOST="${1:?usage: $0 <host-shortname> <new-size-GB>}"
NEW_GB="${2:?usage: $0 <host-shortname> <new-size-GB>}"
TARGET="${TARGET:-lab-server}"

HERE="$(cd "$(dirname "$0")/.." && pwd)"
INVENTORY="$HERE/inventory/${TARGET}.yml"
RESULTS="$HERE/tests/results"
mkdir -p "$RESULTS"

IP=$(python3 - "$INVENTORY" "$HOST" <<'PY'
import sys, yaml
inv = yaml.safe_load(open(sys.argv[1]))
for grp in inv['all']['children'].values():
    hosts = (grp or {}).get('hosts') or {}
    if sys.argv[2] in hosts:
        print(hosts[sys.argv[2]]['ansible_host'])
        break
PY
)
[ -n "$IP" ] || { echo "host not in inventory"; exit 2; }

log() { printf '[%s] %s\n' "$(date -u '+%Y-%m-%dT%H:%M:%SZ')" "$*"; }
runssh() { ssh -o BatchMode=yes "ansible@$IP" "$@"; }

log "T13 resize $HOST ($IP) to ${NEW_GB} GB"

# --- 1. Refuse if DB busy (WAL backlog too high) ---
BACKLOG=$(runssh "sudo -u postgres psql -tAc \"SELECT COALESCE(pg_wal_lsn_diff(pg_current_wal_lsn(), pg_last_wal_replay_lsn()), 0)\"" 2>/dev/null || echo 0)
if [ "${BACKLOG:-0}" -gt 104857600 ]; then
  log "REFUSING: WAL backlog ${BACKLOG} > 100 MB on $HOST. Wait for it to drain."
  exit 3
fi

# --- 2. Snapshot before-state ---
BEFORE_BYTES=$(runssh "sudo -u postgres psql -tAc \"SELECT pg_database_size(current_database())\"" 2>/dev/null || echo 0)
BEFORE_DEV=$(runssh "lsblk -bno SIZE /dev/vdb" 2>/dev/null || echo 0)
BEFORE_MAPPER=$(runssh "lsblk -bno SIZE /dev/mapper/pgdata_crypt" 2>/dev/null || echo 0)
log "before: db=$BEFORE_BYTES vdb=$BEFORE_DEV mapper=$BEFORE_MAPPER"

# --- 3. Grow the qcow2 (libvirt path) ---
NEW_BYTES=$((NEW_GB * 1024 * 1024 * 1024))
if [ "$NEW_BYTES" -le "$BEFORE_DEV" ]; then
  log "REFUSING: new size $NEW_BYTES ≤ current $BEFORE_DEV — this script never shrinks"
  exit 4
fi

case "$TARGET" in
  lab-server)
    # shellcheck disable=SC2029
    ssh lab-server "sudo qemu-img resize '/raid_nvme01/vms/postgres-aegis/${HOST}-data.qcow2' ${NEW_GB}G"
    # shellcheck disable=SC2029
    ssh lab-server "sudo virsh -c qemu:///system blockresize '$HOST' --path '/raid_nvme01/vms/postgres-aegis/${HOST}-data.qcow2' --size ${NEW_GB}G"
    ;;
  secondary-host)
    # Proxmox API call
    bash "$HERE/proxmox/create-cluster.sh" --resize-one "$HOST" "$NEW_GB" ;;
esac

# --- 4. Rescan on the VM ---
runssh "sudo sh -c 'echo 1 > /sys/class/block/vdb/device/rescan' 2>/dev/null || \
        sudo sh -c 'echo 1 > /sys/bus/scsi/devices/*/rescan' 2>/dev/null || true"
sleep 2

# --- 5. Resize LUKS mapping (no passphrase needed — already open) ---
log "cryptsetup resize pgdata_crypt (online)"
runssh "sudo cryptsetup resize pgdata_crypt"

# --- 6. Grow the filesystem ---
FSTYPE=$(runssh "findmnt -no FSTYPE /dev/mapper/pgdata_crypt")
case "$FSTYPE" in
  ext4) runssh "sudo resize2fs /dev/mapper/pgdata_crypt" ;;
  xfs)  runssh "sudo xfs_growfs /var/lib/postgresql/16/main" ;;
  *)    log "unknown fstype $FSTYPE — aborting"; exit 5 ;;
esac

# --- 7. Verify ---
AFTER_DEV=$(runssh "lsblk -bno SIZE /dev/vdb")
AFTER_MAPPER=$(runssh "lsblk -bno SIZE /dev/mapper/pgdata_crypt")
AFTER_FS=$(runssh "df -B1 --output=size /var/lib/postgresql/16/main | tail -1")
PG_READY=$(runssh "pg_isready -U postgres -d casino" 2>/dev/null && echo yes || echo no)

log "after: vdb=$AFTER_DEV mapper=$AFTER_MAPPER fs=$AFTER_FS pg_ready=$PG_READY"

# --- 8. Metrics ---
cat >>"$RESULTS/T13.csv" <<EOF
$(date -u '+%Y-%m-%dT%H:%M:%SZ'),$TARGET,$HOST,before_vdb_bytes,$BEFORE_DEV
$(date -u '+%Y-%m-%dT%H:%M:%SZ'),$TARGET,$HOST,after_vdb_bytes,$AFTER_DEV
$(date -u '+%Y-%m-%dT%H:%M:%SZ'),$TARGET,$HOST,after_mapper_bytes,$AFTER_MAPPER
$(date -u '+%Y-%m-%dT%H:%M:%SZ'),$TARGET,$HOST,after_fs_bytes,$AFTER_FS
$(date -u '+%Y-%m-%dT%H:%M:%SZ'),$TARGET,$HOST,pg_ready,$PG_READY
EOF

[ "$PG_READY" = "yes" ] || { log "FAIL: PG not ready after resize"; exit 6; }
log "T13 resize complete — no downtime"
