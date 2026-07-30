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

# Start an isolated OpenBao sandbox for chapter-20b exercises.
#
# This NEVER touches the production openbao.service. It:
#   1. Writes a minimal config to $SANDBOX_DIR/config.hcl using the file
#      backend (simpler than raft for ephemeral test data).
#   2. Starts `bao server` under nohup, bound to 127.0.0.1:$SANDBOX_PORT.
#   3. Initialises with Shamir seal (1 key share, threshold 1 — fine for a
#      throw-away sandbox; production uses 5-of-3 per chapter 20).
#   4. Writes the root token and unseal key to $SANDBOX_INIT_FILE.
#
# To stop: ./stop-sandbox.sh

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=../lib/common.sh
source "$SCRIPT_DIR/../lib/common.sh"

require_cmd "$BAO_BIN"
require_cmd curl
require_cmd python3

ensure_sandbox

if [[ -f "$SANDBOX_PID_FILE" ]] && kill -0 "$(cat "$SANDBOX_PID_FILE")" 2>/dev/null; then
  log "sandbox already running (pid $(cat "$SANDBOX_PID_FILE"))"
  exit 0
fi

log "creating sandbox at $SANDBOX_DIR"
mkdir -p "$SANDBOX_DIR/data" "$SANDBOX_DIR/audit"
chmod 700 "$SANDBOX_DIR"

cat > "$SANDBOX_DIR/config.hcl" <<EOF
# chapter-20b sandbox — throw-away OpenBao instance, NOT for production
# Note: disable_mlock is not a valid directive in OpenBao 2.5+; mlock is
# controlled per-storage-backend or via the listener config instead.
ui = false

storage "file" {
  path = "$SANDBOX_DIR/data"
}

listener "tcp" {
  address     = "127.0.0.1:$SANDBOX_PORT"
  tls_disable = true
}

# OpenBao 2.5+ requires audit devices to be declared in config, not enabled
# via the API. The production ops-host cluster follows the same pattern.
audit "file" "file_audit" {
  options {
    file_path = "$SANDBOX_DIR/audit/audit.log"
    log_raw   = "false"
  }
}

api_addr     = "http://127.0.0.1:$SANDBOX_PORT"
cluster_addr = "http://127.0.0.1:$((SANDBOX_PORT + 1))"
log_level    = "info"
EOF

log "starting bao server on 127.0.0.1:$SANDBOX_PORT"
nohup "$BAO_BIN" server -config="$SANDBOX_DIR/config.hcl" \
  >"$SANDBOX_LOG" 2>&1 &
echo $! > "$SANDBOX_PID_FILE"

# Wait for the listener to come up, bounded retries.
wait_ready() {
  curl -fsS "$BAO_ADDR/v1/sys/seal-status" >/dev/null 2>&1
}
retry 20 0.5 wait_ready || die "sandbox failed to start; check $SANDBOX_LOG"

# If already initialised (rare — only if the data dir survived), skip init.
if curl -fsS "$BAO_ADDR/v1/sys/init" | python3 -c "import json,sys; sys.exit(0 if json.load(sys.stdin)['initialized'] else 1)"; then
  log "sandbox already initialised — skipping init"
  exit 0
fi

log "initialising sandbox (Shamir 1-of-1)"
init_response=$("$BAO_BIN" operator init \
  -address="$BAO_ADDR" \
  -key-shares=1 \
  -key-threshold=1 \
  -format=json)

printf '%s' "$init_response" > "$SANDBOX_INIT_FILE"
chmod 600 "$SANDBOX_INIT_FILE"

unseal_key=$(python3 -c "import json; print(json.load(open('$SANDBOX_INIT_FILE'))['unseal_keys_b64'][0])")

log "unsealing sandbox"
"$BAO_BIN" operator unseal -address="$BAO_ADDR" "$unseal_key" >/dev/null

log "sandbox is ready at $BAO_ADDR"
log "root token saved to $SANDBOX_INIT_FILE (chmod 600)"
