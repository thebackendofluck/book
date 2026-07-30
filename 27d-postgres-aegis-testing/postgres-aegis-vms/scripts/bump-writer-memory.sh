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

# bump-writer-memory.sh — increase RAM on both shard writers and re-run
# pgbench at -c 500 to saturate the connection pool.
#
# Context: pgbench -c 500 OOM-killed at 4 GB writer RAM. Bump to 12 GB.
# Proxmox API is at 10.0.0.10:8006. Auth via API token env vars.
#
# Usage:
#   export PROXMOX_API_TOKEN_ID="root@pam!claude"
#   export PROXMOX_API_TOKEN_SECRET="<secret>"
#   sudo ./bump-writer-memory.sh [<ram-mb>] [<pgbench-clients>]
#
# Safe: only changes RAM (hotplug not available — requires VM reboot).
# Writers are drained (Patroni will elect a new leader) before reboot.

set -euo pipefail

PROXMOX="10.0.0.10"
PROXMOX_NODE="secondary-host"
SSH_OPTS=(-o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null)
RAM_MB="${1:-12288}"
PGBENCH_CLIENTS="${2:-500}"
PGBENCH_THREADS=16
PGBENCH_SECONDS=60

WRITERS=(
  "2010:10.0.42.30:pg-shard-a-writer-1"
  "2012:10.0.42.32:pg-shard-b-writer-1"
)

TOKEN_ID="${PROXMOX_API_TOKEN_ID:?must be set}"
TOKEN_SECRET="${PROXMOX_API_TOKEN_SECRET:?must be set}"
TICKET_HEADER="Authorization: PVEAPIToken=${TOKEN_ID}=${TOKEN_SECRET}"

log() { echo "[bump-writer-memory] $*"; }

api_put() {
  local path="$1"; shift
  curl -sk -X PUT \
    -H "${TICKET_HEADER}" \
    -H "Content-Type: application/json" \
    "https://${PROXMOX}/api2/json/nodes/${PROXMOX_NODE}${path}" "$@"
}

api_post() {
  local path="$1"
  curl -sk -X POST \
    -H "${TICKET_HEADER}" \
    "https://${PROXMOX}/api2/json/nodes/${PROXMOX_NODE}${path}"
}

drain_writer() {
  local ip="$1" name="$2"
  log "Draining ${name} (${ip}) — Patroni will elect replica as leader ..."
  # shellcheck disable=SC2029
  ssh "${SSH_OPTS[@]}" "ansible@${ip}" \
    "sudo patronictl -c /etc/patroni/patroni.yml switchover --master ${name} --force" 2>/dev/null \
    || log "WARN: switchover returned non-zero (may have already switched)"
  sleep 10
}

set_ram() {
  local vmid="$1"
  log "Setting VMID ${vmid} memory to ${RAM_MB} MB ..."
  api_put "/qemu/${vmid}/config" --data-urlencode "memory=${RAM_MB}" | python3 -c \
    "import sys,json; r=json.load(sys.stdin); print('  PVE response:', r.get('data','ok'))"
}

reboot_vm() {
  local vmid="$1" ip="$2" name="$3"
  log "Rebooting VMID ${vmid} (${name}) ..."
  api_post "/qemu/${vmid}/status/reboot" | python3 -c \
    "import sys,json; print('  UPID:', json.load(sys.stdin).get('data','?'))"
  log "Waiting 60s for reboot + Patroni re-join ..."
  sleep 60
  local state
  state=$(curl -sk -H "${TICKET_HEADER}" \
    "https://${PROXMOX}/api2/json/nodes/${PROXMOX_NODE}/qemu/${vmid}/status/current" \
    | python3 -c "import sys,json; print(json.load(sys.stdin)['data']['status'])")
  log "VMID ${vmid} state after reboot: ${state}"
}

wait_patroni() {
  local ip="$1" name="$2"
  log "Waiting for Patroni on ${name} to rejoin cluster ..."
  local i
  for i in $(seq 1 12); do
    if curl -sf --max-time 3 "http://${ip}:8008/health" >/dev/null 2>&1; then
      log "  ${name} healthy after ${i}×5s"
      return 0
    fi
    sleep 5
  done
  log "WARN: ${name} Patroni not responding after 60s — check manually"
}

run_pgbench() {
  local ip="$1" name="$2"
  log "Running pgbench -c ${PGBENCH_CLIENTS} -j ${PGBENCH_THREADS} -T ${PGBENCH_SECONDS} on ${name} ..."
  ssh "${SSH_OPTS[@]}" "ansible@${ip}" sudo -u postgres pgbench \
    -h 127.0.0.1 -U aegis_admin -d casino \
    -c "${PGBENCH_CLIENTS}" -j "${PGBENCH_THREADS}" -T "${PGBENCH_SECONDS}" \
    -P 10 --protocol=prepared \
    2>&1 | tee -a "/tmp/pgbench-c500-${name}.log"
  log "pgbench log: /tmp/pgbench-c500-${name}.log"
}

for entry in "${WRITERS[@]}"; do
  IFS=: read -r vmid ip name <<< "${entry}"
  drain_writer "${ip}" "${name}"
  set_ram "${vmid}"
  reboot_vm "${vmid}" "${ip}" "${name}"
  wait_patroni "${ip}" "${name}"
done

log "All writers rebooted with ${RAM_MB} MB RAM."
log "Starting pgbench at -c ${PGBENCH_CLIENTS} ..."

for entry in "${WRITERS[@]}"; do
  IFS=: read -r vmid ip name <<< "${entry}"
  run_pgbench "${ip}" "${name}"
done

log "Done."
