#!/usr/bin/env bash
# Companion code for "The Backend of Luck" - Chapter 28b, Infrastructure Patterns Deep Dive.
# https://thebackendofluck.com | https://github.com/thebackendofluck/book
# SPDX-License-Identifier: Apache-2.0
#
# FOR TESTING AND EVALUATION ONLY. NOT FOR PRODUCTION USE.
# Published to demonstrate the patterns explained in the book. This code is
# not certified for real-money gaming: operating a gambling platform requires
# your own licence, independent test-lab certification (GLI, eCOGRA or
# equivalent) and regulator approval.

# magic-transit-readiness.sh — Validate Cloudflare Magic Transit readiness
#
# Magic Transit is a Cloudflare enterprise product that protects operator-owned
# IP ranges at L3/4 using BGP anycast. Operators announce their prefixes through
# Cloudflare; traffic enters the Cloudflare network and is scrubbed before
# forwarding to the operator's origin over GRE or CNI tunnels.
#
# This script validates whether the account and IP infrastructure is ready:
#   1. Checks account entitlements for Magic Transit
#   2. Lists any BYOIP prefixes already delegated to CF
#   3. Checks existing GRE/CNI tunnel configuration
#   4. Reports readiness gaps + next steps
#
# Usage:
#   CF_ZONE=cloud-acmetocasino.com bash magic-transit-readiness.sh
#
# Exit codes: 0=ready, 1=error, 2=prerequisites missing

set -uo pipefail

CF_ZONE="${CF_ZONE:-cloud-acmetocasino.com}"
CF_ACCOUNT="${CF_ACCOUNT:-<your-cf-account-id>}"

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
red()    { printf '\033[0;31m[FAIL]\033[0m %s\n' "$*"; }
green()  { printf '\033[0;32m[PASS]\033[0m %s\n' "$*"; }
yellow() { printf '\033[0;33m[WARN]\033[0m %s\n' "$*"; }
info()   { printf '\033[0;34m[INFO]\033[0m %s\n' "$*"; }

MISSING=0
WARNINGS=0

check_pass() { green "$1"; }
check_fail() { red "$1"; MISSING=$(( MISSING + 1 )); }
check_warn() { yellow "$1"; WARNINGS=$(( WARNINGS + 1 )); }

cf_api() {
    local path="$1"
    curl -sf "https://api.cloudflare.com/client/v4${path}" \
        -H "Authorization: Bearer ${CF_API_TOKEN}" \
        -H "Content-Type: application/json"
}

require_cmd() {
    command -v "$1" >/dev/null 2>&1 || { echo "ERROR: $1 required" >&2; exit 1; }
}

require_cmd curl
require_cmd jq

echo ""
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo "  Cloudflare Magic Transit Readiness Check"
echo "  Account: ${CF_ACCOUNT}"
echo "  Zone:    ${CF_ZONE}"
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo ""

# ── 1. Account details ────────────────────────────────────────────────────────
info "Checking account..."
account_resp=$(cf_api "/accounts/${CF_ACCOUNT}" 2>/dev/null) || {
    check_fail "Cannot reach CF API for account ${CF_ACCOUNT}"
    exit 1
}
account_name=$(printf '%s' "$account_resp" | jq -r '.result.name // "unknown"')
info "Account name: ${account_name}"

# ── 2. Magic Transit entitlement ──────────────────────────────────────────────
info "Checking Magic Transit entitlements..."
# Magic Transit entitlement check: look for magic_transit or magic_firewall in subscriptions
subscriptions_resp=$(cf_api "/accounts/${CF_ACCOUNT}/subscriptions" 2>/dev/null) || subscriptions_resp="{}"
mt_sub=$(printf '%s' "$subscriptions_resp" | jq -r '[.result[]? | select(.app.name // "" | test("magic|transit"; "i"))] | length')

if [[ "${mt_sub:-0}" -gt 0 ]]; then
    check_pass "Magic Transit subscription found"
    printf '%s' "$subscriptions_resp" | jq -r '.result[] | select(.app.name // "" | test("magic|transit"; "i")) | "           → \(.app.name) [\(.status)]"'
else
    check_warn "Magic Transit subscription not found via API"
    echo "           Magic Transit requires an enterprise contract — contact Cloudflare sales"
    echo "           Prerequisites: /24 or larger IP prefix, AS number, LOA document"
fi

# ── 3. BYOIP prefixes ─────────────────────────────────────────────────────────
info "Checking BYOIP (Bring Your Own IP) prefixes..."
prefixes_resp=$(cf_api "/accounts/${CF_ACCOUNT}/addressing/prefixes" 2>/dev/null) || prefixes_resp='{"result":[]}'
prefix_count=$(printf '%s' "$prefixes_resp" | jq '.result | length')

if [[ "$prefix_count" -gt 0 ]]; then
    check_pass "${prefix_count} IP prefix(es) registered with Cloudflare:"
    printf '%s' "$prefixes_resp" | jq -r '.result[] | "           → \(.cidr) [\(.advertised | if . then "advertised" else "not-advertised" end)] ASN:\(.asn // "n/a")"'
else
    check_fail "No BYOIP prefixes configured"
    echo "           Required: at least one /24 prefix with:"
    echo "             - IP ownership proof (RPKI ROA or IRR route object)"
    echo "             - Letter of Authorization (LOA) signed by IP registrant"
    echo "             - ASN for BGP peering with Cloudflare"
    echo ""
    echo "           Template LOA: https://developers.cloudflare.com/byoip/loa/"
fi

# ── 4. GRE tunnels ────────────────────────────────────────────────────────────
info "Checking Magic Transit GRE tunnels..."
gre_resp=$(cf_api "/accounts/${CF_ACCOUNT}/magic/gre_tunnels" 2>/dev/null) || gre_resp='{"result":[]}'
gre_count=$(printf '%s' "$gre_resp" | jq '.result | length')

if [[ "$gre_count" -gt 0 ]]; then
    check_pass "${gre_count} GRE tunnel(s) configured:"
    printf '%s' "$gre_resp" | jq -r '.result[] | "           → \(.name): \(.customer_gre_endpoint) → \(.cloudflare_gre_endpoint) [\(.health_check.enabled | if . then "health-check-on" else "health-check-off" end)]"'
else
    check_warn "No GRE tunnels configured"
    echo "           GRE tunnels are required to forward scrubbed traffic back to origin."
    echo "           Alternative: CNI (Cloudflare Network Interconnect) for direct peering."
    echo ""
    echo "           Minimum tunnel spec:"
    echo "             - MTU: 1476 (GRE overhead = 24 bytes from MTU 1500)"
    echo "             - TTL: 64"
    echo "             - Health check interval: 60s"
fi

# ── 5. CNI (Network Interconnect) ─────────────────────────────────────────────
info "Checking Cloudflare Network Interconnect (CNI) configuration..."
cni_resp=$(cf_api "/accounts/${CF_ACCOUNT}/magic/cf_interconnects" 2>/dev/null) || cni_resp='{"result":[]}'
cni_count=$(printf '%s' "$cni_resp" | jq '.result | length')

if [[ "$cni_count" -gt 0 ]]; then
    check_pass "${cni_count} CNI interconnect(s) configured:"
    printf '%s' "$cni_resp" | jq -r '.result[] | "           → \(.name): \(.interface_address)"'
else
    info "No CNI interconnects configured (GRE tunnels are the simpler starting point)"
fi

# ── 6. BGP routing check ──────────────────────────────────────────────────────
info "Checking static route configuration..."
routes_resp=$(cf_api "/accounts/${CF_ACCOUNT}/magic/routes" 2>/dev/null) || routes_resp='{"result":[]}'
route_count=$(printf '%s' "$routes_resp" | jq '.result | length')

if [[ "$route_count" -gt 0 ]]; then
    check_pass "${route_count} static route(s) configured:"
    printf '%s' "$routes_resp" | jq -r '.result[] | "           → \(.prefix) via \(.nexthop) [priority \(.priority)]"'
else
    check_warn "No static routes configured"
    echo "           Static routes map prefixes to GRE tunnels in the Cloudflare dashboard"
fi

# ── 7. Summary ────────────────────────────────────────────────────────────────
echo ""
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo "  Magic Transit Readiness Summary"
echo ""
echo "  Prerequisites missing:  ${MISSING}"
echo "  Warnings (advisory):    ${WARNINGS}"
echo ""
echo "  Prerequisites checklist:"
echo "  [ ] Enterprise contract with Cloudflare for Magic Transit"
echo "  [ ] Operator-owned IP prefix (/24 minimum, /23 or larger recommended)"
echo "  [ ] ASN assigned by regional internet registry (ARIN/RIPE/APNIC)"
echo "  [ ] RPKI ROA or IRR route object for IP prefix"
echo "  [ ] Letter of Authorization (LOA) from IP registrant"
echo "  [ ] GRE tunnel or CNI interconnect to operator's data center"
echo "  [ ] Cloudflare IP ranges whitelisted in operator's firewall"
echo "  [ ] BGP peering capacity and failover tested"
echo ""
echo "  Deployment timeline: ~10-12 hours (Cloudflare SLA)"
echo "  DDoS mitigation activation: <10 minutes after attack detection"
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"

if [[ "$MISSING" -gt 0 ]]; then
    exit 2
fi
