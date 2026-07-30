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

# scripts/add-write-shard.sh — add a new write shard (shard-c, -d, ...)
# Adding a write shard is more invasive than adding a reader: PgCat's hash
# ring expands and part of the existing data must move. This script does it
# in 5 stages, each reversible up to the "flip" step.
#
# Usage:
#   add-write-shard.sh <new-shard-letter> <writer-ip> <reader-1-ip> ... <reader-5-ip>
#
# Stages:
#   1. Provision VMs (1 writer + 5 readers) for the new shard.
#   2. Bootstrap Patroni for scope=shard-<letter>, identical schema + pg_aegis.
#   3. Extend PgCat config: NEW RING with new shard added, but weight=0.
#   4. Run the resharder: for each hash bucket that moves to the new shard,
#      stream-copy affected rows via logical replication (pgoutput) from
#      origin shard → new shard. Use transactional LSN-based cutover per
#      bucket to avoid row loss.
#   5. Flip PgCat weight to normal; delete moved rows from origin shard;
#      delete old hash buckets from origin.

set -euo pipefail

SHARD_LETTER="${1:?usage: $0 <letter> <writer-ip> <reader-1..5-ip>}"
shift
WRITER_IP="${1:?missing writer ip}"
shift
READER_IPS=("$@")
[ "${#READER_IPS[@]}" -ge 1 ] || { echo "need at least 1 reader ip"; exit 2; }

TARGET="${TARGET:-lab-server}"
HERE="$(cd "$(dirname "$0")/.." && pwd)"
INVENTORY="$HERE/inventory/${TARGET}.yml"

echo "[write-scale-out] adding shard-${SHARD_LETTER}  writer=${WRITER_IP}  readers=${READER_IPS[*]}"

# 1. Append new groups to inventory
python3 - "$INVENTORY" "$SHARD_LETTER" "$WRITER_IP" "${READER_IPS[@]}" <<'PY'
import sys, yaml
inv_file, letter, writer_ip, *reader_ips = sys.argv[1:]
inv = yaml.safe_load(open(inv_file))
kids = inv['all']['children']
writer_grp = f'shard_{letter}_writer'
reader_grp = f'shard_{letter}_readers'
kids[writer_grp] = {
  'hosts': {
    f'pg-shard-{letter}-writer-1': {
      'ansible_host': writer_ip, 'vm_cpu': 4, 'vm_ram_mb': 8192,
      'patroni_scope': f'shard-{letter}'
    }
  }
}
kids[reader_grp] = {
  'hosts': {
    f'pg-shard-{letter}-reader-{i+1}': {
      'ansible_host': ip, 'patroni_scope': f'shard-{letter}'
    } for i, ip in enumerate(reader_ips)
  },
  'vars': {'vm_cpu': 2, 'vm_ram_mb': 4096}
}
# expand convenience groups
kids.setdefault('writers', {'children': {}})['children'][writer_grp] = None
kids.setdefault('readers', {'children': {}})['children'][reader_grp] = None
yaml.dump(inv, open(inv_file, 'w'), default_flow_style=False, sort_keys=False)
PY

# 2. Provision + bootstrap
bash "$HERE/libvirt/create-cluster.sh"
ansible-playbook -i "$INVENTORY" "$HERE/ansible/site.yml" \
  --limit "shard_${SHARD_LETTER}_writer,shard_${SHARD_LETTER}_readers" \
  --extra-vars "bao_token=${BAO_TOKEN:?set BAO_TOKEN}"

# 3. Extend PgCat ring (weight=0 for new shard)
ansible-playbook -i "$INVENTORY" "$HERE/ansible/site.yml" \
  --limit pgcat --tags pgcat \
  --extra-vars "bao_token=$BAO_TOKEN pgcat_new_shard_letter=$SHARD_LETTER pgcat_new_shard_weight=0"

# 4. Resharder — move buckets owned by the new shard.
python3 "$HERE/scripts/_reshard.py" --inventory "$INVENTORY" --new-shard "$SHARD_LETTER"

# 5. Flip weight to normal
ansible-playbook -i "$INVENTORY" "$HERE/ansible/site.yml" \
  --limit pgcat --tags pgcat \
  --extra-vars "bao_token=$BAO_TOKEN pgcat_new_shard_letter=$SHARD_LETTER pgcat_new_shard_weight=1"

echo "[write-scale-out] done. shard-${SHARD_LETTER} active."
