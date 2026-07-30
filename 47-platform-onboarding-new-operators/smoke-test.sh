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

# smoke-test.sh
#
# Post-deployment smoke test for a newly provisioned operator environment.
#
# Invoked by the deploy-operator.yml CI/CD pipeline after each deployment:
#
#   ./scripts/smoke-test.sh \
#     --operator acme-casino \
#     --environment staging \
#     --tests health,auth,games,payments
#
# Exit codes:
#   0 — all selected tests passed
#   1 — one or more tests failed
#
# Chapter 47: Platform Onboarding — From Contract to First Real-Money Bet
# Script reference: scripts/chapter-47/smoke-test.sh

set -euo pipefail

# ── Defaults ──────────────────────────────────────────────────────────────────
OPERATOR=""
ENVIRONMENT="staging"
TESTS="health,auth,games,payments"
TIMEOUT=30          # seconds per HTTP check
PASS=0
FAIL=0

# ── Argument parsing ───────────────────────────────────────────────────────────
while [[ $# -gt 0 ]]; do
    case "$1" in
        --operator)
            OPERATOR="$2"
            shift 2
            ;;
        --environment)
            ENVIRONMENT="$2"
            shift 2
            ;;
        --tests)
            TESTS="$2"
            shift 2
            ;;
        --timeout)
            TIMEOUT="$2"
            shift 2
            ;;
        *)
            echo "Unknown argument: $1" >&2
            exit 1
            ;;
    esac
done

if [[ -z "$OPERATOR" ]]; then
    echo "ERROR: --operator is required" >&2
    exit 1
fi

# ── Derived values ─────────────────────────────────────────────────────────────
BASE_URL="https://${OPERATOR}.platform.com"
if [[ "$ENVIRONMENT" == "staging" ]]; then
    BASE_URL="https://${OPERATOR}-staging.platform.com"
fi

# ── Helpers ───────────────────────────────────────────────────────────────────
log_pass() {
    echo "  [PASS] $1"
    PASS=$(( PASS + 1 ))
}

log_fail() {
    echo "  [FAIL] $1" >&2
    FAIL=$(( FAIL + 1 ))
}

http_get() {
    local url="$1"
    local expected_status="${2:-200}"
    local actual_status

    actual_status=$(curl -s -o /dev/null -w "%{http_code}" \
        --max-time "$TIMEOUT" "$url" 2>/dev/null || echo "000")

    if [[ "$actual_status" == "$expected_status" ]]; then
        return 0
    else
        echo "    Expected HTTP $expected_status, got $actual_status for $url" >&2
        return 1
    fi
}

# ── Test suites ────────────────────────────────────────────────────────────────

test_health() {
    echo ""
    echo "── Health checks ────────────────────────────────────────"

    if http_get "${BASE_URL}/api/health"; then
        log_pass "API health endpoint responding"
    else
        log_fail "API health endpoint not responding"
    fi

    if http_get "${BASE_URL}/api/health/db"; then
        log_pass "Database connectivity check"
    else
        log_fail "Database connectivity check failed"
    fi

    if http_get "${BASE_URL}/api/health/cache"; then
        log_pass "Cache (Redis) connectivity check"
    else
        log_fail "Cache (Redis) connectivity check failed"
    fi

    if http_get "${BASE_URL}/"; then
        log_pass "Frontend serving correctly (HTTP 200)"
    else
        log_fail "Frontend not serving correctly"
    fi
}

test_auth() {
    echo ""
    echo "── Auth checks ──────────────────────────────────────────"

    # Registration endpoint should be available
    if http_get "${BASE_URL}/api/auth/register" 405; then
        # 405 Method Not Allowed on GET is expected for a POST-only endpoint
        log_pass "Registration endpoint reachable (POST-only)"
    elif http_get "${BASE_URL}/api/auth/register" 200; then
        log_pass "Registration endpoint reachable"
    else
        log_fail "Registration endpoint not reachable"
    fi

    # Login endpoint should be available
    if http_get "${BASE_URL}/api/auth/login" 405; then
        log_pass "Login endpoint reachable (POST-only)"
    elif http_get "${BASE_URL}/api/auth/login" 200; then
        log_pass "Login endpoint reachable"
    else
        log_fail "Login endpoint not reachable"
    fi

    # Unauthenticated access to a protected resource should return 401
    if http_get "${BASE_URL}/api/player/profile" 401; then
        log_pass "Protected endpoint returns 401 for unauthenticated requests"
    else
        log_fail "Protected endpoint does not enforce authentication"
    fi
}

test_games() {
    echo ""
    echo "── Games checks ─────────────────────────────────────────"

    if http_get "${BASE_URL}/api/games"; then
        log_pass "Games catalog endpoint responding"
    else
        log_fail "Games catalog endpoint not responding"
    fi

    if http_get "${BASE_URL}/api/games/providers"; then
        log_pass "Game providers endpoint responding"
    else
        log_fail "Game providers endpoint not responding"
    fi

    if http_get "${BASE_URL}/api/lobby"; then
        log_pass "Lobby API responding"
    else
        log_fail "Lobby API not responding"
    fi
}

test_payments() {
    echo ""
    echo "── Payment checks ───────────────────────────────────────"

    # PSP configuration endpoint (unauthenticated, returns available methods)
    if http_get "${BASE_URL}/api/payments/methods"; then
        log_pass "Payment methods endpoint responding"
    else
        log_fail "Payment methods endpoint not responding"
    fi

    # Payment flow requires auth — expect 401 for unauthenticated access
    if http_get "${BASE_URL}/api/payments/deposit" 401; then
        log_pass "Deposit endpoint enforces authentication"
    else
        log_fail "Deposit endpoint does not enforce authentication"
    fi

    if http_get "${BASE_URL}/api/payments/withdraw" 401; then
        log_pass "Withdrawal endpoint enforces authentication"
    else
        log_fail "Withdrawal endpoint does not enforce authentication"
    fi
}

# ── Main ──────────────────────────────────────────────────────────────────────
echo "======================================================"
echo "  Smoke test: ${OPERATOR} (${ENVIRONMENT})"
echo "  Base URL:   ${BASE_URL}"
echo "  Test suites: ${TESTS}"
echo "======================================================"

IFS=',' read -ra TEST_SUITE_LIST <<< "$TESTS"
for suite in "${TEST_SUITE_LIST[@]}"; do
    case "$suite" in
        health)   test_health ;;
        auth)     test_auth ;;
        games)    test_games ;;
        payments) test_payments ;;
        *)
            echo "WARNING: Unknown test suite '${suite}', skipping" >&2
            ;;
    esac
done

echo ""
echo "======================================================"
echo "  Results: ${PASS} passed, ${FAIL} failed"
echo "======================================================"

if [[ "$FAIL" -gt 0 ]]; then
    echo "SMOKE TEST FAILED" >&2
    exit 1
fi

echo "SMOKE TEST PASSED"
exit 0
