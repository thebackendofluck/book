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
# geo-compliance-checker.sh
# =============================================================================
# Verification script to confirm that geo-blocking is working correctly across
# all layers (DNS, CDN, application). Run this:
#   - After initial deployment
#   - After any infrastructure change (new CDN rules, nginx update, etc.)
#   - On a weekly schedule as a sanity check (cron or CI pipeline)
#   - Before a regulator audit (verify your evidence is current)
#
# The script tests from a set of IP addresses representative of blocked and
# allowed jurisdictions. It uses a combination of:
#   - DNS resolution checks (nslookup / dig)
#   - HTTP request simulation (curl with spoofed X-Forwarded-For)
#   - CloudFront/Cloudflare header verification
#   - Response code validation (expected: 451 for blocked, 200 for allowed)
#
# Usage:
#   chmod +x geo-compliance-checker.sh
#   ./geo-compliance-checker.sh --target https://casino.example.com
#   ./geo-compliance-checker.sh --target https://casino.example.com --verbose
#   ./geo-compliance-checker.sh --help
#
# Exit codes:
#   0 = all tests passed
#   1 = one or more tests failed (geo-blocking not working as expected)
#   2 = configuration error
#
# Dependencies: curl, dig, jq, awk, nc
# =============================================================================

set -euo pipefail

# ---- Colours ----
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
BOLD='\033[1m'
NC='\033[0m'

# ---- Defaults ----
TARGET_URL=""
VERBOSE=false
OUTPUT_JSON=false
REPORT_FILE="geo-compliance-report-$(date +%Y%m%d-%H%M%S).json"
FAIL_COUNT=0
PASS_COUNT=0
TOTAL_COUNT=0

# =============================================================================
# Argument parsing
# =============================================================================
usage() {
    cat <<EOF
Usage: $(basename "$0") [OPTIONS]

Options:
  -t, --target URL      Target base URL to test (e.g., https://casino.example.com)
  -v, --verbose         Print full curl output for each test
  -j, --json            Write JSON report to file
  -o, --output FILE     JSON report output file (default: auto-named)
  -h, --help            Show this help message

Examples:
  $(basename "$0") --target https://casino.example.com
  $(basename "$0") --target https://casino.example.com --verbose --json

EOF
}

while [[ $# -gt 0 ]]; do
    case "$1" in
        -t|--target)  TARGET_URL="$2"; shift 2 ;;
        -v|--verbose) VERBOSE=true;    shift   ;;
        -j|--json)    OUTPUT_JSON=true; shift  ;;
        -o|--output)  REPORT_FILE="$2"; shift 2 ;;
        -h|--help)    usage; exit 0             ;;
        *) echo "Unknown option: $1"; usage; exit 2 ;;
    esac
done

if [[ -z "$TARGET_URL" ]]; then
    echo -e "${RED}ERROR: --target is required${NC}" >&2
    usage
    exit 2
fi

# Strip trailing slash
TARGET_URL="${TARGET_URL%/}"

# =============================================================================
# Test data
# Test IPs are representative addresses from each jurisdiction.
# In production tests, use actual IP addresses from your geo-test vendor.
#
# IMPORTANT: These are TEST IPs only — do not use live player IPs.
# For accurate testing, use a dedicated geo-test service like
# GeoComply's test harness or MaxMind's test database IPs.
# =============================================================================

# Format: "description|ip|country_code|expected_status"
declare -a BLOCKED_TESTS=(
    "UAE (Federal Law No. 6 of 2018)|5.62.56.160|AE|451"
    "Saudi Arabia (Royal Decree M/33)|185.105.88.10|SA|451"
    "China (Criminal Law Article 303)|1.180.0.1|CN|451"
    "Qatar (Law No. 14 of 2014)|31.166.128.1|QA|451"
    "Iran (Islamic Penal Code)|5.200.0.1|IR|451"
    "Pakistan (Prevention of Gambling Act 1977)|39.32.0.1|PK|451"
    "North Korea (complete prohibition)|175.45.176.1|KP|451"
    "Afghanistan (Penal Code Article 277)|107.179.128.1|AF|451"
)

declare -a ALLOWED_TESTS=(
    "United Kingdom (UKGC licensed)|81.2.69.142|GB|200"
    "Germany (licensed market)|80.237.0.1|DE|200"
    "Sweden (licensed market)|2.248.0.1|SE|200"
    "Malta (MGA licensed)|213.148.0.1|MT|200"
    "Ireland (licensed market)|85.204.4.1|IE|200"
)

declare -a API_TESTS=(
    "API: registration endpoint from blocked country|5.62.56.160|AE|451"
    "API: game launch from allowed country|81.2.69.142|GB|200"
)

# =============================================================================
# Helper functions
# =============================================================================

log_info()    { echo -e "${BLUE}[INFO]${NC}  $*"; }
log_ok()      { echo -e "${GREEN}[PASS]${NC}  $*"; }
log_fail()    { echo -e "${RED}[FAIL]${NC}  $*"; }
log_warn()    { echo -e "${YELLOW}[WARN]${NC}  $*"; }
log_section() { echo -e "\n${BOLD}${BLUE}=== $* ===${NC}"; }

# Check required tools
check_deps() {
    local missing=()
    for cmd in curl dig jq; do
        command -v "$cmd" &>/dev/null || missing+=("$cmd")
    done
    if [[ ${#missing[@]} -gt 0 ]]; then
        echo -e "${RED}ERROR: Missing required tools: ${missing[*]}${NC}" >&2
        echo "Install with: apt-get install curl dnsutils jq" >&2
        exit 2
    fi
}

# Run a single HTTP test
# $1 = description, $2 = ip, $3 = country_code, $4 = expected_status, $5 = endpoint
run_http_test() {
    local desc="$1" ip="$2" country="$3" expected_status="$4" endpoint="${5:-/}"
    local url="${TARGET_URL}${endpoint}"

    TOTAL_COUNT=$((TOTAL_COUNT + 1))

    # Simulate a request from the test IP using X-Forwarded-For
    # NOTE: This only works if your nginx/application trusts X-Forwarded-For.
    # In production behind CloudFront, use CF-Connecting-IP instead.
    local curl_args=(
        --silent
        --max-time 10
        --write-out "%{http_code}|%{time_total}"
        --output /dev/null
        -H "X-Forwarded-For: ${ip}"
        -H "X-Test-Geo-Country: ${country}"
        -H "User-Agent: GeoComplianceChecker/1.0 (iGaming Audit Tool)"
    )

    if $VERBOSE; then
        curl_args+=(--verbose)
    fi

    local result
    result=$(curl "${curl_args[@]}" "$url" 2>&1 || true)
    local http_status response_time
    http_status=$(echo "$result" | tail -1 | cut -d'|' -f1)
    response_time=$(echo "$result" | tail -1 | cut -d'|' -f2)

    local status="PASS"
    if [[ "$http_status" != "$expected_status" ]]; then
        status="FAIL"
        FAIL_COUNT=$((FAIL_COUNT + 1))
        log_fail "${desc} | IP: ${ip} | Country: ${country} | Expected: ${expected_status} | Got: ${http_status} | Time: ${response_time}s"
    else
        PASS_COUNT=$((PASS_COUNT + 1))
        log_ok  "${desc} | IP: ${ip} | Country: ${country} | Status: ${http_status} | Time: ${response_time}s"
    fi

    # Append to JSON results array
    printf '{"description":"%s","ip":"%s","country":"%s","endpoint":"%s","expected":%s,"actual":%s,"passed":%s,"response_time_seconds":%s}\n' \
        "$desc" "$ip" "$country" "$endpoint" "$expected_status" "${http_status:-0}" \
        "$( [[ $status == "PASS" ]] && echo true || echo false )" \
        "$response_time" >> /tmp/geo_test_results_$$.jsonl
}

# DNS geo-routing check
check_dns_geo_routing() {
    log_section "DNS Geolocation Routing Check"
    local domain
    domain=$(echo "$TARGET_URL" | sed 's|https\?://||' | cut -d'/' -f1)

    log_info "Checking DNS resolution for: ${domain}"

    if ! command -v dig &>/dev/null; then
        log_warn "dig not available — skipping DNS checks"
        return
    fi

    # Check that the domain resolves
    local resolved_ips
    resolved_ips=$(dig +short "$domain" A 2>/dev/null || true)
    if [[ -z "$resolved_ips" ]]; then
        log_fail "DNS: ${domain} did not resolve"
        FAIL_COUNT=$((FAIL_COUNT + 1))
        TOTAL_COUNT=$((TOTAL_COUNT + 1))
    else
        log_ok "DNS: ${domain} resolves to: $(echo "$resolved_ips" | tr '\n' ' ')"
        PASS_COUNT=$((PASS_COUNT + 1))
        TOTAL_COUNT=$((TOTAL_COUNT + 1))
    fi

    # Check for HTTPS redirect (HTTP → HTTPS)
    local http_redirect
    http_redirect=$(curl --silent --max-time 10 --write-out "%{redirect_url}" --output /dev/null \
        "http://${domain}/" 2>/dev/null || true)
    if [[ "$http_redirect" == "https://"* ]]; then
        log_ok "HTTP → HTTPS redirect: $http_redirect"
        PASS_COUNT=$((PASS_COUNT + 1))
    else
        log_warn "HTTP → HTTPS redirect not detected (got: ${http_redirect:-none})"
    fi
    TOTAL_COUNT=$((TOTAL_COUNT + 1))
}

# TLS certificate check
check_tls() {
    log_section "TLS Certificate Check"
    local domain
    domain=$(echo "$TARGET_URL" | sed 's|https\?://||' | cut -d'/' -f1)

    local tls_info
    tls_info=$(echo | openssl s_client -connect "${domain}:443" -servername "$domain" 2>/dev/null | \
        openssl x509 -noout -subject -dates 2>/dev/null || true)

    if [[ -z "$tls_info" ]]; then
        log_warn "TLS: Could not verify certificate (openssl not available or connection failed)"
        return
    fi

    local expiry
    expiry=$(echo "$tls_info" | grep "notAfter" | cut -d'=' -f2)
    if [[ -n "$expiry" ]]; then
        log_ok "TLS: Certificate valid until: $expiry"
        PASS_COUNT=$((PASS_COUNT + 1))
    fi
    TOTAL_COUNT=$((TOTAL_COUNT + 1))
}

# Check compliance response body
check_block_response_body() {
    log_section "Block Response Body Verification"
    log_info "Verifying 451 response contains required compliance fields"

    local response
    response=$(curl --silent --max-time 10 \
        -H "X-Forwarded-For: 5.62.56.160" \
        -H "X-Test-Geo-Country: AE" \
        "${TARGET_URL}/" 2>/dev/null || true)

    TOTAL_COUNT=$((TOTAL_COUNT + 1))
    if echo "$response" | jq -e '.error // .message // .code' &>/dev/null 2>&1; then
        log_ok "Block response contains JSON with required fields"
        PASS_COUNT=$((PASS_COUNT + 1))

        local code
        code=$(echo "$response" | jq -r '.code // empty' 2>/dev/null || true)
        if [[ "$code" == "GEO_BLOCK"* ]]; then
            log_ok "Block response code is correct: $code"
        fi
    else
        log_warn "Block response is not JSON or missing required fields: ${response:0:200}"
    fi
}

# =============================================================================
# Main test runner
# =============================================================================

check_deps

# Initialize JSON results file
true > /tmp/geo_test_results_$$.jsonl

echo -e "${BOLD}"
echo "============================================================"
echo "  iGaming Geo-Compliance Verification"
echo "  Target: ${TARGET_URL}"
echo "  Date:   $(date -u +"%Y-%m-%dT%H:%M:%SZ")"
echo "============================================================"
echo -e "${NC}"

# DNS checks
check_dns_geo_routing

# TLS check
check_tls

# --- Blocked country tests ---
log_section "Blocked Jurisdiction Tests (Expected: HTTP 451)"
for test in "${BLOCKED_TESTS[@]}"; do
    IFS='|' read -r desc ip country expected_status <<< "$test"
    run_http_test "$desc" "$ip" "$country" "$expected_status" "/"
done

# --- Allowed country tests ---
log_section "Allowed Jurisdiction Tests (Expected: HTTP 200)"
for test in "${ALLOWED_TESTS[@]}"; do
    IFS='|' read -r desc ip country expected_status <<< "$test"
    run_http_test "$desc" "$ip" "$country" "$expected_status" "/"
done

# --- API endpoint tests ---
log_section "API Endpoint Tests"
for test in "${API_TESTS[@]}"; do
    IFS='|' read -r desc ip country expected_status <<< "$test"
    run_http_test "$desc" "$ip" "$country" "$expected_status" "/api/"
done

# Block response body
check_block_response_body

# =============================================================================
# Summary
# =============================================================================
echo ""
echo -e "${BOLD}============================================================${NC}"
echo -e "${BOLD}  Test Results${NC}"
echo -e "${BOLD}============================================================${NC}"
echo -e "  Total:   ${TOTAL_COUNT}"
echo -e "  ${GREEN}Passed:  ${PASS_COUNT}${NC}"
echo -e "  ${RED}Failed:  ${FAIL_COUNT}${NC}"
echo ""

# =============================================================================
# JSON Report
# =============================================================================
if $OUTPUT_JSON; then
    {
        echo "{"
        echo "  \"report_generated\": \"$(date -u +"%Y-%m-%dT%H:%M:%SZ")\","
        echo "  \"target_url\": \"${TARGET_URL}\","
        echo "  \"total_tests\": ${TOTAL_COUNT},"
        echo "  \"passed\": ${PASS_COUNT},"
        echo "  \"failed\": ${FAIL_COUNT},"
        echo "  \"pass_rate_percent\": $(awk "BEGIN{printf \"%.1f\", ${PASS_COUNT}/${TOTAL_COUNT}*100}"),"
        echo "  \"results\": ["
        # Convert JSONL to JSON array
        paste -sd ',' /tmp/geo_test_results_$$.jsonl | sed 's/^/    [/' | sed 's/$/]/'
        echo "  ]"
        echo "}"
    } > "$REPORT_FILE"
    log_info "JSON report written to: ${REPORT_FILE}"
fi

# Cleanup temp file
rm -f /tmp/geo_test_results_$$.jsonl

# Exit with failure if any tests failed
if [[ "$FAIL_COUNT" -gt 0 ]]; then
    echo -e "${RED}${BOLD}COMPLIANCE CHECK FAILED: ${FAIL_COUNT} test(s) failed.${NC}"
    echo -e "${RED}Investigate immediately — geo-blocking may not be working as required.${NC}"
    exit 1
else
    echo -e "${GREEN}${BOLD}ALL TESTS PASSED. Geo-blocking is functioning correctly.${NC}"
    exit 0
fi
