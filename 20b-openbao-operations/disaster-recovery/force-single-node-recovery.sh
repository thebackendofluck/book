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

# Walk through the quorum-loss recovery procedure in the sandbox.
#
# This script is safe to run repeatedly against the throw-away sandbox; it
# WILL refuse to run if BAO_ADDR points at anything on port 8200 (which is
# the default production port on ops-host). The whole point of the exercise
# is on-call muscle memory: type the commands in the sandbox, not in
# production.

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

# This check runs BEFORE lib/common.sh is sourced, on purpose. It reads what
# the operator exported, which is the only thing worth checking. The earlier
# version of this guard sat after the source line, and common.sh assigned
# BAO_ADDR itself, so the case statement was matching the library's sandbox
# default and could never fire no matter what the operator had set.
case "${BAO_ADDR:-}" in
  *:8200*|*:8201*)
    printf '[20b] ERROR: BAO_ADDR=%s points at production -- refusing to run DR drill\n' \
      "$BAO_ADDR" >&2
    exit 1
    ;;
esac

# shellcheck source=../lib/common.sh
source "$SCRIPT_DIR/../lib/common.sh"

# Second layer: ensure_sandbox now parses host and port out of the operator's
# BAO_ADDR and rejects anything that is not loopback on SANDBOX_PORT.
ensure_sandbox

# This script deletes and rewrites raft state, so refuse to do it anywhere but
# under the sandbox directory. SANDBOX_DIR is also operator-supplied.
case "$SANDBOX_DIR" in
  /opt/openbao*|/etc/openbao*|/var/lib/openbao*|/var/log/openbao*)
    die "SANDBOX_DIR=$SANDBOX_DIR is a production path -- refusing to run DR drill"
    ;;
esac

RAFT_DIR="$SANDBOX_DIR/data/raft"
if [[ ! -d "$RAFT_DIR" ]]; then
  log "sandbox is using the file backend; DR drill only applies to raft."
  log "start a raft-backed sandbox variant before running this script:"
  log "  SANDBOX_BACKEND=raft $SCRIPT_DIR/../setup/start-sandbox.sh"
  exit 0
fi

log "stopping sandbox to simulate lost quorum"
"$SCRIPT_DIR/../setup/stop-sandbox.sh" --keep

cat > "$RAFT_DIR/peers.json" <<EOF
[
  {
    "id": "ops-host-sandbox",
    "address": "127.0.0.1:$((SANDBOX_PORT + 1))",
    "non_voter": false
  }
]
EOF

log "wrote peers.json to force single-node recovery:"
cat "$RAFT_DIR/peers.json"

log "restarting sandbox -- raft will honour peers.json and reduce to a single node"
"$SCRIPT_DIR/../setup/start-sandbox.sh"

log "verifying raft peer list"
load_root_token
"$BAO_BIN" operator raft list-peers || log "  (raft list-peers may fail briefly while raft reconverges)"

log "DR drill complete."
log "In production, the next step would be 'bao operator raft join https://new-peer:8200'"
log "on each replacement node to restore the intended cluster topology."
