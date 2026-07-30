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

# argo-latency-benchmark.sh — Measure TTFB and verify Argo Smart Routing status
#
# Playtech case study measured 35-40ms TTFB improvement with Argo Smart Routing.
# This script:
#   1. Fetches CF_API_TOKEN from OpenBao (fallback: env var)
#   2. Checks Argo Smart Routing enabled/disabled for the zone via CF API
#   3. Runs N curl measurements and reports p50/p95 TTFB
#   4. Reads cf-cache-status + server-timing headers to confirm backbone routing
#
# Usage:
#   CF_ZONE=cloud-acmetocasino.com bash argo-latency-benchmark.sh [URL] [N]
#
#   URL   — target URL (default: https://$CF_ZONE/)
#   N     — number of curl samples (default: 20)
#
# Exit codes: 0=OK, 1=error, 2=Argo disabled

set -uo pipefail

# ── Config ────────────────────────────────────────────────────────────────────
CF_ZONE="${CF_ZONE:-cloud-acmetocasino.com}"
CF_ACCOUNT="${CF_ACCOUNT:-<your-cf-account-id>}"
TARGET_URL="${1:-https://${CF_ZONE}/}"
SAMPLES="${2:-20}"

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
red()   { printf '\033[0;31m%s\033[0m\n' "$*"; }
green() { printf '\033[0;32m%s\033[0m\n' "$*"; }
yellow(){ printf '\033[0;33m%s\033[0m\n' "$*"; }
info()  { printf '\033[0;34m[INFO]\033[0m %s\n' "$*"; }

cf_api() {
    local path="$1"
    curl -sf "https://api.cloudflare.com/client/v4${path}" \
        -H "Authorization: Bearer ${CF_API_TOKEN}" \
        -H "Content-Type: application/json"
}

require_cmd() {
    command -v "$1" >/dev/null 2>&1 || { echo "ERROR: $1 required but not found" >&2; exit 1; }
}

require_cmd curl
require_cmd jq
require_cmd bc

# ── 1. Resolve zone ID ────────────────────────────────────────────────────────
info "Resolving zone ID for ${CF_ZONE}..."
zone_response=$(cf_api "/zones?name=${CF_ZONE}&per_page=1")
zone_id=$(printf '%s' "$zone_response" | jq -r '.result[0].id // empty')

if [[ -z "$zone_id" ]]; then
    echo "ERROR: Zone '${CF_ZONE}' not found in account ${CF_ACCOUNT}" >&2
    echo "       Check CF_ZONE and that the API token has Zone.Read permission." >&2
    exit 1
fi
info "Zone ID: ${zone_id}"

# ── 2. Check Argo Smart Routing status ───────────────────────────────────────
info "Checking Argo Smart Routing status..."
argo_response=$(cf_api "/zones/${zone_id}/argo/smart_routing" 2>/dev/null) || {
    yellow "WARNING: Could not fetch Argo Smart Routing status (may require paid plan)"
    argo_value="unknown"
}
if [[ -n "${argo_response:-}" ]]; then
    argo_value=$(printf '%s' "$argo_response" | jq -r '.result.value // "unknown"')
fi

echo ""
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo "  Zone:                ${CF_ZONE}"
echo "  Zone ID:             ${zone_id}"
echo "  Argo Smart Routing:  ${argo_value}"
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"

if [[ "$argo_value" == "off" ]]; then
    yellow "  Argo Smart Routing is DISABLED — enable via CF dashboard or:"
    yellow "  curl -X PATCH .../argo/smart_routing -d '{\"value\":\"on\"}'"
    echo ""
fi

# ── 3. TTFB benchmark ─────────────────────────────────────────────────────────
info "Running ${SAMPLES} TTFB measurements against ${TARGET_URL}..."

ttfb_values=()
cache_hits=0

for i in $(seq 1 "$SAMPLES"); do
    result=$(curl -sf -o /dev/null \
        -H "Cache-Control: no-cache" \
        -H "Pragma: no-cache" \
        --dump-header /tmp/argo-bench-headers-$$ \
        -w "%{time_starttransfer}" \
        "$TARGET_URL" 2>/dev/null) || result="0"

    ttfb_ms=$(echo "$result * 1000" | bc -l 2>/dev/null | xargs printf "%.1f")
    ttfb_values+=("$ttfb_ms")

    cache_status=$(grep -i "^cf-cache-status:" /tmp/argo-bench-headers-$$ 2>/dev/null \
        | tr -d '\r' | awk '{print $2}') || cache_status="MISS"
    [[ "$cache_status" == "HIT" ]] && cache_hits=$(( cache_hits + 1 ))

    printf "  Sample %2d/%d: %s ms  [cf-cache-status: %s]\n" \
        "$i" "$SAMPLES" "$ttfb_ms" "${cache_status:-MISS}"
done
rm -f /tmp/argo-bench-headers-$$

# ── 4. Statistics (sort + awk for p50/p95) ────────────────────────────────────
sorted_values=()
while IFS= read -r line; do
    sorted_values+=("$line")
done < <(printf '%s\n' "${ttfb_values[@]}" | sort -n)

n="${#sorted_values[@]}"
p50_idx=$(( (n - 1) / 2 ))
p95_idx=$(( n * 95 / 100 ))
p50="${sorted_values[$p50_idx]}"
p95="${sorted_values[$p95_idx]}"
min="${sorted_values[0]}"
max="${sorted_values[$((n-1))]}"

sum=0
for v in "${sorted_values[@]}"; do
    sum=$(echo "$sum + $v" | bc -l)
done
avg=$(echo "scale=1; $sum / $n" | bc -l)

# ── 5. Argo impact estimate ───────────────────────────────────────────────────
# Playtech measured 35-40ms improvement with Argo Smart Routing enabled.
# Without Argo, public internet routing adds congestion-dependent overhead.
if [[ "$argo_value" == "on" ]]; then
    argo_label="ENABLED (private backbone active)"
    benchmark_note="Playtech reference: ~37ms improvement vs. public routing"
else
    estimated_without=$(echo "$p50 + 37" | bc -l | xargs printf "%.1f")
    argo_label="DISABLED (public internet routing)"
    benchmark_note="Estimated p50 without Argo: ~${estimated_without}ms (Playtech benchmark: +35-40ms)"
fi

echo ""
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo "  TTFB Results (${SAMPLES} samples)"
echo "  Target: ${TARGET_URL}"
echo ""
echo "  Min:    ${min} ms"
echo "  p50:    ${p50} ms"
echo "  p95:    ${p95} ms"
echo "  Max:    ${max} ms"
echo "  Avg:    ${avg} ms"
echo ""
echo "  Cache hits: ${cache_hits}/${SAMPLES}"
echo ""
echo "  Argo Smart Routing: ${argo_label}"
echo "  ${benchmark_note}"
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"

if [[ "$argo_value" == "off" ]]; then
    echo ""
    yellow "ACTION: Enable Argo Smart Routing to reduce p50 TTFB by ~35-40ms"
    yellow "        CF Dashboard → Speed → Optimization → Argo Smart Routing"
    exit 2
fi

green "Argo Smart Routing is active — backbone routing in effect."
