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

# proxmox/create-cluster.sh — clone the ubuntu-jammy-pgdb-template into a
# postgres-aegis fleet on secondary-host via the Proxmox API.
#
# Reads inventory/proxmox-secondary-host.yml, picks each host's vmid, and issues a
# POST /nodes/secondary-host/qemu/<TEMPLATE>/clone for every host.
#
# Modes:
#   (no arg)    create missing VMs
#   --destroy   stop and delete VMs
#   --recreate  destroy and then create

set -euo pipefail

HERE="$(cd "$(dirname "$0")" && pwd)"
INVENTORY="$HERE/../inventory/proxmox-secondary-host.yml"

: "${PROXMOX_API_URL:?set PROXMOX_API_URL or source ~/.config/postgres-aegis/secondary-host.env}"
: "${PROXMOX_API_TOKEN_ID:?}"
: "${PROXMOX_API_TOKEN_SECRET:?}"
: "${PROXMOX_NODE:=secondary-host}"
: "${PROXMOX_HOST:=secondary-host}"       # SSH target for direct ZFS tuning

TEMPLATE_VMID="${TEMPLATE_VMID:-9000}"       # tmpl-noble-k8s (Ubuntu 24.04 Noble)
STORAGE="${STORAGE:-nvme-zfs-vms}"
ZFS_POOL="${ZFS_POOL:-nvme-0-zfs}"           # underlying ZFS pool name
DATA_DISK_GB="${DATA_DISK_GB:-100}"           # second disk, will hold LUKS + PGDATA

api() {
  local method="$1"; shift
  local path="$1"; shift
  curl -sk -X "$method" \
    -H "Authorization: PVEAPIToken=${PROXMOX_API_TOKEN_ID}=${PROXMOX_API_TOKEN_SECRET}" \
    "$@" "${PROXMOX_API_URL}${path}"
}

mode="${1:-create}"
case "$mode" in
  --destroy)  mode="destroy" ;;
  --recreate) mode="recreate" ;;
  --add-one)  mode="add-one"; shift; ADD_NAME="${1:?need vm name}" ;;
  --destroy-one) mode="destroy-one"; shift; DEL_NAME="${1:?need vm name}" ;;
  --resize-one)  mode="resize-one";  shift; RES_NAME="${1:?need vm name}"; shift; RES_GB="${1:?need size GB}" ;;
esac

list_vms() {
  python3 - "$INVENTORY" <<'PY'
import sys, yaml
with open(sys.argv[1]) as f:
    inv = yaml.safe_load(f)
kids = inv['all']['children']
seen = set()
for grp_name, grp in kids.items():
    if grp_name in ('writers', 'readers', 'postgres'):
        continue
    for host, h in ((grp or {}).get('hosts') or {}).items():
        if host in seen:
            continue
        seen.add(host)
        gvars = (grp or {}).get('vars') or {}
        cpu = (h or {}).get('vm_cpu')    or gvars.get('vm_cpu')    or 2
        ram = (h or {}).get('vm_ram_mb') or gvars.get('vm_ram_mb') or 4096
        ip  = (h or {}).get('ansible_host', '')
        vmid = (h or {}).get('vmid') or gvars.get('vmid') or 0
        print(f"{host} {vmid} {cpu} {ram} {ip}")
PY
}

clone_one() {
  local name="$1" newid="$2" cpu="$3" ram="$4" ip="$5"
  echo "[proxmox] clone $name (vmid=$newid, cpu=$cpu, ram=${ram}M, ip=$ip)"
  # Orphan cleanup before clone (Proxmox refuses if 201.conf exists from a
  # previous aborted run). Uses pvesh via root ssh when the API token lacks
  # raw fs access. Harmless if already clean.
  if [ -n "${PROXMOX_ROOT_SSHPASS:-}" ] && command -v sshpass >/dev/null; then
    SSHPASS="$PROXMOX_ROOT_SSHPASS" sshpass -e ssh \
      -o StrictHostKeyChecking=no -o PreferredAuthentications=password -o PubkeyAuthentication=no \
      "root@${PROXMOX_HOST:-10.0.0.10}" \
      "rm -f /etc/pve/nodes/${PROXMOX_NODE}/qemu-server/${newid}.conf; \
       zfs list -H -o name 2>/dev/null | grep -E '/vm-${newid}-' | xargs -r -n1 zfs destroy 2>/dev/null; \
       true" 2>/dev/null || true
  fi
  api POST "/nodes/${PROXMOX_NODE}/qemu/${TEMPLATE_VMID}/clone" \
    -d "newid=${newid}" -d "name=${name}" -d "full=1" -d "storage=${STORAGE}"
  # Wait for clone task to complete — poll until the VM is configurable.
  for _ in $(seq 1 120); do
    sleep 5
    status=$(api GET "/nodes/${PROXMOX_NODE}/qemu/${newid}/status/current" 2>/dev/null \
             | python3 -c "import sys,json; d=json.load(sys.stdin); print(d.get('data',{}).get('status',''))" 2>/dev/null || echo "")
    [ -n "$status" ] && break
  done
  # Resize OS disk to 40G (template default is only 3.5G).
  api PUT "/nodes/${PROXMOX_NODE}/qemu/${newid}/resize" \
    -d "disk=scsi0" -d "size=40G" >/dev/null 2>&1 || true
  # Pre-create the data zvol with PostgreSQL-optimised settings BEFORE Proxmox
  # attaches it. Proxmox's default blocksize is 16K (from storage.cfg); we need
  # 8K to match PostgreSQL's 8K page size and avoid read-modify-write on every
  # page write. volblocksize cannot be changed after creation.
  local zvol="${ZFS_POOL}/vms/vm-${newid}-disk-1"
  ssh -o BatchMode=yes "root@${PROXMOX_HOST}" \
    "zfs create \
       -V ${DATA_DISK_GB}G \
       -o volblocksize=8K \
       -o compression=lz4 \
       -o logbias=latency \
       -o primarycache=metadata \
       -o sync=standard \
       ${zvol}" 2>/dev/null || true   # noop if already exists
  # Tell Proxmox to reference the pre-created zvol (not re-create it).
  api PUT "/nodes/${PROXMOX_NODE}/qemu/${newid}/config" \
    -d "scsi1=${STORAGE}:vm-${newid}-disk-1,discard=on,ssd=1,iothread=1" >/dev/null
  # CPU / RAM / boot / network / cloud-init user + ssh keys.
  local sshkeys
  sshkeys=$(python3 -c "import urllib.parse,sys;print(urllib.parse.quote(open(sys.argv[1]).read()))" \
            "${SSH_PUBKEY_PATH:-$HOME/.ssh/id_ed25519.pub}")
  api PUT "/nodes/${PROXMOX_NODE}/qemu/${newid}/config" \
    -d "cores=${cpu}" -d "memory=${ram}" -d "onboot=1" -d "agent=1" \
    -d "ipconfig0=ip=${ip}/24,gw=10.0.42.1" \
    -d "ciuser=ansible" -d "sshkeys=${sshkeys}" >/dev/null
  # Start the VM.
  api POST "/nodes/${PROXMOX_NODE}/qemu/${newid}/status/start" >/dev/null
}

destroy_one() {
  local name="$1" vmid="$2"
  echo "[proxmox] destroy $name (vmid=$vmid)"
  api POST "/nodes/${PROXMOX_NODE}/qemu/${vmid}/status/stop"    >/dev/null 2>&1 || true
  sleep 3
  api DELETE "/nodes/${PROXMOX_NODE}/qemu/${vmid}" -d "purge=1" >/dev/null 2>&1 || true
}

main() {
  case "$mode" in
    destroy)
      while read -r NAME VMID _CPU _RAM _IP; do destroy_one "$NAME" "$VMID"; done < <(list_vms)
      ;;
    recreate)
      while read -r NAME VMID _CPU _RAM _IP; do destroy_one "$NAME" "$VMID"; done < <(list_vms)
      sleep 5
      while read -r NAME VMID CPU RAM IP;    do clone_one  "$NAME" "$VMID" "$CPU" "$RAM" "$IP"; done < <(list_vms)
      ;;
    add-one)
      ROW=$(list_vms | awk -v n="$ADD_NAME" '$1==n {print; exit}')
      [ -n "$ROW" ] || { echo "host $ADD_NAME not in inventory"; exit 2; }
      read -r NAME VMID CPU RAM IP <<<"$ROW"
      clone_one "$NAME" "$VMID" "$CPU" "$RAM" "$IP"
      ;;
    destroy-one)
      ROW=$(list_vms | awk -v n="$DEL_NAME" '$1==n {print; exit}')
      [ -n "$ROW" ] || { echo "host $DEL_NAME not in inventory"; exit 2; }
      read -r NAME VMID _CPU _RAM _IP <<<"$ROW"
      destroy_one "$NAME" "$VMID"
      ;;
    resize-one)
      ROW=$(list_vms | awk -v n="$RES_NAME" '$1==n {print; exit}')
      [ -n "$ROW" ] || { echo "host $RES_NAME not in inventory"; exit 2; }
      read -r _NAME VMID _CPU _RAM _IP <<<"$ROW"
      echo "[proxmox] resize vmid=$VMID data disk to ${RES_GB}G"
      api PUT "/nodes/${PROXMOX_NODE}/qemu/${VMID}/resize" -d "disk=scsi1" -d "size=${RES_GB}G"
      ;;
    create)
      while read -r NAME VMID CPU RAM IP; do
        EXISTS=$(api GET "/nodes/${PROXMOX_NODE}/qemu/${VMID}/status/current" 2>&1 | python3 -c "import sys,json; d=json.load(sys.stdin); print('y' if d.get('data') else 'n')" 2>/dev/null || echo "n")
        if [ "$EXISTS" = "y" ]; then
          echo "[proxmox] skip (exists): $NAME"
          continue
        fi
        clone_one "$NAME" "$VMID" "$CPU" "$RAM" "$IP"
      done < <(list_vms)
      ;;
  esac
  echo "[proxmox] done"
}

main
