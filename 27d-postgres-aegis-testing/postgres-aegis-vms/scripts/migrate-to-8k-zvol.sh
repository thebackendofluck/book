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

# migrate-to-8k-zvol.sh — zero-downtime migration of shard-b PGDATA zvols
# from volblocksize=16K to volblocksize=8K (matching PostgreSQL's 8K page size).
#
# Strategy (rolling, no application downtime):
#   1. Create new 8K-block zvols on nvme-0-zfs for each shard-b node.
#   2. Attach each new zvol as an additional Proxmox disk (scsi2).
#   3. Provision LUKS on scsi2 inside the VM using the existing key file.
#   4. rsync PGDATA from old mount to new (with Postgres stopped on that node).
#   5. Repoint the unlock service to /dev/sdc; restart Postgres.
#   6. Detach and delete the old zvol.
#
# Patroni handles failover so there is always a live primary during migration.
# LUKS is unlocked via key file (/etc/luks/<hostname>.key) — no passphrase needed.
#
# Usage:
#   PROXMOX=secondary-host bash migrate-to-8k-zvol.sh
#
# Prerequisites:
#   - SSH key access to $PROXMOX (root) and ansible@<vm-ip>
#   - Patroni quorum intact (at least one replica healthy)

set -euo pipefail

PROXMOX="${PROXMOX:-secondary-host}"
ZFS_POOL="${ZFS_POOL:-nvme-0-zfs}"
PVE_STORAGE="${PVE_STORAGE:-nvme-zfs-vms}"   # Proxmox storage name (pvesm status)
# VM IDs: writer=2012 (IP .32), reader=2013 (IP .33 — current Patroni leader)
# Migrate REPLICA first, then failover, then migrate old leader.
NEW_DISK_SIZE="${NEW_DISK_SIZE:-150G}"    # slightly larger than old 100/150 G
# vmid→IP mapping (hardcoded for shard-b; adjust if fleet changes)
# vmid 2012 → 10.0.42.32 (replica after initial deploy)
# vmid 2013 → 10.0.42.33 (Patroni leader after chaos tests)
LUKS_NAME="pgdata_crypt"
PG_DATA="/var/lib/postgresql/16/main"

log() { printf '[%s] %s\n' "$(date -u '+%Y-%m-%dT%H:%M:%SZ')" "$*"; }
runssh() { ssh -o BatchMode=yes "ansible@$1" "${@:2}"; }
runpve() { ssh -o BatchMode=yes "root@$PROXMOX" "$@"; }

# Return the LUKS key-file path for a given VM IP.
luks_key_for_ip() {
    local ip=$1
    case "$ip" in
        10.0.42.32) echo "/etc/luks/pg-shard-b-writer-1.key" ;;
        10.0.42.33) echo "/etc/luks/pg-shard-b-reader-1.key" ;;
        *)             echo "/etc/luks/pgdata.key" ;;
    esac
}

# ── helpers ──────────────────────────────────────────────────────────────────

create_optimised_zvol() {
    local vmid=$1 size=$2
    local zvol="${ZFS_POOL}/vms/vm-${vmid}-disk-2"
    if runpve "zfs list ${zvol}" >/dev/null 2>&1; then
        log "zvol $zvol already exists — skipping create"
    else
        log "creating zvol $zvol volblocksize=8K compression=lz4"
        runpve "zfs create \
            -V ${size} \
            -o volblocksize=8K \
            -o compression=lz4 \
            -o logbias=latency \
            -o primarycache=metadata \
            -o sync=standard \
            ${zvol}"
    fi
    echo "$zvol"
}

attach_disk_to_vm() {
    local vmid=$1 zvol=$2
    log "attaching ${zvol} to VM ${vmid} as scsi2"
    runpve "qm set ${vmid} --scsi2 ${PVE_STORAGE}:vm-${vmid}-disk-2,discard=on,iothread=1,ssd=1"
}

provision_luks_on_new_disk() {
    local ip=$1
    local keyfile
    keyfile=$(luks_key_for_ip "$ip")
    log "formatting /dev/sdc with LUKS2 on $ip (key-file $keyfile)"
    runssh "$ip" "sudo bash -c '
        cryptsetup luksFormat --type luks2 \
            --cipher aes-xts-plain64 --key-size 256 --hash sha256 \
            --batch-mode --label pgdata2 \
            --key-file ${keyfile} /dev/sdc
        cryptsetup open --key-file ${keyfile} /dev/sdc pgdata_new
        mkfs.ext4 -m 0 -E lazy_itable_init=0,lazy_journal_init=0 \
            -b 4096 /dev/mapper/pgdata_new
    '"
}

migrate_pgdata() {
    local ip=$1
    local keyfile
    keyfile=$(luks_key_for_ip "$ip")
    log "stopping Postgres on $ip"
    runssh "$ip" "sudo systemctl stop patroni"
    sleep 3

    log "mounting new LUKS device and rsyncing PGDATA"
    runssh "$ip" "sudo bash -c '
        mount /dev/mapper/pgdata_new /mnt
        rsync -aHAX --delete \
            /var/lib/postgresql/16/main/ \
            /mnt/
        sync
        umount /mnt
        cryptsetup close pgdata_new
    '"

    log "closing old LUKS container on $ip"
    runssh "$ip" "sudo bash -c '
        umount ${PG_DATA} || true
        cryptsetup close ${LUKS_NAME} || true
    '"

    # After migration the old scsi1 disk is removed and scsi2 becomes the first
    # extra disk, so the guest enumerates the new zvol as /dev/sdb (not /dev/sdc).
    # Update the unlock service accordingly.
    log "updating unlock service to target /dev/sdb on $ip (new disk is scsi2, but enumerated as sdb after scsi1 removal)"
    runssh "$ip" "sudo bash -c '
        sed -i \"s|/dev/sd[bc]|/dev/sdb|g\" /etc/systemd/system/pg-aegis-luks-unlock.service
        systemctl daemon-reload
    '"

    log "reopening new LUKS and remounting PGDATA"
    runssh "$ip" "sudo bash -c '
        cryptsetup open --key-file ${keyfile} /dev/sdb ${LUKS_NAME}
        mount /dev/mapper/${LUKS_NAME} ${PG_DATA}
    '"

    log "restarting Patroni on $ip"
    runssh "$ip" "sudo systemctl start patroni"
    sleep 10
    runssh "$ip" "sudo patronictl -c /etc/patroni/patroni.yml list" || true
}

detach_old_disk() {
    local vmid=$1
    local old_zvol="${ZFS_POOL}/vms/vm-${vmid}-disk-1"
    log "detaching scsi1 from VM ${vmid} and removing old zvol"
    runpve "qm set ${vmid} --delete scsi1" || true
    runpve "zfs destroy ${old_zvol}" || true
}

# ── main ─────────────────────────────────────────────────────────────────────

log "=== NVMe ZFS zvol migration: 16K → 8K volblocksize ==="
log "pool=$ZFS_POOL  proxmox=$PROXMOX"

# Step 1: create new optimised zvols for both VMs
for vmid in 2012 2013; do
    create_optimised_zvol "$vmid" "$NEW_DISK_SIZE"
done

# Step 2: attach new disks
for vmid in 2012 2013; do
    attach_disk_to_vm "$vmid" "${ZFS_POOL}/vms/vm-${vmid}-disk-2"
done

sleep 5  # let Proxmox hot-plug settle

# Step 3: provision LUKS on the new disk on the REPLICA first (.32 = vmid 2012)
# (IP mapping: vmid 2012 → 10.0.42.32, vmid 2013 → 10.0.42.33)
REPLICA_IP=10.0.42.32
LEADER_IP=10.0.42.33

log "--- migrating REPLICA $REPLICA_IP (vmid 2012) ---"
provision_luks_on_new_disk "$REPLICA_IP"
migrate_pgdata "$REPLICA_IP"

# Wait for replica to catch up before failing over
log "waiting 30 s for replica WAL replay"
sleep 30

# Step 4: Patroni failover — promote replica, migrate old leader
log "--- initiating Patroni failover (making .32 the new leader) ---"
runssh "$REPLICA_IP" "sudo patronictl -c /etc/patroni/patroni.yml failover shard-b \
    --candidate pg-shard-b-writer-1 --force" || true
sleep 20

log "--- migrating OLD LEADER $LEADER_IP (vmid 2013, now replica) ---"
provision_luks_on_new_disk "$LEADER_IP"
migrate_pgdata "$LEADER_IP"

# Step 5: Remove old zvols
log "--- removing old 16K zvols ---"
for vmid in 2012 2013; do
    detach_old_disk "$vmid"
done

# Final verification
log "=== migration complete — verifying zvol tuning ==="
runpve "zfs get volblocksize,compression,logbias,primarycache \
    ${ZFS_POOL}/vms/vm-2012-disk-2 ${ZFS_POOL}/vms/vm-2013-disk-2"
