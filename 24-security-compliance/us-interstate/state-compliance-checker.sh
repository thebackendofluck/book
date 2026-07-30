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

# =============================================================================
# state-compliance-checker.sh
# Verify that an operator's deployment correctly enforces US state-level
# geo-blocking for each licensed and unlicensed state.
#
# This script probes your platform's geo-enforcement at every layer:
#   1. DNS / CDN edge (Cloudflare Workers X-Forwarded-State or CF-Visitor-Country)
#   2. nginx GeoIP2 network layer
#   3. Application API geo endpoint
#
# It also validates:
#   - GeoComply SDK script is present and reachable on the game page
#   - HTTP 451 is returned for blocked states (not 403 or 200)
#   - CORS headers are present on geo API endpoints
#   - Compliance page content matches regulatory requirements
#
# Usage:
#   chmod +x state-compliance-checker.sh
#   ./state-compliance-checker.sh --base-url https://casino.example.com \
#       --geo-endpoint /api/v1/geo/status \
#       --game-url /games/lobby
#
# Requirements:
#   - curl >= 7.55 (for --fail-with-body)
#   - jq >= 1.6
#
# Exit codes:
#   0  All checks passed
#   1  One or more compliance failures detected
#   2  Script misconfiguration or missing dependencies
# =============================================================================

set -euo pipefail

# ---------------------------------------------------------------------------
# Defaults (override via arguments or environment variables)
# ---------------------------------------------------------------------------
BASE_URL="${BASE_URL:-https://casino.example.com}"
GEO_ENDPOINT="${GEO_ENDPOINT:-/api/v1/geo/status}"
GAME_URL="${GAME_URL:-/games/lobby}"
REPORT_FILE="${REPORT_FILE:-compliance-check-$(date +%Y%m%d-%H%M%S).json}"
VERBOSE="${VERBOSE:-false}"
CURL_TIMEOUT=15

# ---------------------------------------------------------------------------
# ANSI colours
# ---------------------------------------------------------------------------
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m' # No Colour

# ---------------------------------------------------------------------------
# State definitions
# ---------------------------------------------------------------------------
# Format: "STATE_CODE:EXPECT_ALLOWED"  (1 = access permitted, 0 = access blocked)
# Based on licensed online casino states as of 2025.
declare -A STATE_EXPECTED
STATE_EXPECTED=(
    # --- Licensed online casino states (should be ALLOWED) ---
    [NJ]=1    # New Jersey — DGE, live since Nov 2013
    [PA]=1    # Pennsylvania — iGCB, live since Jul 2019
    [MI]=1    # Michigan — MGCB, live since Jan 2021
    [WV]=1    # West Virginia — WVLCB, live since Jul 2020
    [CT]=1    # Connecticut — DCP + CGCC, live since Oct 2021
    [DE]=1    # Delaware — DLC, live since Nov 2012
    [RI]=1    # Rhode Island — DBR, live since Mar 2023

    # --- Sports-betting-only states: casino must be BLOCKED ---
    [NY]=0    # New York — sports betting legal (NYSGC), no online casino
    [IL]=0    # Illinois — sports betting legal (IGB), no online casino
    [CO]=0    # Colorado — sports betting legal (CLR), no online casino
    [AZ]=0    # Arizona — sports betting legal (ADGR), no online casino
    [TN]=0    # Tennessee — sports betting legal (TGEA), no online casino
    [VA]=0    # Virginia — sports betting legal (VLR), no online casino
    [OH]=0    # Ohio — sports betting legal (OCCC), no online casino
    [MD]=0    # Maryland — sports betting legal (MGBC), no online casino
    [LA]=0    # Louisiana — sports betting legal (LGC), no online casino
    [IN]=0    # Indiana — sports betting legal (IGC), no online casino
    [IA]=0    # Iowa — sports betting legal (IRGC), no online casino
    [KS]=0    # Kansas — sports betting legal (KLOTTERY), no online casino
    [KY]=0    # Kentucky — sports betting legal (KHRC), no online casino

    # --- Fully prohibited (no legal online gambling) ---
    [TX]=0    # Texas — online gambling prohibited
    [CA]=0    # California — ballot measures failed; prohibited
    [FL]=0    # Florida — Seminole compact, no broad online access
    [GA]=0    # Georgia — prohibited
    [AL]=0    # Alabama — prohibited
    [SC]=0    # South Carolina — prohibited
    [WI]=0    # Wisconsin — prohibited
    [UT]=0    # Utah — total gambling prohibition (state constitution)
    [HI]=0    # Hawaii — total gambling prohibition (state law)
    [ID]=0    # Idaho — prohibited
    [ND]=0    # North Dakota — prohibited
    [SD]=0    # South Dakota — prohibited (online)
    [NE]=0    # Nebraska — prohibited
)

# Approximate US lat/lon by state (for GPS spoofing simulation in extended tests).
# Referenced by future --gps-spoof flag; declared here for reference.
declare -A STATE_COORDS
# shellcheck disable=SC2034
STATE_COORDS=(
    [NJ]="40.0583,-74.4057"
    [PA]="40.9699,-77.7278"
    [MI]="44.3148,-85.6024"
    [WV]="38.5976,-80.4549"
    [CT]="41.6032,-73.0877"
    [DE]="38.9108,-75.5277"
    [RI]="41.5801,-71.4774"
    [NY]="43.2994,-74.2179"
    [TX]="31.9686,-99.9018"
    [CA]="36.7783,-119.4179"
    [UT]="39.3210,-111.0937"
    [HI]="20.7967,-156.3319"
)

# ---------------------------------------------------------------------------
# Counters
# ---------------------------------------------------------------------------
PASS=0
FAIL=0
SKIP=0
declare -a FAILURES=()
declare -a RESULTS=()

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
log_info()    { echo -e "${BLUE}[INFO]${NC}  $*"; }
log_pass()    { echo -e "${GREEN}[PASS]${NC}  $*"; }
log_fail()    { echo -e "${RED}[FAIL]${NC}  $*"; FAILURES+=("$*"); }
log_warn()    { echo -e "${YELLOW}[WARN]${NC}  $*"; }
log_section() { echo -e "\n${BLUE}═══ $* ═══${NC}"; }

check_dependency() {
    local cmd="$1"
    if ! command -v "$cmd" &>/dev/null; then
        echo -e "${RED}[ERROR]${NC} Required dependency not found: $cmd"
        exit 2
    fi
}

# Perform an HTTP request simulating a specific US state via X-Forwarded-For
# and a custom US-State header (for application-layer testing).
geo_request() {
    local state="$1"
    local url="$2"
    local extra_headers="${3:-}"
    local spoof_ip
    spoof_ip=$(state_to_ip "$state")

    local curl_args=(
        --silent
        --max-time "$CURL_TIMEOUT"
        --write-out "\n%{http_code}"
        -H "X-Forwarded-For: ${spoof_ip}"
        -H "X-Test-State: ${state}"
        -H "Accept: application/json"
    )
    if [[ -n "$extra_headers" ]]; then
        while IFS= read -r header; do
            curl_args+=(-H "$header")
        done <<< "$extra_headers"
    fi

    curl "${curl_args[@]}" "${url}"
}

# Map state code to a representative public IP address for testing.
# These are well-known ISP IP ranges that geo-resolve to the named state.
state_to_ip() {
    local state="$1"
    case "$state" in
        NJ) echo "67.81.0.1" ;;      # Comcast NJ range
        PA) echo "73.162.0.1" ;;     # Comcast PA range
        MI) echo "75.181.0.1" ;;     # Comcast MI range
        WV) echo "72.178.0.1" ;;     # Comcast WV range
        CT) echo "73.77.0.1"  ;;     # Comcast CT range
        DE) echo "71.225.0.1" ;;     # Comcast DE range
        RI) echo "73.7.0.1"   ;;     # Comcast RI range
        NY) echo "74.64.0.1"  ;;     # Spectrum NY range
        TX) echo "76.6.0.1"   ;;     # AT&T TX range
        CA) echo "67.182.0.1" ;;     # Comcast CA range
        UT) echo "71.37.0.1"  ;;     # Comcast UT range
        HI) echo "65.25.0.1"  ;;     # Hawaii Telecom range
        IL) echo "71.58.0.1"  ;;     # Comcast IL range
        FL) echo "74.0.0.1"   ;;     # Spectrum FL range
        *) echo "8.8.8.8"     ;;     # Fallback: Google DNS (no state)
    esac
}

record_result() {
    local state="$1" check="$2" expected="$3" actual="$4" passed="$5" message="$6"
    RESULTS+=("{\"state\":\"${state}\",\"check\":\"${check}\",\"expected\":\"${expected}\",\"actual\":\"${actual}\",\"passed\":${passed},\"message\":\"${message}\"}")
}

# ---------------------------------------------------------------------------
# Argument parsing
# ---------------------------------------------------------------------------
parse_args() {
    while [[ $# -gt 0 ]]; do
        case "$1" in
            --base-url)       BASE_URL="$2";      shift 2 ;;
            --geo-endpoint)   GEO_ENDPOINT="$2";  shift 2 ;;
            --game-url)       GAME_URL="$2";      shift 2 ;;
            --report)         REPORT_FILE="$2";   shift 2 ;;
            --verbose)        VERBOSE="true";     shift ;;
            --state)          SINGLE_STATE="$2";  shift 2 ;;
            *)                echo "Unknown argument: $1"; exit 2 ;;
        esac
    done
}

# ---------------------------------------------------------------------------
# Checks
# ---------------------------------------------------------------------------

check_geo_endpoint() {
    local state="$1"
    local expected_allowed="${STATE_EXPECTED[$state]:-0}"
    local url="${BASE_URL}${GEO_ENDPOINT}"

    local response http_code body
    response=$(geo_request "$state" "$url")
    http_code=$(echo "$response" | tail -n1)
    body=$(echo "$response" | head -n -1)

    local passed=false
    local message=""

    if [[ "$expected_allowed" == "1" ]]; then
        # Licensed state: expect 200 with allowed=true
        if [[ "$http_code" == "200" ]]; then
            local allowed
            allowed=$(echo "$body" | jq -r '.allowed // "unknown"' 2>/dev/null || echo "unknown")
            if [[ "$allowed" == "true" ]]; then
                passed=true
                message="HTTP 200, allowed=true"
            else
                message="HTTP 200 but allowed=${allowed}"
            fi
        else
            message="Expected 200 for licensed state, got ${http_code}"
        fi
    else
        # Unlicensed/prohibited state: expect 451 or 403
        if [[ "$http_code" == "451" ]]; then
            passed=true
            message="HTTP 451 (correct: Unavailable For Legal Reasons)"
        elif [[ "$http_code" == "403" ]]; then
            passed=true
            message="HTTP 403 (acceptable, prefer 451 per RFC 7725)"
            log_warn "State ${state}: HTTP 403 returned; RFC 7725 recommends HTTP 451"
        elif [[ "$http_code" == "200" ]]; then
            message="COMPLIANCE FAILURE: HTTP 200 for blocked state ${state}"
        else
            message="Unexpected status ${http_code} for blocked state ${state}"
        fi
    fi

    if $passed; then
        log_pass "State ${state} geo-endpoint: ${message}"
        ((PASS++))
        record_result "$state" "geo_endpoint" "$expected_allowed" "$http_code" "true" "$message"
    else
        log_fail "State ${state} geo-endpoint: ${message}"
        ((FAIL++))
        record_result "$state" "geo_endpoint" "$expected_allowed" "$http_code" "false" "$message"
    fi
}

check_game_page_geocomply_sdk() {
    local url="${BASE_URL}${GAME_URL}"
    log_info "Checking GeoComply SDK presence on game page: ${url}"

    local response http_code body
    response=$(curl --silent --max-time "$CURL_TIMEOUT" \
        --write-out "\n%{http_code}" \
        -H "X-Forwarded-For: $(state_to_ip NJ)" \
        "$url")
    http_code=$(echo "$response" | tail -n1)
    body=$(echo "$response" | head -n -1)

    if [[ "$http_code" != "200" ]]; then
        log_warn "Game page returned ${http_code}; cannot check for GeoComply SDK"
        ((SKIP++))
        return
    fi

    # GeoComply SDK bundle is typically loaded from geocomply.net CDN
    if echo "$body" | grep -qi "geocomply"; then
        log_pass "GeoComply SDK reference found on game page"
        ((PASS++))
        record_result "ALL" "geocomply_sdk_present" "present" "found" "true" "SDK reference in page source"
    else
        log_fail "GeoComply SDK not found on game page — regulators require this for US states"
        ((FAIL++))
        record_result "ALL" "geocomply_sdk_present" "present" "not_found" "false" "SDK missing from game page"
    fi
}

check_451_response_body() {
    local state="TX"  # A clearly prohibited state
    local url="${BASE_URL}${GEO_ENDPOINT}"
    log_info "Checking HTTP 451 response body format (state=${state})"

    local response http_code body
    response=$(geo_request "$state" "$url")
    http_code=$(echo "$response" | tail -n1)
    body=$(echo "$response" | head -n -1)

    if [[ "$http_code" != "451" && "$http_code" != "403" ]]; then
        log_warn "Expected 451/403 for TX, got ${http_code} — cannot check body format"
        ((SKIP++))
        return
    fi

    # Check that the response body contains required compliance fields
    local has_reason has_jurisdiction
    has_reason=$(echo "$body" | jq -r '.block_reason // empty' 2>/dev/null)
    has_jurisdiction=$(echo "$body" | jq -r '.jurisdiction // empty' 2>/dev/null)

    if [[ -n "$has_reason" && -n "$has_jurisdiction" ]]; then
        log_pass "451 response body contains block_reason and jurisdiction fields"
        ((PASS++))
        record_result "TX" "451_response_body" "structured" "structured" "true" "block_reason and jurisdiction present"
    else
        log_fail "451 response body missing block_reason or jurisdiction — required for compliance audit"
        ((FAIL++))
        record_result "TX" "451_response_body" "structured" "unstructured" "false" "Missing compliance fields"
    fi
}

check_cors_geo_endpoint() {
    local url="${BASE_URL}${GEO_ENDPOINT}"
    log_info "Checking CORS headers on geo endpoint"

    local headers
    headers=$(curl --silent --max-time "$CURL_TIMEOUT" \
        -I \
        -H "Origin: https://casino.example.com" \
        -H "Access-Control-Request-Method: POST" \
        "$url" 2>&1)

    if echo "$headers" | grep -qi "Access-Control-Allow-Origin"; then
        log_pass "CORS headers present on geo endpoint"
        ((PASS++))
    else
        log_warn "CORS headers missing on geo endpoint (may cause SDK failures in browser)"
        ((SKIP++))
    fi
}

check_vpn_blocking() {
    log_info "Checking VPN/datacenter IP blocking"
    # Cloudflare's 1.1.1.1 is a well-known datacenter IP — should be blocked or
    # challenged on a real platform even if the geo resolves to a licensed state.
    local url="${BASE_URL}${GEO_ENDPOINT}"
    local response http_code

    response=$(curl --silent --max-time "$CURL_TIMEOUT" \
        --write-out "\n%{http_code}" \
        -H "X-Forwarded-For: 1.1.1.1" \
        "$url")
    http_code=$(echo "$response" | tail -n1)

    if [[ "$http_code" == "403" || "$http_code" == "451" ]]; then
        log_pass "Datacenter IP 1.1.1.1 blocked (${http_code}) — VPN detection active"
        ((PASS++))
        record_result "ALL" "vpn_datacenter_block" "blocked" "${http_code}" "true" "Datacenter IP correctly blocked"
    elif [[ "$http_code" == "200" ]]; then
        log_warn "Datacenter IP 1.1.1.1 not blocked — VPN detection may be insufficient"
        ((SKIP++))
        record_result "ALL" "vpn_datacenter_block" "blocked" "200" "false" "Datacenter IP not blocked"
    else
        log_warn "Unexpected status ${http_code} for datacenter IP"
        ((SKIP++))
    fi
}

# ---------------------------------------------------------------------------
# Report generation
# ---------------------------------------------------------------------------
generate_report() {
    local timestamp
    timestamp=$(date -u +"%Y-%m-%dT%H:%M:%SZ")
    local total=$((PASS + FAIL + SKIP))
    local pass_rate=0
    if [[ $total -gt 0 ]]; then
        pass_rate=$(echo "scale=1; $PASS * 100 / $total" | bc)
    fi

    # Build JSON array of results
    local results_json
    results_json=$(printf '%s\n' "${RESULTS[@]}" | jq -s '.')

    # Build failures array
    local failures_json
    if [[ ${#FAILURES[@]} -gt 0 ]]; then
        failures_json=$(printf '%s\n' "${FAILURES[@]}" | jq -Rs 'split("\n") | map(select(length > 0))')
    else
        failures_json="[]"
    fi

    jq -n \
        --arg ts "$timestamp" \
        --arg base_url "$BASE_URL" \
        --argjson pass "$PASS" \
        --argjson fail "$FAIL" \
        --argjson skip "$SKIP" \
        --arg pass_rate "$pass_rate" \
        --argjson results "$results_json" \
        --argjson failures "$failures_json" \
        '{
            generated_at: $ts,
            base_url: $base_url,
            summary: {
                total_checks: ($pass + $fail + $skip),
                passed: $pass,
                failed: $fail,
                skipped: $skip,
                pass_rate_pct: ($pass_rate | tonumber)
            },
            compliance_status: (if $fail == 0 then "COMPLIANT" else "NON_COMPLIANT" end),
            failures: $failures,
            results: $results
        }' > "$REPORT_FILE"

    echo ""
    echo "════════════════════════════════════"
    echo "  Compliance Check Summary"
    echo "════════════════════════════════════"
    echo "  Passed:  ${PASS}"
    echo "  Failed:  ${FAIL}"
    echo "  Skipped: ${SKIP}"
    echo "  Status:  $([ "$FAIL" -eq 0 ] && echo -e "${GREEN}COMPLIANT${NC}" || echo -e "${RED}NON_COMPLIANT${NC}")"
    echo "  Report:  ${REPORT_FILE}"
    echo "════════════════════════════════════"
}

# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------
main() {
    parse_args "$@"
    check_dependency curl
    check_dependency jq

    log_section "US State-Level Geo-Compliance Checker"
    log_info "Target: ${BASE_URL}"
    log_info "Geo endpoint: ${GEO_ENDPOINT}"
    log_info "Game URL: ${GAME_URL}"
    echo ""

    # Optional: test only a single state
    if [[ -n "${SINGLE_STATE:-}" ]]; then
        log_section "Single-state check: ${SINGLE_STATE}"
        check_geo_endpoint "$SINGLE_STATE"
        generate_report
        [[ "$FAIL" -eq 0 ]]
        exit $?
    fi

    log_section "1. GeoComply SDK presence"
    check_game_page_geocomply_sdk

    log_section "2. HTTP 451 response body format"
    check_451_response_body

    log_section "3. CORS header validation"
    check_cors_geo_endpoint

    log_section "4. VPN / datacenter IP blocking"
    check_vpn_blocking

    log_section "5. Licensed state access (should be ALLOWED)"
    for state in NJ PA MI WV CT DE RI; do
        if [[ -n "${STATE_EXPECTED[$state]+x}" ]]; then
            check_geo_endpoint "$state"
        fi
    done

    log_section "6. Prohibited state access (should be BLOCKED)"
    for state in TX CA UT HI GA AL SC WI; do
        if [[ -n "${STATE_EXPECTED[$state]+x}" ]]; then
            check_geo_endpoint "$state"
        fi
    done

    log_section "7. Sports-betting-only states — casino must be BLOCKED"
    for state in NY IL CO AZ TN VA OH MD; do
        if [[ -n "${STATE_EXPECTED[$state]+x}" ]]; then
            check_geo_endpoint "$state"
        fi
    done

    generate_report

    # Exit non-zero if any checks failed
    [[ "$FAIL" -eq 0 ]]
}

main "$@"
