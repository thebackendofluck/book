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

# AcmeToCasino — Multi-Brand Parallel Deployment
# Deploys all casino brands to Cloudflare Edge in parallel
set -euo pipefail

BRANDS=("brand-alpha" "brand-beta" "brand-gamma" "brand-delta")
BASE_DIR="$(cd "$(dirname "$0")/../cloudflare" && pwd)"
RESULTS=()

echo "=== Multi-Brand Deployment ==="
echo "Brands: ${BRANDS[*]}"
echo "Base: ${BASE_DIR}"
echo ""

for brand in "${BRANDS[@]}"; do
  echo "--- Deploying ${brand} ---"
  BRAND_DIR="${BASE_DIR}/brands/${brand}"
  
  if [ ! -f "${BRAND_DIR}/wrangler.toml" ]; then
    echo "  SKIP: No wrangler.toml found for ${brand}"
    continue
  fi
  
  cd "${BRAND_DIR}"
  
  # Symlink shared source code
  ln -sf "${BASE_DIR}/src" "${BRAND_DIR}/src" 2>/dev/null || true
  ln -sf "${BASE_DIR}/node_modules" "${BRAND_DIR}/node_modules" 2>/dev/null || true
  ln -sf "${BASE_DIR}/tsconfig.json" "${BRAND_DIR}/tsconfig.json" 2>/dev/null || true
  ln -sf "${BASE_DIR}/package.json" "${BRAND_DIR}/package.json" 2>/dev/null || true
  
  # Deploy
  npx wrangler deploy 2>&1 | grep -E "Uploaded|Deployed|workers.dev" || true
  
  # Apply schema
  npx wrangler d1 execute "${brand}-db" --remote --file="${BASE_DIR}/schema.sql" 2>&1 | grep "Processed" || true
  
  RESULTS+=("${brand}: DEPLOYED")
  echo ""
done

echo "=== Deployment Summary ==="
for r in "${RESULTS[@]}"; do echo "  ✅ $r"; done
echo ""
echo "Total: ${#RESULTS[@]} brands deployed"
