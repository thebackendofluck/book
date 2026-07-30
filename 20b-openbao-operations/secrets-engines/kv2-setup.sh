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

# Enable a KV v2 mount at platform/secrets/ with CAS required and populate a
# sample secret. Demonstrates the versioned-secret workflow from chapter 20b.

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=../lib/common.sh
source "$SCRIPT_DIR/../lib/common.sh"

ensure_sandbox
load_root_token

MOUNT="platform/secrets"

if ! "$BAO_BIN" secrets list -format=json | python3 -c "import json,sys; print('$MOUNT/' in json.load(sys.stdin))" | grep -q True; then
  log "enabling kv-v2 at $MOUNT"
  "$BAO_BIN" secrets enable -path="$MOUNT" -version=2 kv
else
  log "kv-v2 at $MOUNT already enabled"
fi

log "setting cas_required=true and max_versions=10 on $MOUNT"
"$BAO_BIN" write "$MOUNT/config" \
  cas_required=true \
  max_versions=10 \
  delete_version_after=2160h  # 90 days before hard destroy of soft-deleted versions

# Seed the canary secret used by verify-raft-snapshot.sh
if ! "$BAO_BIN" kv get -format=json "$MOUNT/canary" >/dev/null 2>&1; then
  log "seeding canary secret at $MOUNT/canary"
  # First write: CAS=0 (no existing version)
  "$BAO_BIN" kv put -cas=0 "$MOUNT/canary" \
    purpose="snapshot-freshness-beacon" \
    created_at="$(date -u +%Y-%m-%dT%H:%M:%SZ)" \
    nonce="$(head -c 16 /dev/urandom | base64)"
else
  log "canary secret already exists"
fi

log "kv-v2 setup complete"
"$BAO_BIN" kv get "$MOUNT/canary"
