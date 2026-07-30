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

# Shared helpers for chapter-20b OpenBao scripts.
#
# Source this from any sibling script via:
#     SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
#     # shellcheck source=../lib/common.sh
#     source "$SCRIPT_DIR/../lib/common.sh"
#
# Every helper uses `bao` (the OpenBao CLI) rather than `vault` so that the
# scripts work against the open-source fork shipped by CNCF without any
# aliasing. When OpenBao is installed via the apt package `openbao-hsm`, the
# binary is `bao`; on systems that still use the original vault name, set
# BAO_BIN=vault before sourcing.

set -euo pipefail

: "${BAO_BIN:=bao}"
: "${SANDBOX_PORT:=18300}"
: "${SANDBOX_DIR:=/tmp/openbao-sandbox-20b}"
: "${SANDBOX_LOG:=$SANDBOX_DIR/server.log}"
: "${SANDBOX_PID_FILE:=$SANDBOX_DIR/server.pid}"
: "${SANDBOX_INIT_FILE:=$SANDBOX_DIR/init.json}"

log()  { printf '[20b] %s\n' "$*" >&2; }
die()  { printf '[20b] ERROR: %s\n' "$*" >&2; exit 1; }

# Ports the production OpenBao cluster listens on (see chapter 20's
# setup-openbao-cluster.sh: BAO_PORT / BAO_CLUSTER_PORT).
PRODUCTION_PORTS=(8200 8201)
SANDBOX_PORT_MIN=18300
SANDBOX_PORT_MAX=18399

_is_production_port() {
  local candidate="$1" prod
  for prod in "${PRODUCTION_PORTS[@]}"; do
    if [[ "$candidate" == "$prod" ]]; then
      return 0
    fi
  done
  return 1
}

# SANDBOX_PORT is operator-supplied, so validate it here rather than trusting
# it. Setting SANDBOX_PORT=8200 used to be enough to make every "sandbox only"
# guard in this tree accept the production cluster, because the guards compared
# the address against SANDBOX_PORT instead of against a known-bad list.
[[ "$SANDBOX_PORT" =~ ^[0-9]+$ ]] \
  || die "SANDBOX_PORT must be numeric, got: $SANDBOX_PORT"
if _is_production_port "$SANDBOX_PORT"; then
  die "SANDBOX_PORT=$SANDBOX_PORT is a production OpenBao port — refusing to call that a sandbox"
fi
(( SANDBOX_PORT >= SANDBOX_PORT_MIN && SANDBOX_PORT <= SANDBOX_PORT_MAX )) \
  || die "SANDBOX_PORT=$SANDBOX_PORT is outside the sandbox range ${SANDBOX_PORT_MIN}-${SANDBOX_PORT_MAX}"

# Default BAO_ADDR, but never overwrite an operator-supplied one. Assigning it
# unconditionally is what defeated the production guard in
# force-single-node-recovery.sh: by the time that script inspected BAO_ADDR it
# was reading this file's own sandbox default, so a BAO_ADDR exported by the
# operator was silently discarded and the guard could not fire. Defaulting
# instead of assigning means ensure_sandbox below now judges what the operator
# actually asked for.
: "${BAO_ADDR:=http://127.0.0.1:${SANDBOX_PORT}}"
export BAO_ADDR

require_cmd() {
  command -v "$1" >/dev/null 2>&1 || die "required command not found: $1"
}

# Refuse to operate against anything but the loopback sandbox.
#
# Parses host and port out of BAO_ADDR rather than pattern-matching the whole
# URL. The old glob (*:18*) matched any address with ":18" anywhere in it, so
# https://bao-01.prod:1855 and https://bao-01:8200/v1/x18 both passed.
ensure_sandbox() {
  local addr="$BAO_ADDR" rest host port

  [[ "$addr" == *"://"* ]] || die "BAO_ADDR=$addr is not a URL — refusing"
  rest=${addr#*://}
  rest=${rest%%/*}

  if [[ "$rest" == \[*\]:* ]]; then      # bracketed IPv6 literal
    host=${rest%\]:*}
    host=${host#\[}
    port=${rest##*\]:}
  else
    host=${rest%%:*}
    port=${rest##*:}
    [[ "$port" != "$rest" ]] || die "BAO_ADDR=$addr has no explicit port — refusing"
  fi

  case "$host" in
    127.0.0.1|localhost|::1) ;;
    *) die "BAO_ADDR=$addr is not loopback — refusing to touch a non-local OpenBao" ;;
  esac

  [[ "$port" =~ ^[0-9]+$ ]] || die "BAO_ADDR=$addr has a non-numeric port — refusing"
  if _is_production_port "$port"; then
    die "BAO_ADDR=$addr uses production port ${port} — refusing"
  fi

  # The port must fall inside the sandbox range. Range rather than exact equality
  # with SANDBOX_PORT because ops/verify-raft-snapshot.sh legitimately runs a
  # second throw-away instance at SANDBOX_PORT+10; the range still excludes 8200,
  # 8201 and every other port in existence, which is what the guard is for.
  (( port >= SANDBOX_PORT_MIN && port <= SANDBOX_PORT_MAX )) \
    || die "BAO_ADDR=$addr port ${port} is outside the sandbox range ${SANDBOX_PORT_MIN}-${SANDBOX_PORT_MAX} — refusing"
}

# Retry a command up to N times with a small delay — useful while the sandbox
# boots and the raft/file backend becomes ready.
retry() {
  local tries=$1 delay=$2
  shift 2
  local attempt=1
  while (( attempt <= tries )); do
    if "$@"; then return 0; fi
    sleep "$delay"
    (( attempt++ ))
  done
  return 1
}

# Load the sandbox root token from the init file written by setup.
load_root_token() {
  [[ -f "$SANDBOX_INIT_FILE" ]] || die "init file not found: $SANDBOX_INIT_FILE (run setup first)"
  local token
  token=$(python3 -c "import json,sys; print(json.load(open('$SANDBOX_INIT_FILE'))['root_token'])")
  [[ -n "$token" ]] || die "could not parse root_token from $SANDBOX_INIT_FILE"
  export BAO_TOKEN="$token"
}
