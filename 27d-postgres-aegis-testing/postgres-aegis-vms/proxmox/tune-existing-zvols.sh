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

# tune-existing-zvols.sh — apply live-settable ZFS properties to postgres-aegis
# zvols that were created with suboptimal defaults.
#
# What CAN be changed live (no VM restart, no data migration):
#   compression  — zstd-3 beats lz4 on structured data; applied to new writes
#   logbias      — latency (default) is correct for sync PostgreSQL workloads
#   primarycache — metadata: postgres manages its own page cache (shared_buffers)
#   sync         — standard: ZFS honours fsync from guest; do not set to disabled
#                  on a database VM unless you accept data loss on power failure
#
# What CANNOT be changed live (requires migration via migrate-to-8k-zvol.sh):
#   volblocksize — immutable after creation; must be 8K for new VMs (create-cluster.sh
#                  now pre-creates zvols with 8K, fixing all future deployments)
#
# Usage:
#   PROXMOX_HOST=secondary-host bash proxmox/tune-existing-zvols.sh

set -euo pipefail

PROXMOX_HOST="${PROXMOX_HOST:-secondary-host}"
ZFS_POOL="${ZFS_POOL:-nvme-0-zfs}"

log() { printf '[%s] %s\n' "$(date -u '+%Y-%m-%dT%H:%M:%SZ')" "$*"; }

log "=== ZFS tuning for postgres-aegis data zvols on $PROXMOX_HOST ==="

# All data disks (disk-1) for the shard-b cluster (vmid 2012, 2013, 2030 pgcat)
DATA_ZVOLS=(
    "${ZFS_POOL}/vms/vm-2012-disk-1"
    "${ZFS_POOL}/vms/vm-2013-disk-1"
)

for zvol in "${DATA_ZVOLS[@]}"; do
    if ! ssh -o BatchMode=yes "root@${PROXMOX_HOST}" "zfs list ${zvol}" >/dev/null 2>&1; then
        log "SKIP (not found): ${zvol}"
        continue
    fi
    log "tuning: ${zvol}"
    ssh -o BatchMode=yes "root@${PROXMOX_HOST}" "
        zfs set compression=zstd-3     ${zvol}
        zfs set logbias=latency        ${zvol}
        zfs set primarycache=metadata  ${zvol}
        zfs set sync=standard          ${zvol}
    "
    log "current properties on ${zvol}:"
    ssh -o BatchMode=yes "root@${PROXMOX_HOST}" \
        "zfs get volblocksize,compression,logbias,primarycache,sync ${zvol}"
done

log "=== tune complete ==="
log ""
log "NOTE: volblocksize remains 16K on existing zvols (immutable)."
log "      Run proxmox/migrate-to-8k-zvol.sh to migrate to 8K zvols"
log "      for the PostgreSQL page-size alignment benefit."
log ""
log "ZFS tuning summary for LUKS-encrypted PostgreSQL zvols:"
log "  volblocksize=8K   : [migration required] matches PG 8K page size, eliminates"
log "                       read-modify-write amplification on every page write"
log "  compression=lz4   : lz4 is a near-zero-overhead no-op on LUKS ciphertext;"
log "                       do NOT use zstd/gzip here — they waste CPU with 0% saving"
log "                       because LUKS ciphertext is statistically random."
log "                       To get real compression: use ZFS native encryption instead"
log "                       (zfs set encryption=on) which compresses BEFORE encrypting."
log "  logbias=latency   : correct for sync PostgreSQL (sync_commit=on) — ZIL path"
log "                       optimises small synchronous writes. Use throughput only"
log "                       for bulk-insert or sync_commit=off workloads."
log "  primarycache=metadata: PG manages its own page cache (shared_buffers);"
log "                          caching data blocks in ZFS ARC wastes RAM."
log "  sync=standard     : ZFS honours guest fsync — do not set disabled on DB VMs."
