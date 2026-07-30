#!/usr/bin/env bash
# Companion code for "The Backend of Luck" - Chapter 20b, OpenBao Operations.
# https://thebackendofluck.com | https://github.com/thebackendofluck/book
# SPDX-License-Identifier: Apache-2.0
#
# FOR TESTING AND EVALUATION ONLY. NOT FOR PRODUCTION USE.
# Published to demonstrate the patterns explained in the book. This code is
# not certified for real-money gaming: operating a gambling platform requires
# your own licence, independent test-lab certification (GLI, eCOGRA or
# equivalent) and regulator approval.

# monitor-seal-status.sh -- poll the OpenBao seal-status endpoint and emit
# liveness signals to syslog (consumed by Wazuh) and optionally to a
# Prometheus textfile collector.
#
# Exit codes:
#   0  healthy   (initialized=true, sealed=false)
#   1  sealed    (initialized=true, sealed=true)  -- P1 outage-equivalent
#   2  unreachable (network error, TLS error, non-2xx response, parse error)
#
# Usage:
#   monitor-seal-status.sh [--addr URL] [--interval SECONDS]
#                          [--prometheus PATH] [--once]
#
# Defaults (override with flags or env):
#   BAO_ADDR     https://127.0.0.1:8200
#   INTERVAL     30
#   PROM_FILE    (unset; disables Prometheus textfile output)
#
# Designed to be run from a systemd timer (every 30s) OR as a long-lived
# loop (--interval). For systemd timer mode use --once.
#
# Syslog tag: bao-seal-monitor
# Wazuh rule: scripts/chapter-20b/ops/wazuh/bao-seal-rule.xml
#
# shellcheck disable=SC2310  # we want set -e to apply even inside `if`

set -euo pipefail

BAO_ADDR_DEFAULT="${BAO_ADDR:-https://127.0.0.1:8200}"
INTERVAL_DEFAULT=30
LOG_TAG="bao-seal-monitor"

addr="$BAO_ADDR_DEFAULT"
interval="$INTERVAL_DEFAULT"
prom_file=""
once=0

while [[ $# -gt 0 ]]; do
  case "$1" in
    --addr)        addr="$2"; shift 2 ;;
    --interval)    interval="$2"; shift 2 ;;
    --prometheus)  prom_file="$2"; shift 2 ;;
    --once)        once=1; shift ;;
    -h|--help)
      sed -n '2,25p' "$0"
      exit 0
      ;;
    *)
      echo "unknown flag: $1" >&2
      exit 64
      ;;
  esac
done

log() {
  # Emit to both stderr (for systemd journal capture) and syslog (for Wazuh).
  local level="$1"; shift
  local msg="$*"
  printf '%s [%s] %s\n' "$(date -u '+%Y-%m-%dT%H:%M:%SZ')" "$level" "$msg" >&2
  if command -v logger >/dev/null 2>&1; then
    logger -t "$LOG_TAG" -p "user.${level}" -- "$msg" || true
  fi
}

write_prom() {
  # Atomic-write a Prometheus textfile so node_exporter never races on a
  # half-written file. Caller is responsible for the directory existing.
  [[ -z "$prom_file" ]] && return 0
  local sealed="$1" initialized="$2" reachable="$3" status_numeric="$4"
  local tmp="${prom_file}.tmp.$$"
  {
    printf '# HELP bao_seal_status 1=sealed, 0=unsealed, -1=unreachable\n'
    printf '# TYPE bao_seal_status gauge\n'
    printf 'bao_seal_status{addr="%s"} %s\n' "$addr" "$status_numeric"
    printf '# HELP bao_initialized 1=initialized, 0=not, -1=unknown\n'
    printf '# TYPE bao_initialized gauge\n'
    printf 'bao_initialized{addr="%s"} %s\n' "$addr" "$initialized"
    printf '# HELP bao_unreachable 1=unreachable, 0=reachable\n'
    printf '# TYPE bao_unreachable gauge\n'
    printf 'bao_unreachable{addr="%s"} %s\n' "$addr" "$reachable"
    printf '# HELP bao_seal_probe_timestamp_seconds unix epoch of last probe\n'
    printf '# TYPE bao_seal_probe_timestamp_seconds gauge\n'
    printf 'bao_seal_probe_timestamp_seconds{addr="%s"} %s\n' "$addr" "$(date +%s)"
    if [[ "$sealed" != "unknown" ]]; then
      printf '# HELP bao_sealed_bool 1=sealed, 0=unsealed\n'
      printf '# TYPE bao_sealed_bool gauge\n'
      printf 'bao_sealed_bool{addr="%s"} %s\n' "$addr" "$sealed"
    fi
  } > "$tmp"
  mv -f "$tmp" "$prom_file"
}

probe_once() {
  local body http_code curl_rc
  # -k: many production setups use a self-signed CA whose chain isn't
  # in the system store on the host running the probe; the seal endpoint
  # itself is unauthenticated so the probe doesn't expose secrets either way.
  # Override by setting CA_BUNDLE in env (then we drop -k).
  local curl_args=(--silent --show-error --max-time 5 -o /tmp/bao-seal-probe.$$.json -w '%{http_code}')
  if [[ -n "${CA_BUNDLE:-}" ]]; then
    curl_args+=(--cacert "$CA_BUNDLE")
  else
    curl_args+=(-k)
  fi

  if ! http_code=$(curl "${curl_args[@]}" "${addr}/v1/sys/seal-status" 2>/dev/null); then
    curl_rc=$?
    log err "UNREACHABLE addr=${addr} curl_rc=${curl_rc}"
    write_prom unknown -1 1 -1
    rm -f "/tmp/bao-seal-probe.$$.json"
    return 2
  fi

  if [[ "$http_code" != "200" && "$http_code" != "429" && "$http_code" != "473" && "$http_code" != "503" ]]; then
    log err "UNREACHABLE addr=${addr} http=${http_code}"
    write_prom unknown -1 1 -1
    rm -f "/tmp/bao-seal-probe.$$.json"
    return 2
  fi

  body=$(cat "/tmp/bao-seal-probe.$$.json")
  rm -f "/tmp/bao-seal-probe.$$.json"

  # Extract sealed + initialized from JSON without depending on jq (the host
  # the probe runs on may be minimal). Python is in every modern distro.
  local parsed
  if ! parsed=$(printf '%s' "$body" | python3 -c '
import json, sys
try:
    d = json.load(sys.stdin)
    print("{}|{}|{}".format(
        "1" if d.get("sealed") else "0",
        "1" if d.get("initialized") else "0",
        d.get("version", "unknown"),
    ))
except Exception as e:
    print("ERR|{}".format(e))
    sys.exit(1)
' 2>/dev/null); then
    log err "UNREACHABLE addr=${addr} parse_error body=${body:0:200}"
    write_prom unknown -1 1 -1
    return 2
  fi

  local sealed initialized version
  IFS='|' read -r sealed initialized version <<< "$parsed"

  if [[ "$sealed" == "1" ]]; then
    log crit "SEALED addr=${addr} initialized=${initialized} version=${version} -- P1 outage-equivalent, page on-call"
    write_prom 1 "$initialized" 0 1
    return 1
  fi

  log info "HEALTHY addr=${addr} sealed=false initialized=${initialized} version=${version}"
  write_prom 0 "$initialized" 0 0
  return 0
}

if [[ -n "$prom_file" ]]; then
  prom_dir=$(dirname "$prom_file")
  if [[ ! -d "$prom_dir" ]]; then
    log err "prometheus directory does not exist: $prom_dir"
    exit 64
  fi
fi

if [[ "$once" -eq 1 ]]; then
  probe_once
  exit $?
fi

# Long-lived loop mode (rare; systemd timer is preferred).
last_rc=0
while true; do
  if probe_once; then
    last_rc=0
  else
    last_rc=$?
  fi
  sleep "$interval"
done

# Unreachable, but keep shellcheck happy.
exit "$last_rc"
