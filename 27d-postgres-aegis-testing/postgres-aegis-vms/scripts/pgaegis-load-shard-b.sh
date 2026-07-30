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

# pgaegis-load-shard-b.sh — enable pg_aegis extension on the shard-b
# Patroni cluster.
#
# The .so must already be deployed via Ansible (pg-aegis role ran against
# shard-b nodes). This script only does the runtime activation:
#   1. ALTER SYSTEM to add pg_aegis to shared_preload_libraries
#   2. patronictl restart so PG reloads without etcd failover
#   3. CREATE EXTENSION IF NOT EXISTS pg_aegis
#   4. aegis_generate_key('player_pii_key') if not already present
#
# Run from lab-server after: ansible-playbook site.yml -i inventory/proxmox-secondary-host.yml
#                             --limit pg-shard-b-writer-1,pg-shard-b-reader-1
#                             --tags pg-aegis

set -euo pipefail

WRITER_B="10.0.42.32"
READER_B="10.0.42.33"
SSH_OPTS=(-o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null)
PATRONI_CFG="/etc/patroni/patroni.yml"
CLUSTER="shard-b"

log() { echo "[pgaegis-shard-b] $*"; }

check_so() {
  log "Checking for pg_aegis.so on writer-b ..."
  ssh "${SSH_OPTS[@]}" "ansible@${WRITER_B}" \
    "ls -la /usr/lib/postgresql/16/lib/pg_aegis.so 2>/dev/null || echo 'MISSING'"
}

alter_system() {
  log "Setting shared_preload_libraries on writer-b ..."
  ssh "${SSH_OPTS[@]}" "ansible@${WRITER_B}" \
    sudo -u postgres psql -d casino -c \
    "ALTER SYSTEM SET shared_preload_libraries = 'pg_stat_statements,pgaudit,pg_aegis';"
  log "ALTER SYSTEM done — will take effect after restart."
}

patroni_restart() {
  local host="$1"
  local member="$2"
  log "Restarting Patroni on ${member} (${host}) ..."
  # patronictl restart schedules a graceful PG restart that Patroni controls,
  # preserving HA state — safer than systemctl restart patroni.
  # shellcheck disable=SC2029
  ssh "${SSH_OPTS[@]}" "ansible@${host}" \
    "sudo patronictl -c ${PATRONI_CFG} restart ${CLUSTER} ${member} --force"
  log "Waiting 15s for PG to come back ..."
  sleep 15
}

create_extension() {
  log "Creating pg_aegis extension on shard-b ..."
  ssh "${SSH_OPTS[@]}" "ansible@${WRITER_B}" sudo -u postgres psql -d casino <<'SQL'
DO $$
BEGIN
  BEGIN
    CREATE EXTENSION IF NOT EXISTS pg_aegis;
    RAISE NOTICE 'pg_aegis loaded: %', (SELECT aegis_version());
  EXCEPTION WHEN OTHERS THEN
    RAISE NOTICE 'pg_aegis unavailable: %; falling back to pgcrypto', SQLERRM;
    CREATE EXTENSION IF NOT EXISTS pgcrypto;
  END;
END$$;
SQL
}

generate_key() {
  log "Generating player_pii_key on shard-b (idempotent) ..."
  ssh "${SSH_OPTS[@]}" "ansible@${WRITER_B}" sudo -u postgres psql -d casino -c \
    "SELECT aegis_generate_key('player_pii_key')
     WHERE NOT EXISTS (SELECT 1 FROM pg_aegis_keys WHERE key_name = 'player_pii_key');" \
    2>/dev/null || log "WARN: aegis_generate_key not available (pgcrypto fallback in use)"
}

check_so
alter_system
patroni_restart "${WRITER_B}" "pg-shard-b-writer-1"
patroni_restart "${READER_B}" "pg-shard-b-reader-1"
create_extension
generate_key

log "pg_aegis activation complete on shard-b."
log "Verify: ssh ansible@${WRITER_B} 'sudo -u postgres psql -d casino -c \"SELECT aegis_version();\"'"
