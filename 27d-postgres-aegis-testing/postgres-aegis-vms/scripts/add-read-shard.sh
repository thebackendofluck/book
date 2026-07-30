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

# scripts/add-read-shard.sh — add a new read replica to an existing shard.
#
# Usage:
#   add-read-shard.sh <shard> <new-host-ip> [vmid-for-proxmox]
#
# Flow:
#   1. Create VM on target (libvirt or Proxmox) — same template as the
#      existing readers.
#   2. Update inventory (append host to shard_{a,b}_readers).
#   3. Run Ansible bootstrap only against the new host.
#   4. Patroni picks it up via etcd automatically; pg_basebackup streams
#      from the current leader.
#   5. Update HAProxy backend list + reload.
#   6. Verify `pg_stat_replication` on the leader shows the new replica
#      state=streaming.
#   7. Optionally run a small smoke test (T07-read) to confirm lag is bounded.

set -euo pipefail

SHARD="${1:?usage: $0 <shard-a|shard-b> <new-host-ip> [vmid]}"
IP="${2:?usage: $0 <shard> <new-host-ip> [vmid]}"
VMID="${3:-}"
TARGET="${TARGET:-lab-server}"

HERE="$(cd "$(dirname "$0")/.." && pwd)"
INVENTORY="$HERE/inventory/${TARGET}.yml"

[ -f "$INVENTORY" ] || { echo "inventory missing: $INVENTORY"; exit 2; }
[ -n "${BAO_TOKEN:-}" ] || { echo "BAO_TOKEN not set"; exit 3; }

# Next reader index in the shard
NEXT=$(python3 - "$INVENTORY" "$SHARD" <<'PY'
import sys, yaml, re
inv = yaml.safe_load(open(sys.argv[1]))
grp = 'shard_a_readers' if sys.argv[2] == 'shard-a' else 'shard_b_readers'
hosts = (inv['all']['children'][grp].get('hosts') or {})
nums = [int(re.search(r'(\d+)$', h).group(1)) for h in hosts]
print(max(nums + [0]) + 1)
PY
)
NEW_NAME="pg-${SHARD}-reader-${NEXT}"
echo "[scale-out] shard=$SHARD new-host=$NEW_NAME ip=$IP target=$TARGET"

# 1. Provision VM
case "$TARGET" in
  lab-server)
    SSH_PUB="$(cat ~/.ssh/id_rsa.pub 2>/dev/null || cat ~/.ssh/id_ed25519.pub)"
    export SSH_PUB
    # Use the same provision function by calling create-cluster with an ephemeral inventory slice
    TMP=$(mktemp)
    cat >"$TMP" <<EOF
all:
  children:
    tmp:
      hosts:
        $NEW_NAME: {ansible_host: $IP, vm_cpu: 2, vm_ram_mb: 4096}
EOF
    INVENTORY="$TMP" bash "$HERE/libvirt/create-cluster.sh"
    rm -f "$TMP"
    ;;
  secondary-host)
    [ -n "$VMID" ] || { echo "need vmid for Proxmox"; exit 4; }
    PROXMOX_VMID="$VMID" PROXMOX_HOST="$IP" bash "$HERE/proxmox/create-cluster.sh" --add-one "$NEW_NAME"
    ;;
esac

# 2. Wait for SSH
echo "[scale-out] waiting for SSH on $IP"
for _ in $(seq 1 60); do
  if ssh -o BatchMode=yes -o ConnectTimeout=3 "ansible@$IP" true 2>/dev/null; then break; fi
  sleep 5
done

# 3. Append to inventory (idempotent)
python3 - "$INVENTORY" "$SHARD" "$NEW_NAME" "$IP" <<'PY'
import sys, yaml
inv_file = sys.argv[1]
inv = yaml.safe_load(open(inv_file))
grp = 'shard_a_readers' if sys.argv[2] == 'shard-a' else 'shard_b_readers'
hosts = inv['all']['children'][grp].setdefault('hosts', {})
if sys.argv[3] in hosts:
    sys.exit(0)
hosts[sys.argv[3]] = {'ansible_host': sys.argv[4], 'patroni_scope': sys.argv[2]}
yaml.dump(inv, open(inv_file, 'w'), default_flow_style=False, sort_keys=False)
PY

# 4. Bootstrap only the new host
ansible-playbook -i "$INVENTORY" "$HERE/ansible/site.yml" \
  --limit "$NEW_NAME" --tags "luks,pg,patroni,aegis,monitoring" \
  --extra-vars "bao_token=$BAO_TOKEN"

# 5. Wait for pg_stat_replication on leader
LEADER=$(python3 - "$INVENTORY" "$SHARD" <<'PY'
import sys, yaml
inv = yaml.safe_load(open(sys.argv[1]))
grp = 'shard_a_writer' if sys.argv[2] == 'shard-a' else 'shard_b_writer'
h = next(iter(inv['all']['children'][grp]['hosts'].values()))
print(h['ansible_host'])
PY
)
echo "[scale-out] polling pg_stat_replication on leader $LEADER"
for _ in $(seq 1 60); do
  STATE=$(ssh -o BatchMode=yes "ansible@$LEADER" \
    "sudo -u postgres psql -tAc \"SELECT state FROM pg_stat_replication WHERE application_name='$NEW_NAME';\"" 2>/dev/null || echo "")
  [ "$STATE" = "streaming" ] && break
  sleep 5
done
[ "${STATE:-}" = "streaming" ] || { echo "replica never reached streaming state"; exit 5; }
echo "[scale-out] $NEW_NAME streaming from $LEADER"

# 6. Reload HAProxy on pgcat hosts
ansible-playbook -i "$INVENTORY" "$HERE/ansible/site.yml" \
  --limit pgcat --tags haproxy --extra-vars "bao_token=$BAO_TOKEN"

# 7. Smoke test — does the new replica answer reads?
REPLAY=$(ssh -o BatchMode=yes "ansible@$IP" \
  "sudo -u postgres psql -tAc \"SELECT pg_is_in_recovery(), pg_last_wal_replay_lsn() IS NOT NULL;\"")
echo "[scale-out] smoke: $REPLAY"
echo "[scale-out] done"
