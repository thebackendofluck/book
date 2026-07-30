#!/usr/bin/env bash
# Companion code for "The Backend of Luck" - Chapter 47d, Casino as a Service.
# https://thebackendofluck.com | https://github.com/thebackendofluck/book
# SPDX-License-Identifier: BUSL-1.1
#
# FOR TESTING AND EVALUATION ONLY. NOT FOR PRODUCTION USE.
# Published to demonstrate the patterns explained in the book. This code is
# not certified for real-money gaming: operating a gambling platform requires
# your own licence, independent test-lab certification (GLI, eCOGRA or
# equivalent) and regulator approval.

# setup_tenant_secrets.sh — provision a tenant's isolated secret material in OpenBao,
# rooted in the platform HSM (YubiHSM).
#
# What it creates (all scoped to the tenant slug, so tenants cannot read each other):
#   1. A policy  caas-<slug>            — read-only on caas/<slug>/* + transit use of the tenant key
#   2. An AppRole auth/approle/role/caas-<slug>  bound to that policy (the runtime logs in with it)
#   3. A Transit key transit/keys/caas-<slug>    — HSM-backed envelope-encryption / JWT-signing key.
#      OpenBao's transit engine and barrier are sealed by the YubiHSM (PKCS#11 auto-unseal,
#      see chapter 20), so the key material never exists in plaintext outside the HSM boundary.
#   4. KV secrets at caas/<slug>/        — db_url, jwt_secret, psp_key, kyc_provider
#
# Dry-run by default (prints the bao commands). Pass EXECUTE=1 to apply.
#   Usage: [EXECUTE=1] setup_tenant_secrets.sh <slug> [jurisdiction]
set -euo pipefail

slug="${1:-}"
jurisdiction="${2:-demo}"
EXECUTE="${EXECUTE:-0}"
KV_MOUNT="${KV_MOUNT:-caas}"
TRANSIT_MOUNT="${TRANSIT_MOUNT:-transit}"

if [[ ! ${slug} =~ ^[a-z0-9][a-z0-9-]{1,38}[a-z0-9]$ ]]; then
    printf 'ERROR: invalid slug %q — must be 3-40 chars, lowercase letters/digits/hyphens.\n' "${slug}" >&2
    exit 1
fi

run() {
    if [ "${EXECUTE}" = "1" ]; then
        printf '[EXECUTE] %s\n' "$*"
        "$@"
    else
        printf '[DRY-RUN] %s\n' "$*"
    fi
}

# Policy document scoped strictly to this tenant (KV + its own transit key only).
policy_hcl="$(cat <<EOF
path "${KV_MOUNT}/data/${slug}/*"      { capabilities = ["read"] }
path "${KV_MOUNT}/metadata/${slug}/*"  { capabilities = ["read","list"] }
path "${TRANSIT_MOUNT}/encrypt/caas-${slug}" { capabilities = ["update"] }
path "${TRANSIT_MOUNT}/decrypt/caas-${slug}" { capabilities = ["update"] }
path "${TRANSIT_MOUNT}/sign/caas-${slug}"    { capabilities = ["update"] }
EOF
)"

printf '== Provisioning OpenBao secret material for tenant %q (jurisdiction=%s) ==\n' "${slug}" "${jurisdiction}"

# 1. Per-tenant policy (idempotent: policy write is create-or-replace)
if [ "${EXECUTE}" = "1" ]; then
    printf '%s\n' "${policy_hcl}" | run bao policy write "caas-${slug}" -
else
    printf '[DRY-RUN] bao policy write caas-%s - <<<(scoped to %s/%s/* + transit caas-%s)\n' \
        "${slug}" "${KV_MOUNT}" "${slug}" "${slug}"
fi

# 2. AppRole bound to the tenant policy (the runtime authenticates with this)
run bao auth enable -path=approle approle
run bao write "auth/approle/role/caas-${slug}" \
    token_policies="caas-${slug}" \
    token_ttl=1h token_max_ttl=4h secret_id_ttl=24h

# 3. HSM-backed Transit key for this tenant (envelope encryption + JWT signing)
run bao write -f "${TRANSIT_MOUNT}/keys/caas-${slug}" type=ed25519

# 4. Seed KV secrets (overwrite-safe per-tenant path)
run bao kv put "${KV_MOUNT}/${slug}" \
    db_url="postgres://caas@caas-${slug}-db.caas-${slug}.svc.cluster.local:5432/casino" \
    jwt_secret="@transit:caas-${slug}" \
    psp_key="set-me-from-psp-vault" \
    kyc_provider="sumsub"

printf 'Tenant %q secret material ready (policy + AppRole + HSM transit key + KV).\n' "${slug}"
