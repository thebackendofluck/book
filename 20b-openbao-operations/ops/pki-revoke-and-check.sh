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

# Issue a certificate, revoke it, and verify it appears in the CRL.
# Used as a smoke test for the PKI revocation path.

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=../lib/common.sh
source "$SCRIPT_DIR/../lib/common.sh"

ensure_sandbox
load_root_token
require_cmd openssl

OUT_DIR="${SANDBOX_DIR}/pki-out"
mkdir -p "$OUT_DIR"

log "issuing ephemeral cert"
json=$("$BAO_BIN" write -format=json pki_int/issue/internal-service \
  common_name="revoke-test.internal.acme" ttl=1h)
serial=$(printf '%s' "$json" | python3 -c 'import json,sys; print(json.load(sys.stdin)["data"]["serial_number"])')
log "serial: $serial"

log "revoking $serial"
"$BAO_BIN" write pki_int/revoke serial_number="$serial" >/dev/null

log "fetching CRL via /v1/pki_int/crl/pem"
curl -fsS "${BAO_ADDR}/v1/pki_int/crl/pem" -o "$OUT_DIR/crl.pem"
[[ -s "$OUT_DIR/crl.pem" ]] || die "CRL fetch returned empty body"

log "checking CRL for serial"
# openssl prints revoked serials as uppercase hex without colons
serial_no_colons=$(printf '%s' "$serial" | tr -d ':' | tr '[:lower:]' '[:upper:]')
if openssl crl -in "$OUT_DIR/crl.pem" -inform pem -text -noout 2>/dev/null \
    | grep -q "$serial_no_colons"; then
  log "CRL revocation verified: $serial found in CRL"
else
  die "FAIL: revoked serial $serial not found in CRL"
fi
