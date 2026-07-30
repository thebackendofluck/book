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

# brands.sh — Deploy all brands in parallel using brand-specific wrangler configs
#
# Chapter 44, Section 12: Deploying Four Brands in Parallel
#
# Each brand is deployed concurrently using a background job.  A failed
# deployment on one brand does not abort the others.  The script reports
# exit status per brand and exits non-zero if any deployment failed.
#
# Usage:
#   ./brands.sh                    # Deploy all brands to production
#   DRY_RUN=1 ./brands.sh          # Dry-run (no actual upload)
#   ENV=staging ./brands.sh        # Deploy all brands to staging environment
#
# Prerequisites:
#   CLOUDFLARE_API_TOKEN and CLOUDFLARE_ACCOUNT_ID must be set in the
#   environment (typically as CI secrets).  In local development, wrangler
#   reads credentials from ~/.wrangler/config/default.toml after `wrangler login`.

set -euo pipefail

# ─── Configuration ─────────────────────────────────────────────────────────

# Brand array: empty string → primary wrangler.toml; others → wrangler.<brand>.toml
# Add a new brand by appending its name here and creating the corresponding .toml file.
BRANDS=("" "acmevegas" "acmegate" "acmedice")

DRY_RUN="${DRY_RUN:-0}"
DEPLOY_ENV="${ENV:-}"

# ─── Helpers ───────────────────────────────────────────────────────────────

log() {
    printf '[%s] %s\n' "$(date -u +%H:%M:%S)" "$*"
}

config_for_brand() {
    local brand="$1"
    if [ -z "$brand" ]; then
        printf 'wrangler.toml'
    else
        printf 'wrangler.%s.toml' "$brand"
    fi
}

worker_name_for_brand() {
    local config="$1"
    grep -m1 '^name' "$config" | sed 's/name *= *"\(.*\)"/\1/'
}

deploy_brand() {
    local brand="$1"
    local config
    config="$(config_for_brand "$brand")"

    if [ ! -f "$config" ]; then
        log "ERROR: config file '$config' not found — skipping brand '${brand:-primary}'"
        return 1
    fi

    local worker_name
    worker_name="$(worker_name_for_brand "$config")"

    log "Deploying ${worker_name} (config: ${config})..."

    local wrangler_args=("deploy" "--config" "$config")

    if [ "$DRY_RUN" = "1" ]; then
        wrangler_args+=("--dry-run")
    fi

    if [ -n "$DEPLOY_ENV" ]; then
        wrangler_args+=("--env" "$DEPLOY_ENV")
    fi

    if npx wrangler "${wrangler_args[@]}" 2>&1 | sed "s/^/[${worker_name}] /"; then
        log "OK: ${worker_name} deployed successfully"
        return 0
    else
        log "FAIL: ${worker_name} deployment failed"
        return 1
    fi
}

# ─── Main ──────────────────────────────────────────────────────────────────

if [ "$DRY_RUN" = "1" ]; then
    log "Mode: DRY RUN (no upload will occur)"
fi

if [ -n "$DEPLOY_ENV" ]; then
    log "Environment: ${DEPLOY_ENV}"
fi

log "Deploying ${#BRANDS[@]} brand(s) in parallel..."

# Launch all deployments concurrently; collect PIDs
pids=()
brand_names=()

for brand in "${BRANDS[@]}"; do
    deploy_brand "$brand" &
    pids+=("$!")
    brand_names+=("${brand:-primary}")
done

# Wait for all background jobs and collect exit codes
failed=0
for i in "${!pids[@]}"; do
    pid="${pids[$i]}"
    name="${brand_names[$i]}"
    if wait "$pid"; then
        log "Brand '${name}': OK"
    else
        log "Brand '${name}': FAILED"
        failed=$((failed + 1))
    fi
done

# Summary
if [ "$failed" -eq 0 ]; then
    log "All ${#BRANDS[@]} brand(s) deployed successfully."
    exit 0
else
    log "ERROR: ${failed} of ${#BRANDS[@]} brand deployment(s) failed."
    exit 1
fi
