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

# Chapter 44 — Implementation Checklist for Cloudflare Workers iGaming Platform
# Run this script to verify all components are correctly configured before go-live
set -euo pipefail

PASS=0
FAIL=0
WARN=0

GREEN='\033[0;32m'
RED='\033[0;31m'
YELLOW='\033[1;33m'
NC='\033[0m' # No Color

check() {
    local label="$1"
    local status="$2"  # pass | fail | warn
    local detail="${3:-}"

    if [[ "$status" == "pass" ]]; then
        echo -e "  ${GREEN}[PASS]${NC} $label"
        PASS=$((PASS + 1))
    elif [[ "$status" == "warn" ]]; then
        echo -e "  ${YELLOW}[WARN]${NC} $label${detail:+ — $detail}"
        WARN=$((WARN + 1))
    else
        echo -e "  ${RED}[FAIL]${NC} $label${detail:+ — $detail}"
        FAIL=$((FAIL + 1))
    fi
}

run_check() {
    local label="$1"
    shift
    if "$@" &>/dev/null 2>&1; then
        check "$label" "pass"
        return 0
    else
        check "$label" "fail"
        return 1
    fi
}

section() {
    echo ""
    echo "─────────────────────────────────────────────"
    echo "  $1"
    echo "─────────────────────────────────────────────"
}

echo ""
echo "=============================================="
echo "  AcmeToCasino — Cloudflare Implementation"
echo "  Checklist v1.0"
echo "=============================================="

# ──────────────────────────────────────────────
# Section 1: Prerequisites
# ──────────────────────────────────────────────
section "1. Prerequisites"

# Node.js >= 18
if command -v node &>/dev/null; then
    NODE_VER=$(node --version | sed 's/v//' | cut -d. -f1)
    if [[ "$NODE_VER" -ge 18 ]]; then
        check "Node.js >= 18 (found v$(node --version | sed 's/v//'))" "pass"
    else
        check "Node.js >= 18" "fail" "found v$(node --version) — upgrade required"
    fi
else
    check "Node.js >= 18" "fail" "node not found in PATH"
fi

# npm
run_check "npm available" which npm

# TypeScript compiler
if npx tsc --version &>/dev/null 2>&1; then
    TSC_VER=$(npx tsc --version 2>/dev/null | awk '{print $2}')
    check "TypeScript available (v${TSC_VER})" "pass"
else
    check "TypeScript available" "fail" "npx tsc --version failed"
fi

# Wrangler CLI
if command -v npx &>/dev/null && npx wrangler --version &>/dev/null 2>&1; then
    WRANGLER_VER=$(npx wrangler --version 2>/dev/null | awk '{print $NF}')
    check "Wrangler CLI available (v${WRANGLER_VER})" "pass"
else
    check "Wrangler CLI available" "fail" "npx wrangler --version failed — run: npm install -D wrangler"
fi

# Wrangler auth
if npx wrangler whoami &>/dev/null 2>&1; then
    check "Wrangler authenticated (wrangler whoami)" "pass"
else
    check "Wrangler authenticated" "fail" "run: npx wrangler login"
fi

# ──────────────────────────────────────────────
# Section 2: Project Configuration
# ──────────────────────────────────────────────
section "2. Project Configuration"

# wrangler.toml exists
if [[ -f "wrangler.toml" ]]; then
    check "wrangler.toml present" "pass"
else
    check "wrangler.toml present" "fail" "not found in current directory"
fi

# package.json exists
if [[ -f "package.json" ]]; then
    check "package.json present" "pass"
else
    check "package.json present" "fail" "not found in current directory"
fi

# tsconfig.json exists
if [[ -f "tsconfig.json" ]]; then
    check "tsconfig.json present" "pass"
else
    check "tsconfig.json present" "fail" "not found in current directory"
fi

# schema.sql exists
if [[ -f "schema.sql" ]]; then
    check "schema.sql present" "pass"
else
    check "schema.sql present" "fail" "not found in current directory"
fi

# src/ directory with expected files
SRC_FILES=(index.ts auth.ts games.ts wallet.ts kyc.ts compliance.ts utils.ts payments.ts)
ALL_SRC=true
for f in "${SRC_FILES[@]}"; do
    if [[ ! -f "src/$f" ]]; then
        check "src/$f" "fail" "missing"
        ALL_SRC=false
    fi
done
if [[ "$ALL_SRC" == "true" ]]; then
    check "All src/*.ts modules present (${#SRC_FILES[@]} files)" "pass"
fi

# Check wrangler.toml for compatibility_date
if [[ -f "wrangler.toml" ]]; then
    if grep -q 'compatibility_date' wrangler.toml; then
        check "wrangler.toml: compatibility_date set" "pass"
    else
        check "wrangler.toml: compatibility_date set" "fail" "missing compatibility_date field"
    fi

    if grep -q 'nodejs_compat' wrangler.toml; then
        check "wrangler.toml: nodejs_compat flag present" "pass"
    else
        check "wrangler.toml: nodejs_compat flag present" "warn" "required for crypto API usage"
    fi

    if grep -q '\[\[d1_databases\]\]' wrangler.toml; then
        check "wrangler.toml: D1 database binding defined" "pass"
    else
        check "wrangler.toml: D1 database binding defined" "fail" "missing [[d1_databases]] block"
    fi

    if grep -q 'SESSIONS' wrangler.toml && grep -q 'CACHE' wrangler.toml; then
        check "wrangler.toml: KV namespaces (SESSIONS + CACHE) defined" "pass"
    else
        check "wrangler.toml: KV namespaces (SESSIONS + CACHE) defined" "fail" "missing KV namespace bindings"
    fi

    if grep -q '\[\[r2_buckets\]\]' wrangler.toml; then
        check "wrangler.toml: R2 bucket binding defined" "pass"
    else
        check "wrangler.toml: R2 bucket binding defined" "warn" "missing [[r2_buckets]] — required for KYC document storage"
    fi
fi

# ──────────────────────────────────────────────
# Section 3: Cloudflare Resources
# ──────────────────────────────────────────────
section "3. Cloudflare Resources"

# Extract database name from wrangler.toml
DB_NAME=""
if [[ -f "wrangler.toml" ]]; then
    DB_NAME=$(grep 'database_name' wrangler.toml | head -1 | sed 's/.*= *"//' | sed 's/".*//')
fi

if [[ -n "$DB_NAME" ]]; then
    if npx wrangler d1 info "$DB_NAME" &>/dev/null 2>&1; then
        check "D1 database '$DB_NAME' exists in Cloudflare" "pass"
    else
        check "D1 database '$DB_NAME' exists in Cloudflare" "fail" "run: npx wrangler d1 create $DB_NAME"
    fi
else
    check "D1 database name detected in wrangler.toml" "fail" "could not parse database_name"
fi

# Check KV namespaces are provisioned (verify IDs are non-placeholder)
if [[ -f "wrangler.toml" ]]; then
    KV_IDS=$(grep -A2 '\[\[kv_namespaces\]\]' wrangler.toml | grep '^\s*id\s*=' | awk -F'"' '{print $2}')
    if [[ -n "$KV_IDS" ]]; then
        # Check for obvious placeholder values
        if echo "$KV_IDS" | grep -qE 'YOUR_|PLACEHOLDER|xxxxxxxx'; then
            check "KV namespace IDs look real (not placeholder)" "fail" "replace placeholder IDs with real ones from wrangler kv namespace list"
        else
            check "KV namespace IDs look real (not placeholder)" "pass"
        fi
    else
        check "KV namespace IDs present in wrangler.toml" "fail" "no id fields found under [[kv_namespaces]]"
    fi
fi

# ──────────────────────────────────────────────
# Section 4: TypeScript / Build
# ──────────────────────────────────────────────
section "4. TypeScript Type-Check"

if [[ -f "tsconfig.json" ]]; then
    if npx tsc --noEmit &>/dev/null 2>&1; then
        check "TypeScript: no type errors (tsc --noEmit)" "pass"
    else
        # Capture errors for display
        TSC_ERRORS=$(npx tsc --noEmit 2>&1 | head -20)
        check "TypeScript: no type errors" "fail" "run 'npx tsc --noEmit' to see errors"
        echo "    First errors:"
        echo "$TSC_ERRORS" | while IFS= read -r line; do
            echo "      $line"
        done
    fi
else
    check "TypeScript type-check" "warn" "tsconfig.json not found — skipping"
fi

# ──────────────────────────────────────────────
# Section 5: Database Schema
# ──────────────────────────────────────────────
section "5. Database Schema"

if [[ -f "schema.sql" ]]; then
    EXPECTED_TABLES=(users games transactions bonuses kyc_records responsible_gambling_settings compliance_events security_events translations)
    SCHEMA_MISSING=false
    for tbl in "${EXPECTED_TABLES[@]}"; do
        if grep -q "CREATE TABLE.*$tbl" schema.sql; then
            true
        else
            check "schema.sql: table '$tbl'" "fail" "missing CREATE TABLE"
            SCHEMA_MISSING=true
        fi
    done
    if [[ "$SCHEMA_MISSING" == "false" ]]; then
        check "schema.sql: all 9 required tables defined" "pass"
    fi

    if grep -q 'CHECK.*status' schema.sql; then
        check "schema.sql: CHECK constraints present (status enums)" "pass"
    else
        check "schema.sql: CHECK constraints present" "warn" "status enum validation recommended"
    fi

    if grep -q 'strftime' schema.sql; then
        check "schema.sql: ISO-8601 timestamps via strftime" "pass"
    else
        check "schema.sql: ISO-8601 timestamps via strftime" "warn" "use strftime('%Y-%m-%dT%H:%M:%SZ','now') for timestamp defaults"
    fi
fi

# ──────────────────────────────────────────────
# Section 6: Security Configuration
# ──────────────────────────────────────────────
section "6. Security Configuration"

# Check secrets are configured (names only — wrangler secret list shows names, not values)
REQUIRED_SECRETS=(JWT_SECRET ENCRYPTION_KEY PAYMENT_PROCESSOR_KEY)
if npx wrangler secret list &>/dev/null 2>&1; then
    SECRET_LIST=$(npx wrangler secret list 2>/dev/null)
    for secret in "${REQUIRED_SECRETS[@]}"; do
        if echo "$SECRET_LIST" | grep -q "$secret"; then
            check "Secret '$secret' configured" "pass"
        else
            check "Secret '$secret' configured" "fail" "run: npx wrangler secret put $secret"
        fi
    done
else
    check "Wrangler secret list accessible" "warn" "could not verify secrets — ensure wrangler is authenticated"
fi

# Check for HSTS header in source
if grep -rq 'Strict-Transport-Security' src/ &>/dev/null 2>&1; then
    check "HSTS header set in Worker response" "pass"
else
    check "HSTS header set in Worker response" "warn" "add Strict-Transport-Security to all responses"
fi

# Check blocked jurisdictions are defined
if [[ -f "src/compliance.ts" ]]; then
    if grep -q 'BLOCKED_JURISDICTIONS\|isBlockedJurisdiction' src/compliance.ts; then
        check "Jurisdiction block list defined in compliance.ts" "pass"
    else
        check "Jurisdiction block list defined" "fail" "isBlockedJurisdiction function not found"
    fi
fi

# Check rate limiting is implemented
if [[ -f "src/index.ts" ]]; then
    if grep -q 'rate:' src/index.ts || grep -q 'rateLimitKey\|rateLimit' src/index.ts; then
        check "Rate limiting implemented in index.ts" "pass"
    else
        check "Rate limiting implemented in index.ts" "warn" "rate limiting recommended before production"
    fi
fi

# Check bot management check
if [[ -f "src/index.ts" ]]; then
    if grep -q 'botManagement\|botScore\|bot_score' src/index.ts; then
        check "Bot scoring check present in index.ts" "pass"
    else
        check "Bot scoring check present" "warn" "cf.botManagement.score check recommended"
    fi
fi

# ──────────────────────────────────────────────
# Section 7: SSL/TLS
# ──────────────────────────────────────────────
section "7. SSL/TLS"

WORKER_NAME=""
if [[ -f "wrangler.toml" ]]; then
    WORKER_NAME=$(grep '^name' wrangler.toml | head -1 | sed 's/.*= *"//' | sed 's/".*//')
fi

if [[ -n "$WORKER_NAME" ]]; then
    WORKER_URL="https://${WORKER_NAME}.workers.dev"
    if curl -sf --max-time 10 "${WORKER_URL}/health" &>/dev/null 2>&1; then
        check "Worker reachable at ${WORKER_URL}" "pass"

        # Verify TLS
        TLS_CHECK=$(curl -sI --max-time 10 "${WORKER_URL}/health" 2>/dev/null | head -5)
        if echo "$TLS_CHECK" | grep -q 'HTTP/2\|HTTP/1.1'; then
            check "TLS active on workers.dev endpoint" "pass"
        else
            check "TLS active on workers.dev endpoint" "warn" "could not verify TLS — check curl response"
        fi

        # Verify cloudflare server header
        if curl -sI --max-time 10 "${WORKER_URL}/health" 2>/dev/null | grep -qi 'server: cloudflare'; then
            check "CF-Ray header confirms edge delivery" "pass"
        fi
    else
        check "Worker reachable at ${WORKER_URL}" "warn" "could not reach endpoint — deploy first: npx wrangler deploy"
    fi
else
    check "Worker URL check" "warn" "could not determine worker name from wrangler.toml"
fi

# ──────────────────────────────────────────────
# Section 8: Payment Integration
# ──────────────────────────────────────────────
section "8. Payment Integration"

if [[ -f "src/payments.ts" ]]; then
    check "payments.ts module present" "pass"

    if grep -q 'verifyWebhookSignature\|HMAC\|hmac' src/payments.ts; then
        check "Webhook HMAC signature verification present" "pass"
    else
        check "Webhook HMAC signature verification" "fail" "webhook endpoint must verify signatures"
    fi

    if grep -q "action: 'ignored_already_terminal'\|already_terminal\|status.*completed\|idempoten" src/payments.ts; then
        check "Webhook idempotency guard present" "pass"
    else
        check "Webhook idempotency guard" "warn" "PSPs retry webhooks — guard against double-credit"
    fi

    if grep -q 'redirectUrl\|redirect_url\|hosted.*payment\|checkout' src/payments.ts; then
        check "Redirect-to-hosted-payment-page pattern (PCI SAQ A)" "pass"
    else
        check "PCI SAQ A redirect pattern" "warn" "ensure card data never enters the Worker"
    fi
else
    check "payments.ts module" "fail" "src/payments.ts not found"
fi

# ──────────────────────────────────────────────
# Section 9: Jurisdiction Compliance
# ──────────────────────────────────────────────
section "9. Jurisdiction Compliance"

if [[ -f "src/compliance.ts" ]]; then
    check "compliance.ts module present" "pass"

    # Self-exclusion
    if grep -q 'self_excluded\|self_exclusion' src/compliance.ts; then
        check "Self-exclusion implemented in compliance.ts" "pass"
    else
        check "Self-exclusion implemented" "fail" "required by UKGC and most regulators"
    fi

    # Deposit limits
    if grep -q 'deposit_limit\|depositLimit' src/compliance.ts; then
        check "Deposit limits implemented in compliance.ts" "pass"
    else
        check "Deposit limits implemented" "fail" "required by UKGC and most regulators"
    fi

    # Compliance event logging
    if grep -q 'compliance_events\|logComplianceEvent' src/compliance.ts; then
        check "Compliance event audit log (compliance_events table)" "pass"
    else
        check "Compliance event audit log" "fail" "required for regulatory audit trail"
    fi

    # Jurisdiction blocking returns 451
    if [[ -f "src/index.ts" ]]; then
        if grep -q '451' src/index.ts; then
            check "HTTP 451 (Legal Unavailable) used for blocked jurisdictions" "pass"
        else
            check "HTTP 451 for blocked jurisdictions" "warn" "RFC 7725 requires 451 for legally blocked content"
        fi
    fi
fi

# ──────────────────────────────────────────────
# Section 10: Monitoring
# ──────────────────────────────────────────────
section "10. Monitoring"

if grep -rq 'logSecurityEvent\|security_events' src/ &>/dev/null 2>&1; then
    check "Security event logging to D1 (security_events)" "pass"
else
    check "Security event logging" "warn" "log high-threat and bot events to D1 for audit"
fi

if grep -rq 'ctx.waitUntil\|waitUntil' src/ &>/dev/null 2>&1; then
    check "ctx.waitUntil used for non-critical path logging" "pass"
else
    check "ctx.waitUntil for async logging" "warn" "use waitUntil to avoid adding logging latency to responses"
fi

# Wrangler tail check (can only verify the command exists)
if npx wrangler tail --help &>/dev/null 2>&1; then
    check "wrangler tail available for real-time log streaming" "pass"
else
    check "wrangler tail available" "warn" "real-time log streaming unavailable"
fi

# ──────────────────────────────────────────────
# Section 11: Performance
# ──────────────────────────────────────────────
section "11. Performance"

# Bundle size check via dry run
if npx wrangler deploy --dry-run &>/dev/null 2>&1; then
    BUNDLE_SIZE=$(npx wrangler deploy --dry-run 2>&1 | grep -o '[0-9.]\+ KiB' | head -1)
    if [[ -n "$BUNDLE_SIZE" ]]; then
        SIZE_KB=$(echo "$BUNDLE_SIZE" | grep -o '[0-9.]\+' | head -1)
        # Warn if over 500KB (10MB limit, but large bundles slow deployment)
        if (( $(echo "$SIZE_KB > 500" | bc -l 2>/dev/null || echo 0) )); then
            check "Bundle size: ${BUNDLE_SIZE} (threshold: 500 KiB)" "warn" "large bundles increase cold start and upload time"
        else
            check "Bundle size: ${BUNDLE_SIZE} (within 500 KiB threshold)" "pass"
        fi
    else
        check "Bundle size check" "warn" "could not parse bundle size from dry-run output"
    fi
else
    check "Dry-run bundle check (wrangler deploy --dry-run)" "warn" "dry-run failed — resolve wrangler errors first"
fi

# ──────────────────────────────────────────────
# Summary
# ──────────────────────────────────────────────
TOTAL=$((PASS + FAIL + WARN))

echo ""
echo "=============================================="
echo "  Results"
echo "=============================================="
echo -e "  ${GREEN}Passed:${NC}   $PASS / $TOTAL"
echo -e "  ${YELLOW}Warnings:${NC} $WARN / $TOTAL"
echo -e "  ${RED}Failed:${NC}   $FAIL / $TOTAL"
echo "=============================================="

if [[ $FAIL -gt 0 ]]; then
    echo ""
    echo -e "  ${RED}Pre-launch blockers detected. Fix all [FAIL] items before go-live.${NC}"
    echo ""
    exit 1
elif [[ $WARN -gt 0 ]]; then
    echo ""
    echo -e "  ${YELLOW}No blockers, but review [WARN] items before production traffic.${NC}"
    echo ""
    exit 0
else
    echo ""
    echo -e "  ${GREEN}All checks passed. Platform is ready for production.${NC}"
    echo ""
    exit 0
fi
