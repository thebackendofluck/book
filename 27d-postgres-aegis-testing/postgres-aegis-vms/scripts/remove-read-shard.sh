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

# scripts/remove-read-shard.sh — drain and remove a read replica.
#
# Usage:
#   remove-read-shard.sh <replica-host>
#
# Flow:
#   1. Mark replica down in HAProxy (drain) — stops new connections.
#   2. Wait for active transactions to end (max 120 s).
#   3. Stop Patroni on the node; Patroni removes itself from the cluster.
#   4. Remove the host from inventory.
#   5. Reload HAProxy; destroy VM (only if --destroy-vm passed).

set -euo pipefail

HOST="${1:?usage: $0 <host-shortname> [--destroy-vm]}"
DESTROY_VM="no"
[ "${2:-}" = "--destroy-vm" ] && DESTROY_VM="yes"

TARGET="${TARGET:-lab-server}"
HERE="$(cd "$(dirname "$0")/.." && pwd)"
INVENTORY="$HERE/inventory/${TARGET}.yml"

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

echo "[scale-in] $HOST ($IP)"

# 1. Drain in HAProxy
for PGCAT in $(python3 -c "
import yaml
inv = yaml.safe_load(open('$INVENTORY'))
for h, hv in inv['all']['children']['pgcat']['hosts'].items():
    print(hv['ansible_host'])
"); do
  ssh -o BatchMode=yes "ansible@$PGCAT" "echo 'set server $HOST state drain' | sudo socat stdio unix-connect:/run/haproxy/admin.sock" || true
done

# 2. Wait for active sessions to wind down
for _ in $(seq 1 24); do
  ACTIVE=$(ssh -o BatchMode=yes "ansible@$IP" \
    "sudo -u postgres psql -tAc \"SELECT count(*) FROM pg_stat_activity WHERE state='active' AND pid<>pg_backend_pid();\"" 2>/dev/null || echo "1")
  [ "$ACTIVE" = "0" ] && break
  sleep 5
done

# 3. Stop Patroni
ssh -o BatchMode=yes "ansible@$IP" "sudo systemctl stop patroni"

# 4. Remove from inventory
python3 - "$INVENTORY" "$HOST" <<'PY'
import sys, yaml
inv = yaml.safe_load(open(sys.argv[1]))
for grp in inv['all']['children'].values():
    hosts = (grp or {}).get('hosts') or {}
    if sys.argv[2] in hosts:
        del hosts[sys.argv[2]]
yaml.dump(inv, open(sys.argv[1], 'w'), default_flow_style=False, sort_keys=False)
PY

# 5. Reload HAProxy
ansible-playbook -i "$INVENTORY" "$HERE/ansible/site.yml" \
  --limit pgcat --tags haproxy --extra-vars "bao_token=${BAO_TOKEN:-}"

# 6. Optional: destroy VM
if [ "$DESTROY_VM" = "yes" ]; then
  case "$TARGET" in
    lab-server)
      # shellcheck disable=SC2029  # HOST intentionally expands on client before ssh
      ssh lab-server "virsh -c qemu:///system destroy '$HOST' 2>/dev/null; virsh -c qemu:///system undefine '$HOST' --remove-all-storage 2>/dev/null" || true ;;
    secondary-host)
      bash "$HERE/proxmox/create-cluster.sh" --destroy-one "$HOST" ;;
  esac
fi

echo "[scale-in] done"
