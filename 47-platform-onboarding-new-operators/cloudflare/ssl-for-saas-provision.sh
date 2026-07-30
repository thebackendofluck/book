#!/usr/bin/env bash
# Companion code for "The Backend of Luck" - Chapter 47, Platform Onboarding.
# https://thebackendofluck.com | https://github.com/thebackendofluck/book
# SPDX-License-Identifier: Apache-2.0
#
# FOR TESTING AND EVALUATION ONLY. NOT FOR PRODUCTION USE.
# Published to demonstrate the patterns explained in the book. This code is
# not certified for real-money gaming: operating a gambling platform requires
# your own licence, independent test-lab certification (GLI, eCOGRA or
# equivalent) and regulator approval.

# ssl-for-saas-provision.sh — Provision SSL for SaaS custom hostname via CF API
#
# Cloudflare SSL for SaaS lets a B2B/white-label iGaming platform provision
# per-operator custom hostnames with automated DV certificates. Each operator's
# domain (e.g., goldencrown-casino.com) gets a DV cert in ~60-120 seconds
# without cert-manager, Let's Encrypt ACME challenges, or per-operator
# infrastructure overhead.
#
# How it works:
#   1. POST /zones/{saas_zone_id}/custom_hostnames  → creates the hostname entry
#   2. CF returns ownership_verification (TXT record) + ssl.validation_records
#   3. Operator adds the TXT record to their DNS
#   4. CF issues DV cert automatically; status transitions: pending → active
#   5. Operator CNAMEs their domain to the SaaS fallback origin
#
# Usage:
#   CF_ZONE=cloud-acmetocasino.com \
#   bash ssl-for-saas-provision.sh <hostname> [ssl_method]
#
#   hostname    — operator custom hostname, e.g. goldencrown-casino.com
#   ssl_method  — "txt" (default) or "http"
#
# Exit codes: 0=provisioned, 1=error

set -uo pipefail

CF_ZONE="${CF_ZONE:-cloud-acmetocasino.com}"

HOSTNAME="${1:-}"
SSL_METHOD="${2:-txt}"

if [[ -z "$HOSTNAME" ]]; then
    echo "Usage: CF_ZONE=cloud-acmetocasino.com bash $0 <operator-hostname> [txt|http]" >&2
    echo "  Example: bash $0 goldencrown-casino.com txt" >&2
    exit 1
fi

# ── Token: OpenBao first, env var fallback ────────────────────────────────────
if [[ -z "${CF_API_TOKEN:-}" ]]; then
    CF_API_TOKEN=$(bao kv get -field=token secret/project/cloudflare/api_token 2>/dev/null) || true
fi
if [[ -z "${CF_API_TOKEN:-}" ]]; then
    echo "ERROR: CF_API_TOKEN not set. Set env var or ensure OpenBao is reachable." >&2
    echo "       bao kv get -field=token secret/project/cloudflare/api_token" >&2
    exit 1
fi

# ── Helpers ───────────────────────────────────────────────────────────────────
green() { printf '\033[0;32m[OK]\033[0m %s\n' "$*"; }
red()   { printf '\033[0;31m[FAIL]\033[0m %s\n' "$*"; }
info()  { printf '\033[0;34m[INFO]\033[0m %s\n' "$*"; }

require_cmd() {
    command -v "$1" >/dev/null 2>&1 || { echo "ERROR: $1 required" >&2; exit 1; }
}
require_cmd curl
require_cmd jq

cf_api() {
    local method="$1" path="$2" data="${3:-}"
    if [[ -n "$data" ]]; then
        curl -sf -X "$method" "https://api.cloudflare.com/client/v4${path}" \
            -H "Authorization: Bearer ${CF_API_TOKEN}" \
            -H "Content-Type: application/json" \
            -d "$data"
    else
        curl -sf -X "$method" "https://api.cloudflare.com/client/v4${path}" \
            -H "Authorization: Bearer ${CF_API_TOKEN}" \
            -H "Content-Type: application/json"
    fi
}

# ── Validate ssl_method ───────────────────────────────────────────────────────
case "$SSL_METHOD" in
    txt|http) ;;
    *) echo "ERROR: ssl_method must be 'txt' or 'http'" >&2; exit 1 ;;
esac

# ── 1. Resolve SaaS zone ID ───────────────────────────────────────────────────
info "Resolving zone ID for ${CF_ZONE}..."
zone_resp=$(cf_api GET "/zones?name=${CF_ZONE}&per_page=1")
zone_id=$(printf '%s' "$zone_resp" | jq -r '.result[0].id // empty')
if [[ -z "$zone_id" ]]; then
    red "Zone '${CF_ZONE}' not found. Check CF_ZONE and API token permissions."
    exit 1
fi
info "SaaS zone ID: ${zone_id}"

# ── 2. Check if hostname already exists ───────────────────────────────────────
info "Checking if ${HOSTNAME} already provisioned..."
existing=$(cf_api GET "/zones/${zone_id}/custom_hostnames?hostname=${HOSTNAME}" 2>/dev/null)
existing_id=$(printf '%s' "$existing" | jq -r '.result[0].id // empty')
if [[ -n "$existing_id" ]]; then
    info "Hostname already exists (id: ${existing_id}) — use ssl-for-saas-verify.sh to check status"
    printf '%s' "$existing" | jq -r '.result[0] | {id, hostname, status, ssl_status: .ssl.status}'
    exit 0
fi

# ── 3. Provision custom hostname ──────────────────────────────────────────────
info "Provisioning SSL for SaaS custom hostname: ${HOSTNAME} (method: ${SSL_METHOD})..."

payload=$(jq -n \
    --arg h "$HOSTNAME" \
    --arg m "$SSL_METHOD" \
    '{
        hostname: $h,
        ssl: {
            method: $m,
            type: "dv",
            settings: {
                min_tls_version: "1.2",
                tls_1_3: "on",
                http2: "on",
                early_hints: "on"
            }
        }
    }')

response=$(cf_api POST "/zones/${zone_id}/custom_hostnames" "$payload")

success=$(printf '%s' "$response" | jq -r '.success')
if [[ "$success" != "true" ]]; then
    red "Provisioning failed:"
    printf '%s' "$response" | jq -r '.errors[] | "  → \(.code): \(.message)"'
    exit 1
fi

# ── 4. Extract and display verification requirements ──────────────────────────
hostname_id=$(printf '%s' "$response" | jq -r '.result.id')
ssl_status=$(printf '%s' "$response" | jq -r '.result.ssl.status')
ownership_type=$(printf '%s' "$response" | jq -r '.result.ownership_verification.type // "n/a"')
ownership_name=$(printf '%s' "$response" | jq -r '.result.ownership_verification.name // "n/a"')
ownership_value=$(printf '%s' "$response" | jq -r '.result.ownership_verification.value // "n/a"')

echo ""
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
green "Custom hostname provisioned"
echo ""
echo "  Hostname ID:  ${hostname_id}"
echo "  Hostname:     ${HOSTNAME}"
echo "  SSL Status:   ${ssl_status}"
echo "  Zone:         ${CF_ZONE} (${zone_id})"
echo ""

if [[ "$ownership_type" == "txt" ]]; then
    echo "  ── Ownership Verification (TXT record) ──────────"
    echo "  The operator must add this DNS TXT record:"
    echo ""
    echo "  Name:   ${ownership_name}"
    echo "  Type:   TXT"
    echo "  Value:  ${ownership_value}"
    echo ""
    echo "  Once added, Cloudflare will:"
    echo "    1. Verify ownership (~minutes)"
    echo "    2. Issue DV certificate (~60-120 seconds)"
    echo "    3. SSL status transitions: pending_validation → active"
fi

# Extract SSL validation records if HTTP method
ssl_val_count=$(printf '%s' "$response" | jq '.result.ssl.validation_records | length // 0')
if [[ "${ssl_val_count:-0}" -gt 0 ]]; then
    echo "  ── SSL Validation Records ───────────────────────"
    printf '%s' "$response" | jq -r '.result.ssl.validation_records[] | "  \(.txt_name)  TXT  \(.txt_value)"'
fi

echo ""
echo "  ── Operator DNS CNAME (final step) ─────────────"
echo "  After cert is active, operator must CNAME their domain:"
echo "  ${HOSTNAME}  CNAME  ${CF_ZONE}."
echo ""
echo "  ── Verify status ────────────────────────────────"
echo "  bash ssl-for-saas-verify.sh ${HOSTNAME}"
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
