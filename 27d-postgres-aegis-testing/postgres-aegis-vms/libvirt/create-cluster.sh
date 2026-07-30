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

# libvirt/create-cluster.sh — create or destroy the postgres-aegis-vms fleet
# on lab-server via qemu+ssh://lab-server/system. Reads inventory/lab-server.yml.
# Idempotent: skips VMs that already exist (unless --recreate is passed).

set -euo pipefail

HERE="$(cd "$(dirname "$0")" && pwd)"
INVENTORY="$HERE/../inventory/lab-server.yml"

BASE_IMAGE="/raid_nvme01/vms/images/ubuntu-22.04-pgdb.qcow2"
VM_DIR="/raid_nvme01/vms/postgres-aegis"
BRIDGE="vmbr0"
VLAN=90

die() { echo "ERROR: $*" >&2; exit 1; }

list_vms() {
  python3 - "$INVENTORY" <<'PY'
import sys, yaml
with open(sys.argv[1]) as f:
    inv = yaml.safe_load(f)
all_grp = inv.get('all', {})
kids = all_grp.get('children', {}) or {}
seen = set()
for grp_name, grp in kids.items():
    if grp_name in ('writers', 'readers', 'postgres'):
        continue
    hosts = (grp or {}).get('hosts') or {}
    gvars = (grp or {}).get('vars') or {}
    for host, h in hosts.items():
        if host in seen:
            continue
        seen.add(host)
        cpu = (h or {}).get('vm_cpu')    or gvars.get('vm_cpu')    or 2
        ram = (h or {}).get('vm_ram_mb') or gvars.get('vm_ram_mb') or 4096
        ip  = (h or {}).get('ansible_host', '')
        print(f"{host} {cpu} {ram} {ip}")
PY
}

provision_one() {
  local name="$1" cpu="$2" ram="$3" ip="$4"
  local disk_os="$VM_DIR/${name}-os.qcow2"
  local disk_data="$VM_DIR/${name}-data.qcow2"
  local seed_iso="$VM_DIR/${name}-seed.iso"
  local cidata
  cidata=$(mktemp -d)

  cat > "$cidata/user-data" <<EOF
#cloud-config
hostname: $name
manage_etc_hosts: true
users:
  - name: ansible
    sudo: ALL=(ALL) NOPASSWD:ALL
    shell: /bin/bash
    ssh_authorized_keys:
      - $SSH_PUB
ssh_pwauth: false
package_update: true
packages:
  - qemu-guest-agent
  - cryptsetup-bin
runcmd:
  - [ systemctl, enable, --now, qemu-guest-agent ]
write_files:
  - path: /etc/netplan/99-static.yaml
    content: |
      network:
        version: 2
        ethernets:
          enp1s0:
            addresses: [$ip/24]
            gateway4: 10.0.42.1
            nameservers:
              addresses: [10.0.10.1, 1.1.1.1]
  - path: /etc/cloud/cloud.cfg.d/99-disable-network.cfg
    content: |
      network: {config: disabled}
EOF

  cat > "$cidata/meta-data" <<EOF
instance-id: iid-$name
local-hostname: $name
EOF

  (
    cd "$cidata"
    if command -v genisoimage >/dev/null; then
      genisoimage -output "$seed_iso.local" -volid cidata -joliet -rock user-data meta-data >/dev/null 2>&1
    else
      mkisofs -output "$seed_iso.local" -volid cidata -joliet -rock user-data meta-data >/dev/null 2>&1
    fi
  )

  scp -q "$seed_iso.local" "lab-server:$seed_iso"
  # shellcheck disable=SC2029
  ssh lab-server "sudo qemu-img create -f qcow2 -F qcow2 -b '$BASE_IMAGE' '$disk_os' 40G && \
               sudo qemu-img create -f qcow2 '$disk_data' 100G && \
               sudo virt-install --connect qemu:///system --name '$name' \
                  --memory '$ram' --vcpus '$cpu' \
                  --disk path='$disk_os',format=qcow2,bus=virtio,cache=writeback \
                  --disk path='$disk_data',format=qcow2,bus=virtio,cache=writeback \
                  --disk path='$seed_iso',device=cdrom \
                  --os-variant ubuntu22.04 \
                  --network bridge='$BRIDGE',model=virtio,portgroup='vlan$VLAN' \
                  --graphics none --noautoconsole --import"
  rm -rf "$cidata" "$seed_iso.local"
}

destroy_one() {
  local name="$1"
  # shellcheck disable=SC2029
  ssh lab-server "virsh -c qemu:///system destroy '$name' 2>/dev/null; \
               virsh -c qemu:///system undefine '$name' --remove-all-storage 2>/dev/null" || true
}

main() {
  local mode="create"
  case "${1:-}" in
    --destroy)  mode="destroy" ;;
    --recreate) mode="recreate" ;;
  esac

  [ -f "$INVENTORY" ] || die "inventory missing: $INVENTORY"

  SSH_PUB="$(cat ~/.ssh/id_rsa.pub 2>/dev/null || cat ~/.ssh/id_ed25519.pub 2>/dev/null || true)"
  [ -n "$SSH_PUB" ] || die "no ssh pub key found at ~/.ssh/id_{rsa,ed25519}.pub"

  echo "[cluster] mode=$mode inventory=$INVENTORY"

  # shellcheck disable=SC2029
  ssh lab-server "sudo mkdir -p '$VM_DIR/images' && test -f '$BASE_IMAGE'" \
    || die "base image missing on lab-server: $BASE_IMAGE (packer build first, or copy a cloud image)"
  ssh lab-server "virsh -c qemu:///system list --all >/dev/null" \
    || die "libvirt not reachable on lab-server"

  while read -r NAME CPU RAM IP; do
    case "$mode" in
      destroy)
        echo "[cluster] destroy: $NAME"
        destroy_one "$NAME"
        ;;
      recreate)
        echo "[cluster] recreate: $NAME"
        destroy_one "$NAME"
        provision_one "$NAME" "$CPU" "$RAM" "$IP"
        ;;
      create)
        # shellcheck disable=SC2029
        if ssh lab-server "virsh -c qemu:///system dominfo '$NAME' >/dev/null 2>&1"; then
          echo "[cluster] skip (exists): $NAME"
          continue
        fi
        echo "[cluster] create: $NAME cpu=$CPU ram=$RAM ip=$IP"
        provision_one "$NAME" "$CPU" "$RAM" "$IP"
        ;;
    esac
  done < <(list_vms)

  echo "[cluster] done"
}

main "$@"
