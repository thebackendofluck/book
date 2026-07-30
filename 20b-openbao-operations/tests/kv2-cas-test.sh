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

# Verify that the KV v2 CAS-required policy rejects writes with a stale version.
# Expected behaviour:
#   1. Put version 1 with cas=0 (success)
#   2. Put version 2 with cas=1 (success)
#   3. Attempt to put another version with cas=1 (FAIL -- stale expected version)
# A non-zero exit from step 3 is the PASS condition.

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=../lib/common.sh
source "$SCRIPT_DIR/../lib/common.sh"

ensure_sandbox
load_root_token

MOUNT="platform/secrets"
KEY="cas-test/$$"

cleanup() {
  "$BAO_BIN" kv metadata delete "$MOUNT/$KEY" >/dev/null 2>&1 || true
}
trap cleanup EXIT

log "step 1: first put with cas=0"
"$BAO_BIN" kv put -cas=0 "$MOUNT/$KEY" value=v1 >/dev/null
log "  ok"

log "step 2: second put with cas=1"
"$BAO_BIN" kv put -cas=1 "$MOUNT/$KEY" value=v2 >/dev/null
log "  ok"

log "step 3: stale put with cas=1 (should fail)"
if "$BAO_BIN" kv put -cas=1 "$MOUNT/$KEY" value=v3 >/dev/null 2>&1; then
  die "FAIL: CAS check did not reject stale write"
fi
log "  correctly rejected -- PASS"

log "kv-v2 CAS enforcement verified"
