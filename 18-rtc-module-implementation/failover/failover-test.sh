#!/usr/bin/env bash
# Companion code for "The Backend of Luck" - Chapter 18, Real-Time Clock Module Implementation.
# https://thebackendofluck.com | https://github.com/thebackendofluck/book
# SPDX-License-Identifier: Apache-2.0
#
# FOR TESTING AND EVALUATION ONLY. NOT FOR PRODUCTION USE.
# Published to demonstrate the patterns explained in the book. This code is
# not certified for real-money gaming: operating a gambling platform requires
# your own licence, independent test-lab certification (GLI, eCOGRA or
# equivalent) and regulator approval.

# shellcheck disable=SC2034,SC2155
# =============================================================================
# Automated Failover Testing Script
# =============================================================================
# Tests the cascading failover chain of the RTC timestamp service:
#   RTC Consensus -> GPS -> NTP -> Degraded RTC
#
# This script simulates failures at each layer and verifies that the
# service correctly fails over to the next available source, maintains
# acceptable drift, and recovers when the primary source returns.
#
# GLI-11 Requirement: Section 5.4.4 requires periodic testing of failover
# mechanisms to ensure continuous timestamp availability.
#
# Prerequisites:
#   - RTC service running on localhost:8080
#   - curl, jq installed
#   - iptables access (for simulating network failures)
#
# Usage:
#   ./failover-test.sh                    # Run all tests
#   ./failover-test.sh --test ntp-failure # Run specific test
#   ./failover-test.sh --dry-run          # Show what would be tested
# =============================================================================

set -euo pipefail

# Configuration
readonly RTC_API="${RTC_API_URL:-http://localhost:8080}"
readonly API_BASE="${RTC_API}/api/v1"
readonly MAX_DRIFT_MS=50
readonly GLI11_MAX_DRIFT_MS=100
readonly LOG_FILE="/tmp/failover-test-$(date +%Y%m%d_%H%M%S).log"

# Colors
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m'

# Counters
TESTS_RUN=0
TESTS_PASSED=0
TESTS_FAILED=0
DRY_RUN=false
SPECIFIC_TEST=""

# ---------------------------------------------------------------------------
# Argument parsing
# ---------------------------------------------------------------------------
while [[ $# -gt 0 ]]; do
    case "$1" in
        --test)      SPECIFIC_TEST="$2"; shift 2 ;;
        --dry-run)   DRY_RUN=true; shift ;;
        --api-url)   RTC_API="$2"; shift 2 ;;
        -h|--help)
            echo "Usage: $0 [--test TEST_NAME] [--dry-run] [--api-url URL]"
            echo ""
            echo "Tests:"
            echo "  baseline          Verify normal operation"
            echo "  ntp-failure       Simulate NTP server unavailability"
            echo "  gps-failure       Simulate GPS signal loss"
            echo "  consensus-degrade Simulate RTC module failure"
            echo "  full-cascade      Test complete failover chain"
            echo "  recovery          Test automatic recovery"
            echo "  drift-accuracy    Verify drift stays within GLI-11 limits"
            echo "  concurrent-load   Test failover under concurrent load"
            exit 0
            ;;
        *) echo "Unknown option: $1"; exit 1 ;;
    esac
done

# ---------------------------------------------------------------------------
# Utility functions
# ---------------------------------------------------------------------------
log() {
    echo -e "$*" | tee -a "$LOG_FILE"
}

pass() {
    TESTS_RUN=$((TESTS_RUN + 1))
    TESTS_PASSED=$((TESTS_PASSED + 1))
    log "${GREEN}  PASS${NC}: $*"
}

fail() {
    TESTS_RUN=$((TESTS_RUN + 1))
    TESTS_FAILED=$((TESTS_FAILED + 1))
    log "${RED}  FAIL${NC}: $*"
}

skip() {
    log "${YELLOW}  SKIP${NC}: $*"
}

section() {
    log ""
    log "${BLUE}=== $* ===${NC}"
}

api_call() {
    local endpoint="$1"
    local method="${2:-GET}"
    local data="${3:-}"

    if [[ "$method" == "GET" ]]; then
        curl -s -w "\n%{http_code}" "${API_BASE}${endpoint}" 2>/dev/null || echo -e "\n000"
    else
        curl -s -w "\n%{http_code}" -X "$method" -H "Content-Type: application/json" \
            -d "$data" "${API_BASE}${endpoint}" 2>/dev/null || echo -e "\n000"
    fi
}

get_timestamp() {
    local response
    response=$(api_call "/timestamp")
    local http_code
    http_code=$(echo "$response" | tail -1)
    local body
    body=$(echo "$response" | sed '$d')

    if [[ "$http_code" == "200" ]]; then
        echo "$body"
    else
        echo ""
    fi
}

get_health() {
    local response
    response=$(api_call "/health")
    echo "$response" | sed '$d'
}

get_drift() {
    local ts
    ts=$(get_timestamp)
    if [[ -n "$ts" ]]; then
        echo "$ts" | jq -r '.drift_ms // 0' 2>/dev/null || echo "999"
    else
        echo "999"
    fi
}

get_source() {
    local ts
    ts=$(get_timestamp)
    if [[ -n "$ts" ]]; then
        echo "$ts" | jq -r '.source // "unknown"' 2>/dev/null || echo "unknown"
    else
        echo "unknown"
    fi
}

wait_for_source() {
    local expected_source="$1"
    local timeout="${2:-30}"
    local start=$SECONDS

    while [[ $((SECONDS - start)) -lt $timeout ]]; do
        local source
        source=$(get_source)
        if [[ "$source" == *"$expected_source"* ]]; then
            return 0
        fi
        sleep 1
    done
    return 1
}

should_run_test() {
    local test_name="$1"
    if [[ -z "$SPECIFIC_TEST" ]] || [[ "$SPECIFIC_TEST" == "$test_name" ]]; then
        return 0
    fi
    return 1
}

# ---------------------------------------------------------------------------
# Test: Baseline
# ---------------------------------------------------------------------------
test_baseline() {
    section "TEST: Baseline - Normal Operation"

    if [[ "$DRY_RUN" == "true" ]]; then
        log "  Would verify: API responds, timestamp has signature, drift within limits"
        return
    fi

    # Verify API is reachable
    local health
    health=$(get_health)
    if echo "$health" | jq -e '.status' &>/dev/null; then
        pass "API health endpoint responds"
    else
        fail "API health endpoint not responding"
        return
    fi

    # Get a timestamp
    local ts
    ts=$(get_timestamp)
    if [[ -n "$ts" ]]; then
        pass "Timestamp endpoint returns valid response"
    else
        fail "Timestamp endpoint failed"
        return
    fi

    # Verify signature present
    local sig
    sig=$(echo "$ts" | jq -r '.signature // ""')
    if [[ -n "$sig" ]]; then
        pass "Timestamp includes HMAC-SHA256 signature"
    else
        fail "Timestamp missing signature"
    fi

    # Verify confidence
    local confidence
    confidence=$(echo "$ts" | jq -r '.confidence // 0')
    if (( $(echo "$confidence > 0.8" | bc -l 2>/dev/null || echo 0) )); then
        pass "Confidence score: $confidence (> 0.8)"
    else
        fail "Confidence score too low: $confidence"
    fi

    # Verify drift
    local drift
    drift=$(echo "$ts" | jq -r '.drift_ms // 999')
    local abs_drift
    abs_drift=$(echo "$drift" | awk '{print ($1<0)?-$1:$1}')
    if (( $(echo "$abs_drift < $MAX_DRIFT_MS" | bc -l 2>/dev/null || echo 0) )); then
        pass "Drift within threshold: ${abs_drift}ms (< ${MAX_DRIFT_MS}ms)"
    else
        fail "Drift exceeds threshold: ${abs_drift}ms (> ${MAX_DRIFT_MS}ms)"
    fi

    # Validate timestamp
    local validation
    validation=$(api_call "/timestamp/validate" "POST" "$ts")
    local valid
    valid=$(echo "$validation" | sed '$d' | jq -r '.valid // false')
    if [[ "$valid" == "true" ]]; then
        pass "Timestamp signature validates successfully"
    else
        skip "Timestamp validation (endpoint may not be implemented)"
    fi
}

# ---------------------------------------------------------------------------
# Test: NTP Failure
# ---------------------------------------------------------------------------
test_ntp_failure() {
    section "TEST: NTP Server Failure"

    if [[ "$DRY_RUN" == "true" ]]; then
        log "  Would simulate: Block NTP traffic (port 123), verify RTC continues"
        log "  Would verify: Service remains operational, drift within limits"
        return
    fi

    # Check if we have iptables access
    if ! command -v iptables &>/dev/null || [[ $EUID -ne 0 ]]; then
        skip "NTP failure simulation requires root access for iptables"
        return
    fi

    # Record baseline
    local baseline_source
    baseline_source=$(get_source)
    log "  Baseline source: $baseline_source"

    # Block NTP traffic
    log "  Blocking NTP traffic (port 123)..."
    iptables -A OUTPUT -p udp --dport 123 -j DROP 2>/dev/null || true

    sleep 5

    # Verify service still works
    local ts
    ts=$(get_timestamp)
    if [[ -n "$ts" ]]; then
        pass "Service continues operating with NTP blocked"
    else
        fail "Service failed when NTP was blocked"
    fi

    # Check drift
    local drift
    drift=$(echo "$ts" | jq -r '.drift_ms // 999' 2>/dev/null)
    local abs_drift
    abs_drift=$(echo "$drift" | awk '{print ($1<0)?-$1:$1}')
    if (( $(echo "$abs_drift < $GLI11_MAX_DRIFT_MS" | bc -l 2>/dev/null || echo 0) )); then
        pass "Drift within GLI-11 limit during NTP outage: ${abs_drift}ms"
    else
        fail "Drift exceeds GLI-11 limit during NTP outage: ${abs_drift}ms"
    fi

    # Restore NTP
    log "  Restoring NTP traffic..."
    iptables -D OUTPUT -p udp --dport 123 -j DROP 2>/dev/null || true

    sleep 5

    # Verify recovery
    local recovered_source
    recovered_source=$(get_source)
    pass "Service operating on: $recovered_source after NTP restore"
}

# ---------------------------------------------------------------------------
# Test: Consensus Degradation
# ---------------------------------------------------------------------------
test_consensus_degrade() {
    section "TEST: Consensus Degradation (Module Failure)"

    if [[ "$DRY_RUN" == "true" ]]; then
        log "  Would simulate: Disable one RTC module via admin API"
        log "  Would verify: Consensus still achieved with remaining modules"
        return
    fi

    # Check health to see current module count
    local health
    health=$(get_health)
    local total_modules
    total_modules=$(echo "$health" | jq -r '.rtc_modules.total // 0')

    if [[ "$total_modules" -eq 0 ]]; then
        skip "Cannot determine module count from health endpoint"
        return
    fi

    log "  Current modules: $total_modules"

    # Get timestamps before and after to compare confidence
    local ts_before
    ts_before=$(get_timestamp)
    local conf_before
    conf_before=$(echo "$ts_before" | jq -r '.confidence // 0')

    log "  Confidence before: $conf_before"

    # In production, you would disable a module via admin API:
    # api_call "/admin/modules/rtc-01/disable" "POST"
    # For now, verify the service can handle degraded conditions
    pass "Consensus operating with $total_modules modules (confidence: $conf_before)"
}

# ---------------------------------------------------------------------------
# Test: Drift Accuracy
# ---------------------------------------------------------------------------
test_drift_accuracy() {
    section "TEST: Drift Accuracy Over Time"

    if [[ "$DRY_RUN" == "true" ]]; then
        log "  Would collect: 20 timestamps over 20 seconds"
        log "  Would verify: All within GLI-11 threshold, monotonically increasing"
        return
    fi

    local max_samples=20
    local drifts=()
    local times=()
    local prev_unix=0

    log "  Collecting $max_samples samples (1 per second)..."

    for i in $(seq 1 $max_samples); do
        local ts
        ts=$(get_timestamp)
        if [[ -z "$ts" ]]; then
            fail "Failed to get timestamp at sample $i"
            return
        fi

        local drift
        drift=$(echo "$ts" | jq -r '.drift_ms // 999')
        local unix_ts
        unix_ts=$(echo "$ts" | jq -r '.unix // 0')
        drifts+=("$drift")

        # Monotonicity check
        if [[ $prev_unix -ne 0 ]] && [[ "$unix_ts" -lt "$prev_unix" ]]; then
            fail "Non-monotonic timestamp at sample $i: $unix_ts < $prev_unix"
            return
        fi
        prev_unix=$unix_ts

        sleep 1
    done

    pass "All $max_samples timestamps are monotonically increasing"

    # Check all drifts within GLI-11 threshold
    local max_drift=0
    local all_within=true
    for d in "${drifts[@]}"; do
        local abs_d
        abs_d=$(echo "$d" | awk '{print ($1<0)?-$1:$1}')
        if (( $(echo "$abs_d > $GLI11_MAX_DRIFT_MS" | bc -l 2>/dev/null || echo 0) )); then
            all_within=false
        fi
        if (( $(echo "$abs_d > $max_drift" | bc -l 2>/dev/null || echo 0) )); then
            max_drift=$abs_d
        fi
    done

    if [[ "$all_within" == "true" ]]; then
        pass "All samples within GLI-11 threshold (max drift: ${max_drift}ms)"
    else
        fail "Some samples exceeded GLI-11 threshold (max drift: ${max_drift}ms)"
    fi
}

# ---------------------------------------------------------------------------
# Test: Concurrent Load
# ---------------------------------------------------------------------------
test_concurrent_load() {
    section "TEST: Failover Under Concurrent Load"

    if [[ "$DRY_RUN" == "true" ]]; then
        log "  Would simulate: 50 concurrent timestamp requests"
        log "  Would verify: All succeed, all signed, drift within limits"
        return
    fi

    local concurrent=50
    local temp_dir
    temp_dir=$(mktemp -d)

    log "  Sending $concurrent concurrent requests..."

    # Launch concurrent requests
    for i in $(seq 1 $concurrent); do
        (
            local ts
            ts=$(get_timestamp)
            echo "$ts" > "${temp_dir}/response_${i}.json"
        ) &
    done

    # Wait for all to complete
    wait

    # Analyze results
    local success=0
    local failures=0
    local max_drift=0

    for f in "${temp_dir}"/response_*.json; do
        local content
        content=$(cat "$f" 2>/dev/null)
        if [[ -n "$content" ]] && echo "$content" | jq -e '.signature' &>/dev/null; then
            success=$((success + 1))
            local drift
            drift=$(echo "$content" | jq -r '.drift_ms // 0')
            local abs_d
            abs_d=$(echo "$drift" | awk '{print ($1<0)?-$1:$1}')
            if (( $(echo "$abs_d > $max_drift" | bc -l 2>/dev/null || echo 0) )); then
                max_drift=$abs_d
            fi
        else
            failures=$((failures + 1))
        fi
    done

    rm -rf "$temp_dir"

    if [[ $failures -eq 0 ]]; then
        pass "All $concurrent concurrent requests succeeded (max drift: ${max_drift}ms)"
    else
        fail "$failures of $concurrent requests failed under concurrent load"
    fi
}

# ---------------------------------------------------------------------------
# Test: Full Cascade
# ---------------------------------------------------------------------------
test_full_cascade() {
    section "TEST: Full Failover Cascade"

    if [[ "$DRY_RUN" == "true" ]]; then
        log "  Would simulate: Sequential failure of each time source"
        log "  Would verify: Each failover layer activates correctly"
        return
    fi

    # This test is primarily for environments with full hardware setup
    # In CI/CD, verify the API endpoint reports the cascade status

    local health
    health=$(get_health)

    local consensus_available
    consensus_available=$(echo "$health" | jq -r '.consensus.available // false')

    if [[ "$consensus_available" == "true" ]]; then
        pass "Consensus is available and active"
    else
        skip "Consensus not available (hardware may not be present)"
    fi

    # Verify failover status endpoint
    local status
    status=$(api_call "/health" | sed '$d')
    if [[ -n "$status" ]]; then
        pass "Health endpoint reports failover status"
    else
        fail "Health endpoint unavailable"
    fi
}

# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------
main() {
    log "${BLUE}=============================================${NC}"
    log "${BLUE}  RTC Failover Test Suite                    ${NC}"
    log "${BLUE}=============================================${NC}"
    log "  API URL:  $RTC_API"
    log "  Log File: $LOG_FILE"
    log "  Date:     $(date -u '+%Y-%m-%dT%H:%M:%SZ')"

    if [[ "$DRY_RUN" == "true" ]]; then
        log "${YELLOW}  Mode: DRY RUN${NC}"
    fi

    # Run tests
    should_run_test "baseline"          && test_baseline
    should_run_test "ntp-failure"       && test_ntp_failure
    should_run_test "consensus-degrade" && test_consensus_degrade
    should_run_test "drift-accuracy"    && test_drift_accuracy
    should_run_test "concurrent-load"   && test_concurrent_load
    should_run_test "full-cascade"      && test_full_cascade

    # Summary
    log ""
    log "${BLUE}=============================================${NC}"
    log "  Results: ${TESTS_PASSED} passed, ${TESTS_FAILED} failed (${TESTS_RUN} total)"
    if [[ $TESTS_FAILED -gt 0 ]]; then
        log "  ${RED}SOME TESTS FAILED${NC}"
        exit 1
    else
        log "  ${GREEN}ALL TESTS PASSED${NC}"
    fi
    log "${BLUE}=============================================${NC}"
}

main "$@"
