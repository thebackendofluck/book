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

# Build a two-tier PKI hierarchy in the sandbox:
#   pki/     -- root CA, 10 year max TTL, signs only the intermediate
#   pki_int/ -- intermediate CA, 5 year max TTL, issues leaf certificates
# Also defines an 'internal-service' role used by pki-issue-cert.sh.

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=../lib/common.sh
source "$SCRIPT_DIR/../lib/common.sh"

ensure_sandbox
load_root_token

OUT_DIR="${SANDBOX_DIR}/pki-out"
mkdir -p "$OUT_DIR"

list_mounts() {
  "$BAO_BIN" secrets list -format=json
}

# Root CA
if ! list_mounts | python3 -c 'import json,sys; print("pki/" in json.load(sys.stdin))' | grep -q True; then
  log "enabling root pki"
  "$BAO_BIN" secrets enable -path=pki pki
  "$BAO_BIN" secrets tune -max-lease-ttl=87600h pki

  log "generating root CA"
  "$BAO_BIN" write -field=certificate pki/root/generate/internal \
    common_name="acme iGaming Root CA (sandbox)" \
    ttl=87600h \
    key_type=ec \
    key_bits=256 > "$OUT_DIR/root.crt"

  "$BAO_BIN" write pki/config/urls \
    issuing_certificates="http://127.0.0.1:${SANDBOX_PORT}/v1/pki/ca" \
    crl_distribution_points="http://127.0.0.1:${SANDBOX_PORT}/v1/pki/crl"
else
  log "root pki already enabled"
fi

# Intermediate CA
if ! list_mounts | python3 -c 'import json,sys; print("pki_int/" in json.load(sys.stdin))' | grep -q True; then
  log "enabling intermediate pki"
  "$BAO_BIN" secrets enable -path=pki_int pki
  "$BAO_BIN" secrets tune -max-lease-ttl=43800h pki_int

  log "generating intermediate CSR"
  "$BAO_BIN" write -format=json pki_int/intermediate/generate/internal \
    common_name="acme iGaming Intermediate CA (sandbox)" \
    key_type=ec key_bits=256 \
    | python3 -c 'import json,sys; print(json.load(sys.stdin)["data"]["csr"])' \
    > "$OUT_DIR/intermediate.csr"

  log "signing intermediate with root"
  "$BAO_BIN" write -format=json pki/root/sign-intermediate \
    csr=@"$OUT_DIR/intermediate.csr" \
    format=pem_bundle \
    ttl=43800h \
    | python3 -c 'import json,sys; print(json.load(sys.stdin)["data"]["certificate"])' \
    > "$OUT_DIR/intermediate.crt"

  log "installing signed intermediate"
  "$BAO_BIN" write pki_int/intermediate/set-signed certificate=@"$OUT_DIR/intermediate.crt" >/dev/null

  "$BAO_BIN" write pki_int/config/urls \
    issuing_certificates="http://127.0.0.1:${SANDBOX_PORT}/v1/pki_int/ca" \
    crl_distribution_points="http://127.0.0.1:${SANDBOX_PORT}/v1/pki_int/crl"
else
  log "intermediate pki already enabled"
fi

# Issuance role
log "ensuring internal-service role"
"$BAO_BIN" write pki_int/roles/internal-service \
  allowed_domains="internal.acme" \
  allow_subdomains=true \
  max_ttl="72h" \
  key_type="ec" \
  key_bits=256 \
  use_csr_common_name=false \
  ou="platform" \
  organization="acme" >/dev/null

log "pki hierarchy ready"
log "root certificate: $OUT_DIR/root.crt"
log "intermediate certificate: $OUT_DIR/intermediate.crt"
"$BAO_BIN" list pki_int/roles
