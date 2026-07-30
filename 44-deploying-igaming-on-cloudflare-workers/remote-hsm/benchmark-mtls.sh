#!/usr/bin/env bash
# Companion code for "The Backend of Luck" - Chapter 44, Deploying iGaming Platforms on Cloudflare Workers.
# https://thebackendofluck.com | https://github.com/thebackendofluck/book
# SPDX-License-Identifier: Apache-2.0
#
# FOR TESTING AND EVALUATION ONLY. NOT FOR PRODUCTION USE.
# Published to demonstrate the patterns explained in the book. This code is
# not certified for real-money gaming: operating a gambling platform requires
# your own licence, independent test-lab certification (GLI, eCOGRA or
# equivalent) and regulator approval.

# benchmark-mtls.sh — Performance comparison: without mTLS vs with mTLS
#
# Runs 50 HSM encrypt calls in each mode and reports p50/p95/p99/avg latencies.
#
# Results from ops-host (2026-03-30):
#   Without mTLS (HTTP direct): p50=14ms  p95=16ms  p99=28ms  avg=13ms
#   With mTLS    (HTTPS+cert):  p50=21ms  p95=25ms  p99=26ms  avg=21ms
#   Overhead: ~7ms p50 (~50% relative, negligible in absolute terms)
#
# Run as root on ops-host:
#   bash benchmark-mtls.sh
#
# The mTLS overhead comes from:
#   - TLS 1.3 handshake: ~3-5ms (one-time per connection, amortised with keepalive)
#   - Client cert verification: ~1-2ms
#   - nginx proxy hop: ~1ms

set -euo pipefail

MTLS_PORT="8443"
DIRECT_PORT="8190"
N_REQUESTS="${1:-50}"

GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m'

info() { echo -e "${YELLOW}[bench]${NC} $*"; }
result() { echo -e "${GREEN}[result]${NC} $*"; }

# ── Get API key ────────────────────────────────────────────────────────────────
HSM_PID="$(ss -tlnp | grep 8190 | grep -oP 'pid=\K[0-9]+' | head -1)"
HSM_KEY="$(cat /proc/"${HSM_PID}"/environ 2>/dev/null | tr '\0' '\n' | grep HSM_API_KEY | cut -d= -f2)"

if [ -z "${HSM_KEY}" ]; then
    echo "ERROR: HSM_API_KEY not found — is hsm-proxy running on 8190?" >&2
    exit 1
fi

PAYLOAD='{"plaintext":"dGVzdA==","key_name":"field-cipher"}'

# ── Benchmark 1: Without mTLS (HTTP direct to uvicorn) ────────────────────────
info "Benchmark 1: ${N_REQUESTS} requests WITHOUT mTLS (HTTP direct to port ${DIRECT_PORT})"
RESULTS_FILE_NOMTLS="/tmp/bench-nomtls.txt"
rm -f "${RESULTS_FILE_NOMTLS}"

for _i in $(seq 1 "${N_REQUESTS}"); do
    START="$(date +%s%N)"
    curl -sf -X POST "http://127.0.0.1:${DIRECT_PORT}/hsm/encrypt" \
        -H "X-API-Key: ${HSM_KEY}" \
        -H "Content-Type: application/json" \
        -d "${PAYLOAD}" > /dev/null
    END="$(date +%s%N)"
    echo $(( (END - START) / 1000000 )) >> "${RESULTS_FILE_NOMTLS}"
done

STATS_NOMTLS="$(sort -n "${RESULTS_FILE_NOMTLS}" | awk \
    'NR==int('"${N_REQUESTS}"'*0.50){p50=$1}
     NR==int('"${N_REQUESTS}"'*0.95){p95=$1}
     NR==int('"${N_REQUESTS}"'*0.99)+1{p99=$1}
     {sum+=$1} END{printf "p50=%dms p95=%dms p99=%dms avg=%dms",p50,p95,p99,sum/NR}')"
result "Without mTLS: ${STATS_NOMTLS}"

# ── Benchmark 2: With mTLS (HTTPS + client cert through nginx) ────────────────
info "Benchmark 2: ${N_REQUESTS} requests WITH mTLS (HTTPS port ${MTLS_PORT})"
RESULTS_FILE_MTLS="/tmp/bench-mtls.txt"
rm -f "${RESULTS_FILE_MTLS}"

for _i in $(seq 1 "${N_REQUESTS}"); do
    START="$(date +%s%N)"
    curl -sf -k --no-sessionid \
        --cert /tmp/worker-client.crt --key /tmp/worker-client.key \
        -X POST "https://127.0.0.1:${MTLS_PORT}/hsm-api/hsm/encrypt" \
        -H "X-API-Key: ${HSM_KEY}" \
        -H "Content-Type: application/json" \
        -d "${PAYLOAD}" > /dev/null
    END="$(date +%s%N)"
    echo $(( (END - START) / 1000000 )) >> "${RESULTS_FILE_MTLS}"
done

STATS_MTLS="$(sort -n "${RESULTS_FILE_MTLS}" | awk \
    'NR==int('"${N_REQUESTS}"'*0.50){p50=$1}
     NR==int('"${N_REQUESTS}"'*0.95){p95=$1}
     NR==int('"${N_REQUESTS}"'*0.99)+1{p99=$1}
     {sum+=$1} END{printf "p50=%dms p95=%dms p99=%dms avg=%dms",p50,p95,p99,sum/NR}')"
result "With mTLS:    ${STATS_MTLS}"

echo ""
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo "  mTLS Performance Benchmark (${N_REQUESTS} requests each)"
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo "  Without mTLS: ${STATS_NOMTLS}"
echo "  With mTLS:    ${STATS_MTLS}"
echo ""
echo "  Note: --no-sessionid forces a full TLS handshake each time."
echo "  In production, Workers reuse TLS sessions — mTLS overhead"
echo "  is amortised to ~1-2ms per request after first connection."
echo "  (Cloudflare Workers maintain persistent connections to origins.)"
echo ""
echo "  PCI DSS Req 4.2.1 impact: ZERO — latency increase is within"
echo "  the existing HSM operation budget (30-80ms total)."
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
