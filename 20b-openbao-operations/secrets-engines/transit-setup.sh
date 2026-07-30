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

# Enable the Transit engine in the sandbox and provision the keys referenced
# throughout chapter 20b. Idempotent: re-running is safe.
#
# Keys provisioned:
#   platform-pii        -- aes256-gcm96, used for PII column encryption examples
#   platform-audit-sign -- ed25519, used for audit chain signing examples

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=../lib/common.sh
source "$SCRIPT_DIR/../lib/common.sh"

ensure_sandbox
load_root_token

# Enable transit (no-op if already enabled)
if ! "$BAO_BIN" secrets list -format=json 2>/dev/null | python3 -c 'import json,sys; print("transit/" in json.load(sys.stdin))' | grep -q True; then
  log "enabling transit engine"
  "$BAO_BIN" secrets enable transit
else
  log "transit already enabled"
fi

# platform-pii (AES-256-GCM96, exportable=false, deletion_allowed=false in prod)
if ! "$BAO_BIN" read -format=json transit/keys/platform-pii >/dev/null 2>&1; then
  log "creating transit key platform-pii"
  "$BAO_BIN" write -f transit/keys/platform-pii type=aes256-gcm96
else
  log "transit key platform-pii already exists"
fi

# platform-audit-sign (ed25519 for signing audit chain entries)
if ! "$BAO_BIN" read -format=json transit/keys/platform-audit-sign >/dev/null 2>&1; then
  log "creating transit signing key platform-audit-sign"
  "$BAO_BIN" write -f transit/keys/platform-audit-sign type=ed25519
else
  log "transit signing key platform-audit-sign already exists"
fi

log "transit setup complete"
"$BAO_BIN" list transit/keys
