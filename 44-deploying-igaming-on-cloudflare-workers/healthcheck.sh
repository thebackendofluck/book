#!/bin/bash
# Companion code for "The Backend of Luck" - Chapter 44, Deploying iGaming Platforms on Cloudflare Workers.
# https://thebackendofluck.com | https://github.com/thebackendofluck/book
# SPDX-License-Identifier: Apache-2.0
#
# FOR TESTING AND EVALUATION ONLY. NOT FOR PRODUCTION USE.
# Published to demonstrate the patterns explained in the book. This code is
# not certified for real-money gaming: operating a gambling platform requires
# your own licence, independent test-lab certification (GLI, eCOGRA or
# equivalent) and regulator approval.

# AcmeToCasino — Cloudflare Workers Health Check
# Verifies all deployed workers are responding correctly

WORKERS=(
  "acmetocasino-api.teste.workers.dev"
  "brand-alpha-api.teste.workers.dev"
  "brand-beta-api.teste.workers.dev"
  "brand-gamma-api.teste.workers.dev"
  "brand-delta-api.teste.workers.dev"
)

echo "=== Cloudflare Workers Health Check ==="
echo "Timestamp: $(date -u +%Y-%m-%dT%H:%M:%SZ)"
echo ""

PASS=0
FAIL=0

for worker in "${WORKERS[@]}"; do
  HTTP_CODE=$(curl -s -o /dev/null -w "%{http_code}" "https://${worker}/" 2>/dev/null)
  LATENCY=$(curl -s -o /dev/null -w "%{time_total}" "https://${worker}/" 2>/dev/null)
  LATENCY_MS=$(echo "$LATENCY * 1000" | bc 2>/dev/null || echo "?")
  
  if [ "$HTTP_CODE" = "200" ]; then
    echo "  ✅ ${worker}: HTTP ${HTTP_CODE} (${LATENCY_MS}ms)"
    PASS=$((PASS + 1))
  else
    echo "  ❌ ${worker}: HTTP ${HTTP_CODE}"
    FAIL=$((FAIL + 1))
  fi
done

echo ""
echo "Results: ${PASS} passed, ${FAIL} failed"
[ "$FAIL" -eq 0 ] && echo "All workers healthy!" || echo "WARNING: ${FAIL} worker(s) unhealthy"
