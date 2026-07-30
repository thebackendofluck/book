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

# spin-env.sh — one-command postgres-aegis environment.
#
# Creates a 1-writer + 1-reader + 3-etcd + 1-pgcat Patroni cluster on
# secondary-host Proxmox from scratch, plus an app-side mTLS client bundle.
#
# Usage (from lab-server):
#   ./spin-env.sh up      # provision + bootstrap
#   ./spin-env.sh test    # run T01 / T06 / T11 / T03 matrix
#   ./spin-env.sh down    # destroy VMs
#
# Env:
#   BAO_TOKEN      (auto-sourced from /opt/dashboard-keys/bao_token via sudo if present)
#   PROXMOX_API_TOKEN_ID / SECRET  (auto-sourced from ~/.config/postgres-aegis/secondary-host.env)

set -euo pipefail

HERE="$(cd "$(dirname "$0")/.." && pwd)"
cd "$HERE"

# --- creds auto-load ---
# shellcheck source=/dev/null
[ -f "$HOME/.config/postgres-aegis/secondary-host.env" ] && . "$HOME/.config/postgres-aegis/secondary-host.env"
: "${PROXMOX_API_URL:=https://10.0.0.10:8006/api2/json}"
: "${PROXMOX_NODE:=secondary-host}"
: "${PROXMOX_API_TOKEN_ID:?export PROXMOX_API_TOKEN_ID or put it in ~/.config/postgres-aegis/secondary-host.env}"
: "${PROXMOX_API_TOKEN_SECRET:?}"
: "${BAO_TOKEN:=$(sudo -n cat /opt/dashboard-keys/bao_token 2>/dev/null || echo '')}"
: "${CONTROLLER:=lab-server}"

# --- inventory shape ---
VMS=(
  "2001 pg-etcd-1 2 2048 10.0.42.21"
  "2002 pg-etcd-2 2 2048 10.0.42.22"
  "2003 pg-etcd-3 2 2048 10.0.42.23"
  "2010 pg-shard-a-writer-1 2 4096 10.0.42.30"
  "2011 pg-shard-a-reader-1 2 2048 10.0.42.31"
  "2030 pg-cat-1 2 2048 10.0.42.50"
)

api() { curl -sk -H "Authorization: PVEAPIToken=${PROXMOX_API_TOKEN_ID}=${PROXMOX_API_TOKEN_SECRET}" "$@"; }

cmd=${1:-}
case "$cmd" in
  up)
    echo "[spin] provisioning ${#VMS[@]} VMs on $PROXMOX_NODE"
    for vm in "${VMS[@]}"; do
      read -r vmid name cpu ram ip <<<"$vm"
      EXISTS=$(api GET "${PROXMOX_API_URL}/nodes/${PROXMOX_NODE}/qemu/${vmid}/status/current" 2>/dev/null | python3 -c "import sys,json; d=json.load(sys.stdin); print('y' if d.get('data') else 'n')" 2>/dev/null || echo n)
      if [ "$EXISTS" = "y" ]; then
        echo "  skip $name (exists)"; continue
      fi
      echo "  clone $name"
      api POST -d "newid=${vmid}" -d "name=${name}" -d "full=1" -d "storage=nvme-zfs-vms" \
        "${PROXMOX_API_URL}/nodes/${PROXMOX_NODE}/qemu/9010/clone" >/dev/null
    done
    echo "[spin] configuring + starting in parallel"
    for vm in "${VMS[@]}"; do
      read -r vmid name cpu ram ip <<<"$vm"
      (
        SSHPUB=$(python3 -c "import urllib.parse,sys;print(urllib.parse.quote(open(sys.argv[1]).read()))" ~/.ssh/id_ed25519.pub)
        api PUT "${PROXMOX_API_URL}/nodes/${PROXMOX_NODE}/qemu/${vmid}/config" \
          -d "cores=${cpu}" -d "memory=${ram}" -d "onboot=0" -d "agent=1" \
          -d "scsi1=nvme-zfs-vms:100,discard=on,ssd=1,iothread=1" \
          -d "ipconfig0=ip=${ip}/24,gw=10.0.42.1" \
          -d "nameserver=10.0.10.1 1.1.1.1" \
          -d "ciuser=ansible" -d "sshkeys=${SSHPUB}" >/dev/null
        api PUT "${PROXMOX_API_URL}/nodes/${PROXMOX_NODE}/qemu/${vmid}/resize" -d "disk=scsi0" -d "size=40G" >/dev/null
        api POST "${PROXMOX_API_URL}/nodes/${PROXMOX_NODE}/qemu/${vmid}/status/start" >/dev/null
      ) &
    done
    wait

    echo "[spin] waiting for all SSH"
    for vm in "${VMS[@]}"; do read -r _ _ _ _ ip <<<"$vm"; until nc -z -w 2 "$ip" 22 2>/dev/null; do sleep 5; done; done

    echo "[spin] running Ansible bootstrap"
    cd "$HERE"
    rsync -a ansible/ inventory/ "$CONTROLLER":/tmp/aegis-test/ 2>&1 | tail -1
    ssh -o BatchMode=yes "$CONTROLLER" \
      "cd /tmp/aegis-test && ansible-playbook -i inventory/proxmox-secondary-host.yml ansible/pre-wait.yml && \
       ansible -i inventory/proxmox-secondary-host.yml all -m apt -a 'name=acl,python3-psycopg2 state=present' --become && \
       ansible-playbook -i inventory/proxmox-secondary-host.yml ansible/site.yml \
         --extra-vars 'smoke_mode=true bao_token=${BAO_TOKEN:-dummy}' \
         --skip-tags monitoring"
    echo "[spin] UP OK"
    ;;

  test)
    echo "[spin] running T01 / T06 / T03 matrix on the current leader"
    ssh -o BatchMode=yes "$CONTROLLER" bash -s <"$HERE/scripts/_matrix.sh"
    ;;

  down)
    echo "[spin] destroying ${#VMS[@]} VMs"
    for vm in "${VMS[@]}"; do
      read -r vmid name _ _ _ <<<"$vm"
      api POST "${PROXMOX_API_URL}/nodes/${PROXMOX_NODE}/qemu/${vmid}/status/stop" >/dev/null 2>&1 || true
      sleep 2
      api DELETE "${PROXMOX_API_URL}/nodes/${PROXMOX_NODE}/qemu/${vmid}?purge=1" >/dev/null 2>&1 || true
      echo "  destroyed $name"
    done
    ;;

  *)
    echo "usage: $0 {up|test|down}"
    exit 2
    ;;
esac
