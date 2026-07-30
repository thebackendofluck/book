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

# ssl-for-saas-verify.sh — Verify SSL for SaaS custom hostname certificate status
#
# Polls the Cloudflare API for a provisioned custom hostname and reports:
#   - SSL certificate status (pending_validation → active)
#   - Ownership verification status
#   - TLS settings (min version, HTTP2, early hints)
#   - Expiry date when active
#
# Usage:
#   CF_ZONE=cloud-acmetocasino.com bash ssl-for-saas-verify.sh <hostname> [--watch]
#
#   hostname  — operator hostname, e.g. goldencrown-casino.com
#   --watch   — poll every 15s until active or error (max 10 minutes)
#
# Exit codes: 0=active, 1=error, 2=pending (useful in CI/CD provisioning pipelines)

set -uo pipefail

CF_ZONE="${CF_ZONE:-cloud-acmetocasino.com}"

HOSTNAME="${1:-}"
WATCH_MODE=0
[[ "${2:-}" == "--watch" ]] && WATCH_MODE=1

if [[ -z "$HOSTNAME" ]]; then
    echo "Usage: CF_ZONE=cloud-acmetocasino.com bash $0 <operator-hostname> [--watch]" >&2
    echo "  Example: bash $0 goldencrown-casino.com --watch" >&2
    exit 1
fi

# ── Token: OpenBao first, env var fallback ────────────────────────────────────
if [[ -z "${CF_API_TOKEN:-}" ]]; then
    CF_API_TOKEN=$(bao kv get -field=token secret/project/cloudflare/api_token 2>/dev/null) || true
fi
if [[ -z "${CF_API_TOKEN:-}" ]]; then
    echo "ERROR: CF_API_TOKEN not set. Set env var or ensure OpenBao is reachable." >&2
    exit 1
fi

# ── Helpers ───────────────────────────────────────────────────────────────────
green()  { printf '\033[0;32m[ACTIVE]\033[0m %s\n' "$*"; }
red()    { printf '\033[0;31m[ERROR]\033[0m %s\n' "$*"; }
yellow() { printf '\033[0;33m[PENDING]\033[0m %s\n' "$*"; }
info()   { printf '\033[0;34m[INFO]\033[0m %s\n' "$*"; }

require_cmd() {
    command -v "$1" >/dev/null 2>&1 || { echo "ERROR: $1 required" >&2; exit 1; }
}
require_cmd curl
require_cmd jq

cf_api() {
    curl -sf "https://api.cloudflare.com/client/v4$1" \
        -H "Authorization: Bearer ${CF_API_TOKEN}" \
        -H "Content-Type: application/json"
}

# ── Resolve zone ID ───────────────────────────────────────────────────────────
zone_resp=$(cf_api "/zones?name=${CF_ZONE}&per_page=1")
zone_id=$(printf '%s' "$zone_resp" | jq -r '.result[0].id // empty')
if [[ -z "$zone_id" ]]; then
    red "Zone '${CF_ZONE}' not found"
    exit 1
fi

# ── Status check function ─────────────────────────────────────────────────────
check_status() {
    local resp
    resp=$(cf_api "/zones/${zone_id}/custom_hostnames?hostname=${HOSTNAME}")
    local count
    count=$(printf '%s' "$resp" | jq '.result | length')

    if [[ "$count" -eq 0 ]]; then
        red "Hostname '${HOSTNAME}' not found in zone ${CF_ZONE}"
        echo "  Run: bash ssl-for-saas-provision.sh ${HOSTNAME}" >&2
        exit 1
    fi

    local ssl_status ownership_status tls_min http2 expiry hostname_id
    ssl_status=$(printf '%s' "$resp" | jq -r '.result[0].ssl.status')
    ownership_status=$(printf '%s' "$resp" | jq -r '.result[0].status')
    tls_min=$(printf '%s' "$resp" | jq -r '.result[0].ssl.settings.min_tls_version // "n/a"')
    http2=$(printf '%s' "$resp" | jq -r '.result[0].ssl.settings.http2 // "n/a"')
    expiry=$(printf '%s' "$resp" | jq -r '.result[0].ssl.certificates[0].expires_on // "n/a"')
    hostname_id=$(printf '%s' "$resp" | jq -r '.result[0].id')

    echo ""
    echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
    echo "  Hostname:            ${HOSTNAME}"
    echo "  Hostname ID:         ${hostname_id}"
    echo "  Ownership status:    ${ownership_status}"
    echo "  SSL certificate:     ${ssl_status}"
    echo "  TLS min version:     ${tls_min}"
    echo "  HTTP/2:              ${http2}"
    echo "  Certificate expiry:  ${expiry}"
    echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"

    case "$ssl_status" in
        active)
            green "${HOSTNAME} — SSL certificate active"
            return 0
            ;;
        pending_validation|pending_issuance|pending_deployment)
            yellow "${HOSTNAME} — ${ssl_status}"
            echo ""
            # Show what's still pending
            local txt_name txt_value
            txt_name=$(printf '%s' "$resp" | jq -r '.result[0].ownership_verification.name // empty')
            txt_value=$(printf '%s' "$resp" | jq -r '.result[0].ownership_verification.value // empty')
            if [[ -n "$txt_name" ]]; then
                echo "  Pending DNS TXT record:"
                echo "    Name:  ${txt_name}"
                echo "    Value: ${txt_value}"
            fi
            local ssl_val_count
            ssl_val_count=$(printf '%s' "$resp" | jq '.result[0].ssl.validation_records | length // 0')
            if [[ "${ssl_val_count:-0}" -gt 0 ]]; then
                echo "  SSL validation records still required:"
                printf '%s' "$resp" | jq -r '.result[0].ssl.validation_records[] | "    \(.txt_name)  TXT  \(.txt_value)"'
            fi
            return 2
            ;;
        *)
            red "${HOSTNAME} — unexpected SSL status: ${ssl_status}"
            printf '%s' "$resp" | jq -r '.result[0].ssl.validation_errors[]? | "  Error: \(.message)"'
            return 1
            ;;
    esac
}

# ── Single check or watch loop ────────────────────────────────────────────────
if [[ "$WATCH_MODE" -eq 0 ]]; then
    check_status
    exit $?
fi

# Watch mode: poll every 15s for up to 10 minutes
info "Watch mode: polling every 15s (max 10 minutes)..."
MAX_ATTEMPTS=40
attempt=0

while [[ $attempt -lt $MAX_ATTEMPTS ]]; do
    attempt=$(( attempt + 1 ))
    printf "\n[%d/%d] %s\n" "$attempt" "$MAX_ATTEMPTS" "$(date '+%H:%M:%S')"
    check_status
    result=$?

    if [[ $result -eq 0 ]]; then
        green "Certificate active — provisioning complete"
        exit 0
    elif [[ $result -eq 1 ]]; then
        red "Provisioning error — check CF dashboard"
        exit 1
    fi

    # Pending — wait and retry
    info "Still pending, waiting 15s..."
    sleep 15
done

red "Timed out after 10 minutes — check CF dashboard for ${HOSTNAME}"
exit 1
