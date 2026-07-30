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

# pgbackrest-stanza-b.sh — bootstrap pgbackrest stanza for shard-b and take
# the first full backup.
#
# Run from lab-server (ops-host) after shard-b Patroni cluster is stable:
#   sudo ./pgbackrest-stanza-b.sh
#
# Prerequisites:
#   - /etc/pgbackrest/pgbackrest.conf present on writer-b (.32) with
#     stanza = casino-aegis-b (rendered by pgbackrest Ansible role)
#   - pgbackrest_s3 credentials in OpenBao at
#     secret/casino/postgres-aegis/pgbackrest_s3
#   - writer-b is the Patroni leader (check: patronictl -c /etc/patroni/patroni.yml list)

set -euo pipefail

WRITER_B="10.0.42.32"
SSH_OPTS=(-o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null)
STANZA="casino-aegis-b"

log() { echo "[pgbackrest-stanza-b] $*"; }

check_patroni_leader() {
  log "Verifying writer-b is Patroni leader ..."
  local role
  # SC2029: STANZA intentionally expands on client side before ssh
  # shellcheck disable=SC2029
  role=$(ssh "${SSH_OPTS[@]}" "ansible@${WRITER_B}" \
    "curl -s http://127.0.0.1:8008/master | python3 -c \"import sys,json; print(json.load(sys.stdin).get('role','unknown'))\" 2>/dev/null || echo unknown")
  if [ "$role" != "master" ]; then
    log "ERROR: writer-b returned role='$role'; must be 'master'. Aborting."
    exit 1
  fi
  log "OK — writer-b is leader."
}

stanza_create() {
  log "Running stanza-create for ${STANZA} on writer-b ..."
  # shellcheck disable=SC2029
  ssh "${SSH_OPTS[@]}" "ansible@${WRITER_B}" \
    "sudo -u postgres pgbackrest --stanza=${STANZA} --log-level-console=info stanza-create"
  log "stanza-create complete."
}

stanza_check() {
  log "Verifying stanza integrity ..."
  # shellcheck disable=SC2029
  ssh "${SSH_OPTS[@]}" "ansible@${WRITER_B}" \
    "sudo -u postgres pgbackrest --stanza=${STANZA} check"
  log "stanza check passed."
}

full_backup() {
  log "Starting first full backup for ${STANZA} ..."
  local t0; t0=$(date +%s)
  # shellcheck disable=SC2029
  ssh "${SSH_OPTS[@]}" "ansible@${WRITER_B}" \
    "sudo -u postgres pgbackrest --stanza=${STANZA} --type=full --log-level-console=info backup"
  local t1; t1=$(date +%s)
  log "Full backup complete in $(( t1 - t0 ))s."
}

show_info() {
  log "Backup info:"
  # shellcheck disable=SC2029
  ssh "${SSH_OPTS[@]}" "ansible@${WRITER_B}" \
    "sudo -u postgres pgbackrest --stanza=${STANZA} info"
}

check_patroni_leader
stanza_create
stanza_check
full_backup
show_info

log "Done. shard-b pgbackrest stanza bootstrapped and first backup written."
