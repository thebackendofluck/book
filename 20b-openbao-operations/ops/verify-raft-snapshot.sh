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

# Restore a raft snapshot into a throw-away verification sandbox and confirm
# that a canary secret is readable. The verification sandbox is a second
# OpenBao instance on port $SANDBOX_PORT+10 so that it does not collide with
# the main chapter-20b sandbox or production.
#
# Usage: verify-raft-snapshot.sh <snapshot-file>

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=../lib/common.sh
source "$SCRIPT_DIR/../lib/common.sh"

SNAP=${1:?usage: verify-raft-snapshot.sh <snapshot-file>}
[[ -f "$SNAP" ]] || die "snapshot not found: $SNAP"

VERIFY_PORT=$((SANDBOX_PORT + 10))
VERIFY_DIR="/tmp/openbao-verify-20b"
VERIFY_PID_FILE="$VERIFY_DIR/server.pid"
VERIFY_LOG="$VERIFY_DIR/server.log"

cleanup() {
  if [[ -f "$VERIFY_PID_FILE" ]]; then
    kill "$(cat "$VERIFY_PID_FILE")" 2>/dev/null || true
  fi
  rm -rf "$VERIFY_DIR"
}
trap cleanup EXIT

log "creating verify sandbox at $VERIFY_DIR (port $VERIFY_PORT)"
mkdir -p "$VERIFY_DIR/data"
chmod 700 "$VERIFY_DIR"

cat > "$VERIFY_DIR/config.hcl" <<EOF
disable_mlock = true
ui            = false

storage "raft" {
  path    = "$VERIFY_DIR/data"
  node_id = "verify-20b"
}

listener "tcp" {
  address     = "127.0.0.1:$VERIFY_PORT"
  tls_disable = true
}

api_addr     = "http://127.0.0.1:$VERIFY_PORT"
cluster_addr = "http://127.0.0.1:$((VERIFY_PORT + 1))"
EOF

nohup "$BAO_BIN" server -config="$VERIFY_DIR/config.hcl" > "$VERIFY_LOG" 2>&1 &
echo $! > "$VERIFY_PID_FILE"

VERIFY_ADDR="http://127.0.0.1:$VERIFY_PORT"
export BAO_ADDR="$VERIFY_ADDR"

for _ in 1 2 3 4 5 6 7 8 9 10; do
  curl -fsS "$VERIFY_ADDR/v1/sys/seal-status" >/dev/null 2>&1 && break
  sleep 0.5
done

log "initialising verify sandbox (will be overwritten by restore)"
init=$("$BAO_BIN" operator init -key-shares=1 -key-threshold=1 -format=json)
unseal_key=$(printf '%s' "$init" | python3 -c 'import json,sys; print(json.load(sys.stdin)["unseal_keys_b64"][0])')
root_token=$(printf '%s' "$init" | python3 -c 'import json,sys; print(json.load(sys.stdin)["root_token"])')
"$BAO_BIN" operator unseal "$unseal_key" >/dev/null
export BAO_TOKEN="$root_token"

log "restoring snapshot $SNAP"
"$BAO_BIN" operator raft snapshot restore -force "$SNAP"

# After restore, the original Shamir keys from the source cluster own the
# seal, so we need to re-unseal with the ORIGINAL unseal keys, not the ones
# we just generated. The main sandbox used a 1-of-1 seal.
src_unseal=$(python3 -c "import json; print(json.load(open('$SANDBOX_INIT_FILE'))['unseal_keys_b64'][0])")
src_token=$(python3 -c "import json; print(json.load(open('$SANDBOX_INIT_FILE'))['root_token'])")

log "re-unsealing with source cluster keys"
"$BAO_BIN" operator unseal "$src_unseal" >/dev/null
export BAO_TOKEN="$src_token"

log "canary read: platform/secrets/canary"
if "$BAO_BIN" kv get platform/secrets/canary >/dev/null 2>&1; then
  log "canary read PASS -- snapshot is valid"
else
  die "canary read FAILED -- snapshot is corrupt or the canary was not in the source cluster"
fi
