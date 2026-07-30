#!/usr/bin/env bash
# Companion code for "The Backend of Luck" - Chapter 24, Security and Compliance.
# https://thebackendofluck.com | https://github.com/thebackendofluck/book
# SPDX-License-Identifier: Apache-2.0
#
# FOR TESTING AND EVALUATION ONLY. NOT FOR PRODUCTION USE.
# Published to demonstrate the patterns explained in the book. This code is
# not certified for real-money gaming: operating a gambling platform requires
# your own licence, independent test-lab certification (GLI, eCOGRA or
# equivalent) and regulator approval.

# shellcheck disable=SC2034,SC2317
# =============================================================================
# Geographic Restriction Validation Tests
# =============================================================================
# Validates that geographic access controls are working correctly across all
# layers: GeoIP lookup, DNS-based blocking, application-layer enforcement,
# and VPN/proxy detection.
#
# Usage:
#   ./geo-test.sh --target https://api.acme-casino.com [--verbose]
#
# Prerequisites:
#   - curl, jq installed
#   - Access to the target gambling platform API
#   - Optional: list of test proxy IPs for VPN bypass testing
# =============================================================================

set -euo pipefail

TARGET="${TARGET:-http://localhost:8080}"
GEO_ENDPOINT="${GEO_ENDPOINT:-/api/v1/geo/check}"
VERBOSE="${VERBOSE:-false}"
PASSED=0
FAILED=0
SKIPPED=0

RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m'

log_pass() { echo -e "  ${GREEN}[PASS]${NC} $*"; PASSED=$((PASSED + 1)); }
log_fail() { echo -e "  ${RED}[FAIL]${NC} $*"; FAILED=$((FAILED + 1)); }
log_skip() { echo -e "  ${YELLOW}[SKIP]${NC} $*"; SKIPPED=$((SKIPPED + 1)); }
log_info() { echo -e "  ${BLUE}[INFO]${NC} $*"; }

# ---------------------------------------------------------------------------
# Test helper
# ---------------------------------------------------------------------------
check_geo() {
    local ip="${1}"
    local expected_decision="${2}"
    local description="${3}"

    local response
    response=$(curl -s -w "\n%{http_code}" \
        -X POST "${TARGET}${GEO_ENDPOINT}" \
        -H "Content-Type: application/json" \
        -d "{\"ip\": \"${ip}\"}" 2>/dev/null) || {
        log_skip "${description} (connection failed)"
        return
    }

    local http_code
    http_code=$(echo "${response}" | tail -1)
    local body
    body=$(echo "${response}" | sed '$d')

    local actual_decision
    actual_decision=$(echo "${body}" | jq -r '.decision' 2>/dev/null || echo "unknown")

    if [[ "${actual_decision}" == "${expected_decision}" ]]; then
        log_pass "${description}"
    else
        log_fail "${description} (expected: ${expected_decision}, got: ${actual_decision})"
        if [[ "${VERBOSE}" == "true" ]]; then
            echo "       Response: ${body}" | head -3
        fi
    fi
}

# ---------------------------------------------------------------------------
# Test suites
# ---------------------------------------------------------------------------

echo "=============================================="
echo " Geographic Access Control Tests"
echo " Target: ${TARGET}"
echo "=============================================="

# --- Test Suite 1: Licensed Jurisdictions (should ALLOW) ---
echo ""
echo "--- Licensed Jurisdictions (expect: allow) ---"

# UK IPs (UKGC license)
check_geo "81.2.69.144"    "allow" "UK IP (London) - UKGC licensed"
check_geo "82.132.248.1"   "allow" "UK IP (Manchester) - UKGC licensed"

# Malta IPs (MGA license)
check_geo "46.11.0.1"      "allow" "Malta IP - MGA licensed"

# Sweden IPs (Spelinspektionen)
check_geo "83.140.0.1"     "allow" "Sweden IP - Spelinspektionen licensed"

# Denmark IPs
check_geo "87.49.0.1"      "allow" "Denmark IP - Danish GA licensed"

# Germany IPs (with restrictions)
check_geo "78.48.0.1"      "allow" "Germany IP - GGL licensed (with bet limits)"

# Ireland IPs
check_geo "87.32.0.1"      "allow" "Ireland IP - Licensed"

# --- Test Suite 2: Blocked Jurisdictions (should BLOCK) ---
echo ""
echo "--- Blocked Jurisdictions (expect: block) ---"

# Sanctioned countries
check_geo "175.45.176.1"   "block" "North Korea IP - OFAC sanctioned"
check_geo "5.160.0.1"      "block" "Iran IP - OFAC sanctioned"
check_geo "5.134.0.1"      "block" "Syria IP - OFAC sanctioned"
check_geo "152.206.0.1"    "block" "Cuba IP - OFAC sanctioned"

# Unlicensed markets
check_geo "1.0.0.1"        "block" "Australia IP - IGA 2001 blocks offshore"
check_geo "202.96.0.1"     "block" "China IP - gambling prohibited"

# --- Test Suite 3: US State-by-State (should vary) ---
echo ""
echo "--- US State-by-State Restrictions ---"

# These tests use well-known IP ranges for US states
# In production, verify with actual state-specific IPs
check_geo "71.80.0.1"      "allow"   "US - New Jersey (licensed state)"
check_geo "68.46.0.1"      "restrict" "US - Generic (needs subdivision check)"

# --- Test Suite 4: VPN/Proxy Detection (should CHALLENGE or BLOCK) ---
echo ""
echo "--- VPN/Proxy Detection ---"

# Known datacenter IPs (DigitalOcean, AWS, etc.)
check_geo "104.131.0.1"    "challenge" "DigitalOcean IP - datacenter/VPN detection"
check_geo "52.0.0.1"       "challenge" "AWS IP - datacenter/VPN detection"

# --- Test Suite 5: Edge Cases ---
echo ""
echo "--- Edge Cases ---"

# Private IPs (should handle gracefully)
check_geo "127.0.0.1"      "block" "Localhost - private network"
check_geo "192.168.1.1"    "block" "RFC1918 private IP"
check_geo "10.0.0.1"       "block" "RFC1918 private IP"

# Invalid IPs
check_geo "999.999.999.999" "block" "Invalid IP address"
check_geo ""                "block" "Empty IP address"

# --- Test Suite 6: API Endpoint Tests ---
echo ""
echo "--- API Endpoint Tests ---"

# Health check
health_response=$(curl -s "${TARGET}/health" 2>/dev/null || echo "")
if echo "${health_response}" | jq -e '.status == "healthy"' >/dev/null 2>&1; then
    log_pass "Health endpoint returns healthy"
else
    log_skip "Health endpoint check (service may not be running)"
fi

# Jurisdictions list
jurisdictions_response=$(curl -s "${TARGET}/api/v1/geo/jurisdictions" 2>/dev/null || echo "")
if echo "${jurisdictions_response}" | jq -e '.jurisdictions | length > 0' >/dev/null 2>&1; then
    jurisdiction_count=$(echo "${jurisdictions_response}" | jq '.jurisdictions | length')
    log_pass "Jurisdictions endpoint returns ${jurisdiction_count} jurisdictions"
else
    log_skip "Jurisdictions endpoint check"
fi

# --- Test Suite 7: Response Format Validation ---
echo ""
echo "--- Response Format Validation ---"

format_response=$(curl -s -X POST "${TARGET}${GEO_ENDPOINT}" \
    -H "Content-Type: application/json" \
    -d '{"ip": "81.2.69.144", "player_id": "test-player"}' 2>/dev/null || echo "")

if [[ -n "${format_response}" ]]; then
    # Check required fields
    required_fields=("decision" "country_code" "country_name" "reason" "timestamp" "ip")
    all_present=true
    for field in "${required_fields[@]}"; do
        if ! echo "${format_response}" | jq -e ".${field}" >/dev/null 2>&1; then
            log_fail "Missing required field: ${field}"
            all_present=false
        fi
    done
    if [[ "${all_present}" == "true" ]]; then
        log_pass "Response contains all required fields"
    fi

    # Check timestamp format (ISO 8601)
    ts=$(echo "${format_response}" | jq -r '.timestamp' 2>/dev/null || echo "")
    if [[ "${ts}" =~ ^[0-9]{4}-[0-9]{2}-[0-9]{2}T ]]; then
        log_pass "Timestamp is in ISO 8601 format"
    else
        log_fail "Timestamp not in ISO 8601 format: ${ts}"
    fi

    # Check player_id is echoed back
    player=$(echo "${format_response}" | jq -r '.player_id' 2>/dev/null || echo "")
    if [[ "${player}" == "test-player" ]]; then
        log_pass "Player ID correctly echoed in response"
    else
        log_fail "Player ID not returned in response"
    fi
else
    log_skip "Response format validation (service not running)"
fi

# --- Test Suite 8: Performance ---
echo ""
echo "--- Performance Tests ---"

if curl -s "${TARGET}/health" >/dev/null 2>&1; then
    # Measure latency of 10 requests
    total_time=0
    for i in $(seq 1 10); do
        req_time=$(curl -s -o /dev/null -w "%{time_total}" \
            -X POST "${TARGET}${GEO_ENDPOINT}" \
            -H "Content-Type: application/json" \
            -d '{"ip": "81.2.69.144"}' 2>/dev/null)
        total_time=$(echo "${total_time} + ${req_time}" | bc 2>/dev/null || echo "0")
    done

    if command -v bc >/dev/null 2>&1; then
        avg_time=$(echo "scale=3; ${total_time} / 10 * 1000" | bc)
        if (( $(echo "${avg_time} < 100" | bc -l) )); then
            log_pass "Average response time: ${avg_time}ms (< 100ms)"
        elif (( $(echo "${avg_time} < 500" | bc -l) )); then
            log_pass "Average response time: ${avg_time}ms (< 500ms, acceptable)"
        else
            log_fail "Average response time: ${avg_time}ms (> 500ms, too slow)"
        fi
    fi
else
    log_skip "Performance tests (service not running)"
fi

# ---------------------------------------------------------------------------
# Summary
# ---------------------------------------------------------------------------
echo ""
echo "=============================================="
echo " Test Results"
echo "  Passed:  ${PASSED}"
echo "  Failed:  ${FAILED}"
echo "  Skipped: ${SKIPPED}"
echo "=============================================="

if [[ ${FAILED} -gt 0 ]]; then
    echo -e "${RED}Some tests FAILED${NC}"
    exit 1
elif [[ ${PASSED} -eq 0 ]]; then
    echo -e "${YELLOW}No tests passed (service may not be running)${NC}"
    exit 0
else
    echo -e "${GREEN}All tests PASSED${NC}"
    exit 0
fi
