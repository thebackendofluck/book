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

# AcmeToCasino — Cloudflare Workers Deployment Script
# Deploys a single brand to Cloudflare Edge
set -euo pipefail

BRAND="${1:-acmetocasino}"
ENV="${2:-development}"

echo "=== Deploying ${BRAND} (${ENV}) ==="

# Step 1: Validate
echo "[1/5] Validating build..."
npx wrangler deploy --dry-run 2>&1 | grep "Total Upload"

# Step 2: Deploy worker
echo "[2/5] Deploying worker..."
npx wrangler deploy 2>&1 | grep -E "Uploaded|Deployed|workers.dev"

# Step 3: Apply database schema
echo "[3/5] Applying D1 schema..."
npx wrangler d1 execute "${BRAND}-db" --remote --file=schema.sql 2>&1 | grep "Processed"

# Step 4: Seed game data
echo "[4/5] Seeding games..."
npx wrangler d1 execute "${BRAND}-db" --remote --file=seed-games.sql 2>&1 | grep "Processed"

# Step 5: Health check
echo "[5/5] Health check..."
WORKER_URL=$(npx wrangler deploy --dry-run 2>&1 | grep "workers.dev" | awk '{print $1}')
curl -s "${WORKER_URL}/health" | python3 -m json.tool

echo "=== ${BRAND} deployed successfully ==="
