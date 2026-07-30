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

# pgcat-patroni-watch.sh — watches Patroni REST APIs on both shards and
# triggers a PgCat config reload whenever leadership changes.
#
# How it works:
#   1. Every 10 s, poll /master on each shard's nodes (writer IPs).
#   2. If the leader IP has changed since last check, rewrite /etc/pgcat/pgcat.toml
#      with the new primary_host and reload PgCat via the admin socket.
#   3. Log every transition to /var/log/pgcat-patroni-watch.log.
#
# PgCat admin API: SHOW SERVERS; RELOAD; on the admin port (9930).
#
# Install as a systemd service:
#   sudo cp pgcat-patroni-watch.sh /usr/local/sbin/
#   sudo systemctl enable --now pgcat-patroni-watch.service
#
# Assumptions:
#   - PgCat is running on this host (10.0.42.50).
#   - pgcat.toml is at /etc/pgcat/pgcat.toml (rendered by Ansible).
#   - psql is available (used for admin socket).

set -euo pipefail

PGCAT_ADMIN_HOST="127.0.0.1"
PGCAT_ADMIN_PORT="9930"
PGCAT_ADMIN_USER="pgcat_admin"
PGCAT_CONF="/etc/pgcat/pgcat.toml"
LOG="/var/log/pgcat-patroni-watch.log"
POLL_INTERVAL=10

declare -A SHARD_NODES
SHARD_NODES[shard-a]="10.0.42.30 10.0.42.31"
SHARD_NODES[shard-b]="10.0.42.32 10.0.42.33"

declare -A LAST_LEADER

log() { echo "$(date -u +%FT%TZ) $*" | tee -a "${LOG}"; }

get_leader() {
  local nodes="$1"
  for ip in ${nodes}; do
    local role
    role=$(curl -sf --max-time 2 "http://${ip}:8008/master" \
      | python3 -c "import sys,json; print(json.load(sys.stdin)['role'])" 2>/dev/null) || continue
    if [ "${role}" = "master" ]; then
      echo "${ip}"
      return 0
    fi
  done
  echo ""
}

reload_pgcat() {
  log "Reloading PgCat via admin socket ..."
  PGPASSWORD="${PGCAT_ADMIN_PW:-}" psql \
    -h "${PGCAT_ADMIN_HOST}" -p "${PGCAT_ADMIN_PORT}" \
    -U "${PGCAT_ADMIN_USER}" -d pgcat \
    -c "RELOAD;" 2>&1 | tee -a "${LOG}" || log "WARN: PgCat reload failed (continuing)"
}

update_primary_in_conf() {
  local shard="$1" new_leader="$2"
  log "Updating ${shard} primary_host → ${new_leader} in ${PGCAT_CONF}"
  # The toml has a line like: servers = [["<ip>", 5432, "primary"], ...]
  # We update just the shard's section by matching the shard comment header.
  # Safer approach: rewrite via python3 (toml not available as pkg on Ubuntu Noble).
  python3 - "${PGCAT_CONF}" "${shard}" "${new_leader}" <<'PY'
import sys, re, pathlib

conf_path, shard_name, new_ip = sys.argv[1], sys.argv[2], sys.argv[3]
text = pathlib.Path(conf_path).read_text()

# Matches the primary entry within the right shard block:
# ["10.0.42.30", 5432, "primary"]
# Strategy: find the shard-specific section header comment and then
# replace the first "primary" server entry that follows it.
section_marker = f'# shard {shard_name}'

# Build pattern that matches from the shard-specific pool header to
# the first "primary" server line.
pat = re.compile(
    r'(\[pools\.casino\.shards\.\d+\][^\[]*?servers\s*=\s*\[.*?)"([0-9.]+)"(\s*,\s*\d+\s*,\s*"primary")',
    re.DOTALL
)

replaced = 0
def replacer(m):
    global replaced
    replaced += 1
    return f'{m.group(1)}"{new_ip}"{m.group(3)}'

new_text = pat.sub(replacer, text, count=1)
if replaced:
    pathlib.Path(conf_path).write_text(new_text)
    print(f'Updated {shard_name} primary → {new_ip}')
else:
    print(f'WARN: could not locate primary entry for {shard_name}')
PY
}

log "pgcat-patroni-watch starting. Poll interval: ${POLL_INTERVAL}s"

while true; do
  for shard in "${!SHARD_NODES[@]}"; do
    nodes="${SHARD_NODES[$shard]}"
    leader=$(get_leader "${nodes}")
    if [ -z "${leader}" ]; then
      log "WARN: no leader found for ${shard} — cluster may be electing"
      continue
    fi
    prev="${LAST_LEADER[$shard]:-}"
    if [ "${leader}" != "${prev}" ]; then
      log "LEADER CHANGE ${shard}: '${prev}' → '${leader}'"
      LAST_LEADER[$shard]="${leader}"
      if [ -n "${prev}" ]; then
        update_primary_in_conf "${shard}" "${leader}"
        reload_pgcat
      else
        log "Initial leader for ${shard}: ${leader}"
      fi
    fi
  done
  sleep "${POLL_INTERVAL}"
done
