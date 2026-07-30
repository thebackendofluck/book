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

# keys.sh — Generate and set encryption keys as wrangler secrets
#
# Chapter 44, Section 6: Store Secrets
#
# This script generates cryptographically random keys and sets them as
# Wrangler secrets for all configured brands.  It never writes a key
# to disk, to stdout in a log-visible context, or to wrangler.toml.
#
# Secrets set by this script:
#   JWT_SECRET       — 256-bit HMAC-SHA256 signing key (64 hex chars)
#   ENCRYPTION_KEY   — 256-bit AES-256-GCM key (64 hex chars)
#
# Provider and PSP keys are NOT generated here — they are issued by
# the respective provider and must be set manually.
#
# Usage:
#   ./keys.sh                       # Set secrets for all brands (production)
#   ./keys.sh --brand acmevegas     # Set secrets for a single brand
#   ./keys.sh --rotate              # Regenerate and overwrite existing secrets
#   ./keys.sh --dry-run             # Print what would be set without setting
#
# Requirements:
#   - wrangler CLI in PATH (npx wrangler or global install)
#   - CLOUDFLARE_API_TOKEN set in environment OR `wrangler login` completed
#   - openssl(1) available for key generation

set -euo pipefail

# ─── Defaults ──────────────────────────────────────────────────────────────

TARGET_BRAND=""      # empty = all brands
ROTATE=0
DRY_RUN=0

BRANDS=("" "acmevegas" "acmegate" "acmedice")

# ─── Argument parsing ───────────────────────────────────────────────────────

while [ $# -gt 0 ]; do
    case "$1" in
        --brand)
            shift
            TARGET_BRAND="${1:-}"
            ;;
        --rotate)
            ROTATE=1
            ;;
        --dry-run)
            DRY_RUN=1
            ;;
        -h|--help)
            sed -n '2,/^$/p' "$0" | sed 's/^# *//'
            exit 0
            ;;
        *)
            printf 'Unknown option: %s\n' "$1" >&2
            exit 1
            ;;
    esac
    shift
done

# ─── Helpers ───────────────────────────────────────────────────────────────

log() {
    printf '[%s] %s\n' "$(date -u +%H:%M:%S)" "$*"
}

# Generate a 32-byte (256-bit) hex string using openssl.
# Falls back to /dev/urandom if openssl is not available.
generate_hex_key() {
    if command -v openssl > /dev/null 2>&1; then
        openssl rand -hex 32
    else
        # Portable fallback using /dev/urandom
        od -A n -t x1 -N 32 /dev/urandom | tr -d ' \n'
    fi
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
    if [ -f "$config" ]; then
        grep -m1 '^name' "$config" | sed 's/name *= *"\(.*\)"/\1/'
    else
        printf '(config not found: %s)' "$config"
    fi
}

# Set a single wrangler secret.
# In dry-run mode, prints what would be set without calling wrangler.
set_secret() {
    local name="$1"
    local value="$2"
    local config="$3"
    local worker_name
    worker_name="$(worker_name_for_brand "$config")"

    if [ "$DRY_RUN" = "1" ]; then
        log "DRY-RUN: would set ${name} for ${worker_name} (${config})"
        return 0
    fi

    if printf '%s' "$value" | npx wrangler secret put "$name" --config "$config" 2>&1; then
        log "Set ${name} for ${worker_name}"
    else
        log "ERROR: failed to set ${name} for ${worker_name}" >&2
        return 1
    fi
}

# ─── Per-brand secret rotation ─────────────────────────────────────────────

set_secrets_for_brand() {
    local brand="$1"
    local config
    config="$(config_for_brand "$brand")"
    local worker_name
    worker_name="$(worker_name_for_brand "$config")"

    if [ ! -f "$config" ] && [ "$DRY_RUN" = "0" ]; then
        log "WARNING: config '${config}' not found — skipping brand '${brand:-primary}'"
        return 0
    fi

    log "Generating secrets for ${worker_name} (${config})..."

    # Each brand gets its own independently generated key so that a compromise
    # of one brand's secret does not affect any other brand.
    local jwt_secret encryption_key
    jwt_secret="$(generate_hex_key)"
    encryption_key="$(generate_hex_key)"

    if [ "$ROTATE" = "1" ]; then
        log "Rotating secrets for ${worker_name} (existing values will be overwritten)"
    fi

    set_secret "JWT_SECRET"      "$jwt_secret"      "$config"
    set_secret "ENCRYPTION_KEY"  "$encryption_key"  "$config"

    log "Secrets generated for ${worker_name}"
    log "  JWT_SECRET:      ${jwt_secret:0:8}...${jwt_secret: -8} (truncated)"
    log "  ENCRYPTION_KEY:  ${encryption_key:0:8}...${encryption_key: -8} (truncated)"
    log ""
    log "  IMPORTANT: these values are not stored anywhere except Cloudflare's"
    log "  encrypted secret store.  Rotate them with: ./keys.sh --brand ${brand:-primary} --rotate"
}

# ─── Main ──────────────────────────────────────────────────────────────────

if [ "$DRY_RUN" = "1" ]; then
    log "Mode: DRY RUN — no secrets will be set"
fi

log "Checking wrangler availability..."
if ! npx wrangler --version > /dev/null 2>&1; then
    log "ERROR: wrangler CLI not found.  Install with: npm install -g wrangler" >&2
    exit 1
fi

if [ -n "$TARGET_BRAND" ]; then
    # Single-brand mode
    log "Target brand: ${TARGET_BRAND:-primary}"
    set_secrets_for_brand "$TARGET_BRAND"
else
    # All-brands mode
    log "Setting secrets for ${#BRANDS[@]} brand(s)..."
    failed=0
    for brand in "${BRANDS[@]}"; do
        if ! set_secrets_for_brand "$brand"; then
            failed=$((failed + 1))
        fi
    done

    if [ "$failed" -gt 0 ]; then
        log "ERROR: ${failed} brand(s) failed during secret rotation" >&2
        exit 1
    fi
fi

log "Done."
log ""
log "Next steps:"
log "  1. Set provider API keys manually (issued by your game provider):"
log "     npx wrangler secret put NETENT_API_KEY"
log "     npx wrangler secret put EVOLUTION_API_KEY"
log "     npx wrangler secret put PRAGMATIC_API_KEY"
log "     npx wrangler secret put PAYMENT_PROCESSOR_KEY"
log ""
log "  2. Set CF_API_TOKEN, CF_ZONE_ID, CF_ACCESS_AUDIENCE for analytics:"
log "     npx wrangler secret put CF_API_TOKEN"
log ""
log "  3. Deploy the Worker:"
log "     npx wrangler deploy"
log "     # or for multi-brand:"
log "     ./brands.sh"
