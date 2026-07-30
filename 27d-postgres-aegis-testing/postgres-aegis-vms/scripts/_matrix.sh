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

# _matrix.sh — run the subset of T01/T06/T03/T11 matrix tests on a
# live postgres-aegis cluster (invoked from spin-env.sh test).
#
# Assumes the cluster is up and ansible inventory is in place.
set -euo pipefail

INV=/tmp/aegis-test/inventory/proxmox-secondary-host.yml
LEADER_IP=$(curl -s http://10.0.42.30:8008/cluster 2>/dev/null | \
  python3 -c "import sys,json; d=json.load(sys.stdin); print([m['host'] for m in d['members'] if m.get('role')=='leader'][0])" 2>/dev/null \
  || echo "10.0.42.30")

echo "[matrix] current leader = $LEADER_IP"

# Fetch the aegis_admin password from Bao
SUPER_PW=$(sudo -n cat /opt/dashboard-keys/bao_token 2>/dev/null | \
  { read -r T; BAO_ADDR=https://127.0.0.1:8200 BAO_TOKEN=$T bao kv get -field=value secret/casino/postgres-aegis/super 2>/dev/null; } \
  || echo "")

if [ -z "$SUPER_PW" ]; then
  echo "[matrix] no BAO super pw — will use pgbench via socket on the leader"
fi

# T01
echo "=== T01 baseline OLTP ==="
ansible -i "$INV" -m shell -a "
sudo -u postgres psql -c \"CREATE ROLE bench LOGIN SUPERUSER PASSWORD 'bench';\" 2>/dev/null || true
sudo -u postgres psql -c \"CREATE DATABASE casino OWNER bench;\" 2>/dev/null || true
PGPASSWORD=bench pgbench -h 127.0.0.1 -U bench -i -q -s 20 casino 2>&1 | tail -1
PGPASSWORD=bench pgbench -h 127.0.0.1 -U bench -c 32 -j 4 -T 30 -M prepared casino 2>&1 | grep -E 'tps|latency'
" --limit "pg-shard-a-writer-1,pg-shard-a-reader-1" 2>&1 | grep -E 'tps|latency|CHANGED'

# T06 pgcrypto vs aegis-fallback
echo "=== T06 pgcrypto INSERT 50k ==="
ansible -i "$INV" -m shell -a "
PGPASSWORD=bench psql -h 127.0.0.1 -U bench -d casino -c \"CREATE EXTENSION IF NOT EXISTS pgcrypto;
CREATE TABLE IF NOT EXISTS pii (id bigserial, v bytea);
TRUNCATE pii;
INSERT INTO pii (v) SELECT pgp_sym_encrypt('test'||i, 'key') FROM generate_series(1, 50000) i;\"
PGPASSWORD=bench psql -h 127.0.0.1 -U bench -d casino -c \"SELECT pg_size_pretty(pg_total_relation_size('pii'));\"
" --limit "pg-shard-a-reader-1" 2>&1 | grep -E 'INSERT|MB|CHANGED'

echo "[matrix] done"
