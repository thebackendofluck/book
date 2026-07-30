#!/usr/bin/env bash
# Companion code for "The Backend of Luck" - Chapter 25, GLI-GSF Compliance Framework.
# https://thebackendofluck.com | https://github.com/thebackendofluck/book
# SPDX-License-Identifier: Apache-2.0
#
# FOR TESTING AND EVALUATION ONLY. NOT FOR PRODUCTION USE.
# Published to demonstrate the patterns explained in the book. This code is
# not certified for real-money gaming: operating a gambling platform requires
# your own licence, independent test-lab certification (GLI, eCOGRA or
# equivalent) and regulator approval.

# shellcheck disable=SC2001,SC2015,SC2034,SC2129
# =============================================================================
# OWASP API Top 10 Scanner for iGaming Platforms
# GLI-GSF Phase 3 - API Security Validation
#
# Checks gambling platform APIs against OWASP API Security Top 10 (2023):
#   API1  - Broken Object Level Authorization (BOLA)
#   API2  - Broken Authentication
#   API3  - Broken Object Property Level Authorization
#   API4  - Unrestricted Resource Consumption
#   API5  - Broken Function Level Authorization
#   API6  - Unrestricted Access to Sensitive Business Flows
#   API7  - Server Side Request Forgery (SSRF)
#   API8  - Security Misconfiguration
#   API9  - Improper Inventory Management
#   API10 - Unsafe Consumption of APIs
#
# GLI-GSF-5 Reference: Section 4.2 - API Security Controls
#
# Usage:
#   ./owasp-api-check.sh -t https://api.casino.example.com -k <api_key>
#   ./owasp-api-check.sh -t https://api.casino.example.com -s openapi.yaml
#   ./owasp-api-check.sh -t https://api.casino.example.com --full-scan
#
# Requirements:
#   curl, jq, openssl, nmap (optional), nuclei (optional)
# =============================================================================

set -euo pipefail

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------
TARGET_URL=""
API_KEY=""
OPENAPI_SPEC=""
OUTPUT_DIR="./owasp-api-report-$(date +%Y%m%d-%H%M%S)"
FULL_SCAN=false
TIMEOUT=10
VERBOSE=false
MAX_CONCURRENT=5

# Colors
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m'

# Counters
PASS_COUNT=0
FAIL_COUNT=0
WARN_COUNT=0
INFO_COUNT=0

# ---------------------------------------------------------------------------
# Helper Functions
# ---------------------------------------------------------------------------
log_pass() { echo -e "${GREEN}[PASS]${NC} $1"; ((PASS_COUNT++)); }
log_fail() { echo -e "${RED}[FAIL]${NC} $1"; ((FAIL_COUNT++)); }
log_warn() { echo -e "${YELLOW}[WARN]${NC} $1"; ((WARN_COUNT++)); }
log_info() { echo -e "${BLUE}[INFO]${NC} $1"; ((INFO_COUNT++)); }

usage() {
    cat << 'EOF'
Usage: owasp-api-check.sh [OPTIONS]

Options:
  -t, --target URL         Target API base URL (required)
  -k, --api-key KEY        API key for authenticated tests
  -s, --spec FILE          OpenAPI/Swagger specification file
  -o, --output DIR         Output directory for reports
  -f, --full-scan          Enable all checks including active tests
  -v, --verbose            Verbose output
  -h, --help               Show this help

Examples:
  # Basic scan
  ./owasp-api-check.sh -t https://api.casino.example.com

  # Authenticated scan with OpenAPI spec
  ./owasp-api-check.sh -t https://api.casino.example.com -k "Bearer token123" -s openapi.yaml

  # Full scan with report output
  ./owasp-api-check.sh -t https://api.casino.example.com --full-scan -o ./report
EOF
    exit 0
}

parse_args() {
    while [[ $# -gt 0 ]]; do
        case $1 in
            -t|--target)   TARGET_URL="$2"; shift 2 ;;
            -k|--api-key)  API_KEY="$2"; shift 2 ;;
            -s|--spec)     OPENAPI_SPEC="$2"; shift 2 ;;
            -o|--output)   OUTPUT_DIR="$2"; shift 2 ;;
            -f|--full-scan) FULL_SCAN=true; shift ;;
            -v|--verbose)  VERBOSE=true; shift ;;
            -h|--help)     usage ;;
            *) echo "Unknown option: $1"; usage ;;
        esac
    done

    if [[ -z "$TARGET_URL" ]]; then
        echo "Error: Target URL is required (-t)"
        usage
    fi

    # Remove trailing slash
    TARGET_URL="${TARGET_URL%/}"
}

setup() {
    mkdir -p "$OUTPUT_DIR"
    echo "OWASP API Security Top 10 - iGaming Platform Scan" > "$OUTPUT_DIR/report.txt"
    echo "Target: $TARGET_URL" >> "$OUTPUT_DIR/report.txt"
    echo "Date: $(date -u)" >> "$OUTPUT_DIR/report.txt"
    echo "---" >> "$OUTPUT_DIR/report.txt"

    # Check required tools
    for tool in curl jq openssl; do
        if ! command -v "$tool" &>/dev/null; then
            echo "Error: $tool is required but not installed"
            exit 1
        fi
    done
}

api_request() {
    local method="$1"
    local path="$2"
    local data="${3:-}"
    local extra_headers="${4:-}"

    local headers=(-s -w "\n%{http_code}" --max-time "$TIMEOUT")

    if [[ -n "$API_KEY" ]]; then
        headers+=(-H "Authorization: $API_KEY")
    fi

    headers+=(-H "Content-Type: application/json")
    headers+=(-H "Accept: application/json")

    if [[ -n "$extra_headers" ]]; then
        headers+=(-H "$extra_headers")
    fi

    if [[ "$method" == "POST" || "$method" == "PUT" || "$method" == "PATCH" ]]; then
        if [[ -n "$data" ]]; then
            curl "${headers[@]}" -X "$method" -d "$data" "${TARGET_URL}${path}" 2>/dev/null
        else
            curl "${headers[@]}" -X "$method" "${TARGET_URL}${path}" 2>/dev/null
        fi
    else
        curl "${headers[@]}" -X "$method" "${TARGET_URL}${path}" 2>/dev/null
    fi
}

# ---------------------------------------------------------------------------
# API1: Broken Object Level Authorization (BOLA)
# ---------------------------------------------------------------------------
check_api1_bola() {
    echo ""
    echo "================================================================"
    echo "API1: Broken Object Level Authorization (BOLA)"
    echo "================================================================"

    # Test IDOR on common gambling endpoints
    local endpoints=(
        "/api/v1/users/1"
        "/api/v1/users/2"
        "/api/v1/bets/1"
        "/api/v1/transactions/1"
        "/api/v1/wallet/1"
        "/api/v1/kyc/documents/1"
        "/api/v1/withdrawals/1"
    )

    for endpoint in "${endpoints[@]}"; do
        local response
        response=$(api_request "GET" "$endpoint" 2>/dev/null || true)
        local status
        status=$(echo "$response" | tail -1)

        if [[ "$status" == "200" ]]; then
            log_warn "API1: $endpoint returned 200 - verify object-level authz"
            echo "  Endpoint $endpoint accessible - manual BOLA test needed" >> "$OUTPUT_DIR/report.txt"
        elif [[ "$status" == "401" || "$status" == "403" ]]; then
            log_pass "API1: $endpoint properly returns $status"
        elif [[ "$status" == "404" ]]; then
            log_info "API1: $endpoint not found (may be OK if path doesn't exist)"
        fi
    done

    # Test sequential ID enumeration
    log_info "API1: Testing sequential ID enumeration on /api/v1/users/{id}"
    local accessible=0
    for id in 1 2 3 100 999 1000; do
        local resp
        resp=$(api_request "GET" "/api/v1/users/$id" 2>/dev/null || true)
        local code
        code=$(echo "$resp" | tail -1)
        if [[ "$code" == "200" ]]; then
            ((accessible++))
        fi
    done

    if [[ $accessible -gt 2 ]]; then
        log_fail "API1: Multiple user records accessible via ID enumeration ($accessible/6)"
    else
        log_pass "API1: ID enumeration appears properly restricted"
    fi

    # GLI-GSF specific: Check bet history isolation
    log_info "API1: Check bet history isolation between players"
}

# ---------------------------------------------------------------------------
# API2: Broken Authentication
# ---------------------------------------------------------------------------
check_api2_auth() {
    echo ""
    echo "================================================================"
    echo "API2: Broken Authentication"
    echo "================================================================"

    # Test auth endpoint rate limiting
    log_info "API2: Testing auth endpoint rate limiting"
    local blocked=false
    for i in $(seq 1 20); do
        local resp
        resp=$(api_request "POST" "/api/v1/auth/login" \
            '{"email":"test@test.com","password":"wrong"}' 2>/dev/null || true)
        local code
        code=$(echo "$resp" | tail -1)

        if [[ "$code" == "429" ]]; then
            log_pass "API2: Rate limiting active after $i attempts"
            blocked=true
            break
        fi
    done

    if [[ "$blocked" == "false" ]]; then
        log_fail "API2: No rate limiting on auth endpoint after 20 attempts"
    fi

    # Check for JWT issues
    log_info "API2: Testing JWT security"
    local jwt_resp
    jwt_resp=$(api_request "POST" "/api/v1/auth/login" \
        '{"email":"test@test.com","password":"test"}' 2>/dev/null || true)

    # Check if token is in response
    local body
    body=$(echo "$jwt_resp" | sed '$d')
    if echo "$body" | jq -e '.token' &>/dev/null; then
        local token
        token=$(echo "$body" | jq -r '.token')

        # Check if JWT uses none algorithm
        local header
        header=$(echo "$token" | cut -d. -f1 | base64 -d 2>/dev/null || true)
        if echo "$header" | grep -qi '"alg":"none"'; then
            log_fail "API2: JWT uses 'none' algorithm - CRITICAL"
        fi

        # Check JWT expiration
        local payload
        payload=$(echo "$token" | cut -d. -f2 | base64 -d 2>/dev/null || true)
        if echo "$payload" | jq -e '.exp' &>/dev/null; then
            local exp
            exp=$(echo "$payload" | jq -r '.exp')
            local now
            now=$(date +%s)
            local ttl=$(( exp - now ))
            if [[ $ttl -gt 86400 ]]; then
                log_warn "API2: JWT TTL is ${ttl}s (>24h) - consider shorter expiry"
            else
                log_pass "API2: JWT TTL is ${ttl}s"
            fi
        fi
    fi

    # Check password reset flow
    local reset_resp
    reset_resp=$(api_request "POST" "/api/v1/auth/reset-password" \
        '{"email":"test@test.com"}' 2>/dev/null || true)
    local reset_code
    reset_code=$(echo "$reset_resp" | tail -1)

    if [[ "$reset_code" == "200" ]]; then
        local reset_body
        reset_body=$(echo "$reset_resp" | sed '$d')
        if echo "$reset_body" | grep -qi "token\|reset_link\|code"; then
            log_fail "API2: Password reset exposes token/link in response"
        else
            log_pass "API2: Password reset doesn't expose sensitive data"
        fi
    fi

    # Check for session management
    log_info "API2: GLI-GSF requires session timeout <= 30 min for gambling platforms"
}

# ---------------------------------------------------------------------------
# API3: Broken Object Property Level Authorization
# ---------------------------------------------------------------------------
check_api3_property() {
    echo ""
    echo "================================================================"
    echo "API3: Broken Object Property Level Authorization"
    echo "================================================================"

    # Test mass assignment on user profile
    log_info "API3: Testing mass assignment on user endpoints"
    local mass_assign_resp
    mass_assign_resp=$(api_request "PATCH" "/api/v1/users/me" \
        '{"role":"admin","balance":999999,"is_verified":true,"vip_level":10}' 2>/dev/null || true)
    local ma_code
    ma_code=$(echo "$mass_assign_resp" | tail -1)

    if [[ "$ma_code" == "200" ]]; then
        local ma_body
        ma_body=$(echo "$mass_assign_resp" | sed '$d')
        if echo "$ma_body" | jq -e '.role' 2>/dev/null | grep -qi "admin"; then
            log_fail "API3: Mass assignment - role escalation possible"
        elif echo "$ma_body" | jq -e '.balance' 2>/dev/null | grep -q "999999"; then
            log_fail "API3: Mass assignment - balance manipulation possible"
        else
            log_warn "API3: User update returned 200 - verify filtered fields"
        fi
    elif [[ "$ma_code" == "400" || "$ma_code" == "403" || "$ma_code" == "422" ]]; then
        log_pass "API3: Mass assignment attempt properly rejected ($ma_code)"
    fi

    # Check if API responses expose sensitive fields
    log_info "API3: Checking for sensitive field exposure in responses"
    local sensitive_fields=("password" "password_hash" "ssn" "credit_card" "cvv"
                           "secret_key" "api_secret" "internal_id")
    local user_resp
    user_resp=$(api_request "GET" "/api/v1/users/me" 2>/dev/null || true)
    local user_body
    user_body=$(echo "$user_resp" | sed '$d')

    for field in "${sensitive_fields[@]}"; do
        if echo "$user_body" | jq -e ".$field" &>/dev/null; then
            log_fail "API3: Response exposes sensitive field: $field"
        fi
    done
}

# ---------------------------------------------------------------------------
# API4: Unrestricted Resource Consumption
# ---------------------------------------------------------------------------
check_api4_resources() {
    echo ""
    echo "================================================================"
    echo "API4: Unrestricted Resource Consumption"
    echo "================================================================"

    # Test pagination limits
    log_info "API4: Testing pagination limits"
    local page_resp
    page_resp=$(api_request "GET" "/api/v1/bets?limit=10000&offset=0" 2>/dev/null || true)
    local page_code
    page_code=$(echo "$page_resp" | tail -1)

    if [[ "$page_code" == "200" ]]; then
        local page_body
        page_body=$(echo "$page_resp" | sed '$d')
        local count
        count=$(echo "$page_body" | jq -r '.data | length' 2>/dev/null || echo "0")
        if [[ "$count" -gt 100 ]]; then
            log_fail "API4: Excessive pagination allowed (limit=10000 returned $count records)"
        else
            log_pass "API4: Pagination properly capped (requested 10000, got $count)"
        fi
    fi

    # Test request size limits
    log_info "API4: Testing request body size limits"
    local large_body
    large_body=$(python3 -c "print('{\"data\":\"' + 'A'*1048576 + '\"}')" 2>/dev/null || true)
    if [[ -n "$large_body" ]]; then
        local size_resp
        size_resp=$(api_request "POST" "/api/v1/bets" "$large_body" 2>/dev/null || true)
        local size_code
        size_code=$(echo "$size_resp" | tail -1)
        if [[ "$size_code" == "413" ]]; then
            log_pass "API4: Request body size limit enforced (413)"
        elif [[ "$size_code" == "200" || "$size_code" == "201" ]]; then
            log_fail "API4: 1MB request body accepted without size limit"
        fi
    fi

    # Test GraphQL depth (if applicable)
    local gql_resp
    gql_resp=$(api_request "POST" "/graphql" \
        '{"query":"{ user { bets { game { provider { games { bets { user { name } } } } } } } }"}' \
        2>/dev/null || true)
    local gql_code
    gql_code=$(echo "$gql_resp" | tail -1)
    if [[ "$gql_code" == "200" ]]; then
        log_warn "API4: Deep GraphQL query accepted - check query depth limits"
    fi

    # Check Content-Length header handling
    log_info "API4: GLI-GSF requires resource limits on all financial transaction endpoints"
}

# ---------------------------------------------------------------------------
# API5: Broken Function Level Authorization
# ---------------------------------------------------------------------------
check_api5_function_authz() {
    echo ""
    echo "================================================================"
    echo "API5: Broken Function Level Authorization"
    echo "================================================================"

    # Test admin endpoints without admin credentials
    local admin_endpoints=(
        "/api/v1/admin/users"
        "/api/v1/admin/transactions"
        "/api/v1/admin/reports"
        "/api/v1/admin/config"
        "/api/v1/internal/rng/seed"
        "/api/v1/internal/game-config"
        "/api/v1/backoffice/players"
        "/api/v1/management/audit-log"
    )

    for endpoint in "${admin_endpoints[@]}"; do
        local resp
        resp=$(api_request "GET" "$endpoint" 2>/dev/null || true)
        local code
        code=$(echo "$resp" | tail -1)

        if [[ "$code" == "200" ]]; then
            log_fail "API5: Admin endpoint accessible: $endpoint"
        elif [[ "$code" == "401" || "$code" == "403" ]]; then
            log_pass "API5: Admin endpoint protected: $endpoint ($code)"
        elif [[ "$code" == "404" ]]; then
            log_info "API5: Endpoint not found: $endpoint"
        fi
    done

    # Test HTTP method tampering
    log_info "API5: Testing HTTP method tampering"
    local methods=("PUT" "DELETE" "PATCH" "OPTIONS")
    for method in "${methods[@]}"; do
        local resp
        resp=$(api_request "$method" "/api/v1/users/1" 2>/dev/null || true)
        local code
        code=$(echo "$resp" | tail -1)
        if [[ "$code" == "200" || "$code" == "204" ]]; then
            log_warn "API5: $method /api/v1/users/1 returned $code - verify authorization"
        fi
    done
}

# ---------------------------------------------------------------------------
# API6: Unrestricted Access to Sensitive Business Flows
# ---------------------------------------------------------------------------
check_api6_business_flows() {
    echo ""
    echo "================================================================"
    echo "API6: Unrestricted Access to Sensitive Business Flows"
    echo "================================================================"

    log_info "API6: Checking gambling-specific business flow protections"

    # Bonus abuse - rapid bonus claims
    log_info "API6: Testing bonus claim rate limiting"
    local bonus_blocked=false
    for i in $(seq 1 10); do
        local resp
        resp=$(api_request "POST" "/api/v1/bonus/claim" \
            '{"bonus_code":"WELCOME100"}' 2>/dev/null || true)
        local code
        code=$(echo "$resp" | tail -1)
        if [[ "$code" == "429" || "$code" == "409" ]]; then
            log_pass "API6: Bonus claim rate limited after $i attempts"
            bonus_blocked=true
            break
        fi
    done
    if [[ "$bonus_blocked" == "false" ]]; then
        log_warn "API6: No rate limiting on bonus claims after 10 attempts"
    fi

    # Multi-accounting check
    log_info "API6: Testing registration for multi-accounting controls"
    local reg_resp
    reg_resp=$(api_request "POST" "/api/v1/auth/register" \
        '{"email":"test-dup@test.com","password":"Test12345!","name":"Test User"}' 2>/dev/null || true)

    # Automated withdrawal
    log_info "API6: Testing withdrawal flow protections"
    local wd_resp
    wd_resp=$(api_request "POST" "/api/v1/withdrawals" \
        '{"amount":50000,"method":"bank_transfer"}' 2>/dev/null || true)
    local wd_code
    wd_code=$(echo "$wd_resp" | tail -1)
    if [[ "$wd_code" == "200" || "$wd_code" == "201" ]]; then
        log_warn "API6: Large withdrawal accepted without additional verification"
    fi

    # Self-exclusion bypass
    log_info "API6: GLI-GSF requires self-exclusion cannot be bypassed via API"
}

# ---------------------------------------------------------------------------
# API7: Server Side Request Forgery (SSRF)
# ---------------------------------------------------------------------------
check_api7_ssrf() {
    echo ""
    echo "================================================================"
    echo "API7: Server Side Request Forgery (SSRF)"
    echo "================================================================"

    local ssrf_payloads=(
        "http://169.254.169.254/latest/meta-data/"
        "http://localhost:6379/"
        "http://127.0.0.1:5432/"
        "http://[::1]/"
        "http://0.0.0.0/"
        "file:///etc/passwd"
        "http://metadata.google.internal/"
    )

    # Test URL parameters that might be vulnerable
    local url_params=(
        "/api/v1/games/thumbnail?url="
        "/api/v1/kyc/document-verify?document_url="
        "/api/v1/webhooks/test?callback_url="
        "/api/v1/payment/callback?return_url="
    )

    for param in "${url_params[@]}"; do
        for payload in "${ssrf_payloads[@]}"; do
            local resp
            resp=$(api_request "GET" "${param}${payload}" 2>/dev/null || true)
            local code
            code=$(echo "$resp" | tail -1)
            local body
            body=$(echo "$resp" | sed '$d')

            if [[ "$code" == "200" ]]; then
                if echo "$body" | grep -qiE "ami-id|instance-id|root:|passwd|PONG|PostgreSQL"; then
                    log_fail "API7: SSRF confirmed on ${param} with payload ${payload}"
                else
                    log_warn "API7: ${param} returned 200 for internal URL - verify"
                fi
            fi
        done
    done

    log_pass "API7: Basic SSRF payloads tested"
}

# ---------------------------------------------------------------------------
# API8: Security Misconfiguration
# ---------------------------------------------------------------------------
check_api8_misconfig() {
    echo ""
    echo "================================================================"
    echo "API8: Security Misconfiguration"
    echo "================================================================"

    # Check security headers
    local headers_resp
    headers_resp=$(curl -sI --max-time "$TIMEOUT" "$TARGET_URL/" 2>/dev/null || true)

    local required_headers=(
        "Strict-Transport-Security"
        "X-Content-Type-Options"
        "X-Frame-Options"
        "Content-Security-Policy"
    )

    for header in "${required_headers[@]}"; do
        if echo "$headers_resp" | grep -qi "$header"; then
            log_pass "API8: Header present: $header"
        else
            log_fail "API8: Missing security header: $header"
        fi
    done

    # Check for information disclosure
    if echo "$headers_resp" | grep -qi "Server:"; then
        local server
        server=$(echo "$headers_resp" | grep -i "Server:" | head -1)
        log_warn "API8: Server header exposed: $server"
    fi

    if echo "$headers_resp" | grep -qi "X-Powered-By:"; then
        local powered
        powered=$(echo "$headers_resp" | grep -i "X-Powered-By:" | head -1)
        log_fail "API8: X-Powered-By exposed: $powered"
    fi

    # Check CORS
    local cors_resp
    cors_resp=$(curl -sI --max-time "$TIMEOUT" \
        -H "Origin: https://evil.example.com" \
        "$TARGET_URL/api/v1/" 2>/dev/null || true)

    if echo "$cors_resp" | grep -qi "Access-Control-Allow-Origin: \*"; then
        log_fail "API8: CORS allows all origins (wildcard)"
    elif echo "$cors_resp" | grep -qi "Access-Control-Allow-Origin: https://evil"; then
        log_fail "API8: CORS reflects arbitrary origin"
    else
        log_pass "API8: CORS properly configured"
    fi

    # Check for debug/dev endpoints
    local debug_endpoints=(
        "/debug" "/actuator" "/health" "/metrics"
        "/swagger-ui.html" "/api-docs" "/graphql/playground"
        "/phpinfo.php" "/.env" "/config.json"
        "/api/v1/debug" "/internal/status"
    )

    for endpoint in "${debug_endpoints[@]}"; do
        local resp
        resp=$(api_request "GET" "$endpoint" 2>/dev/null || true)
        local code
        code=$(echo "$resp" | tail -1)
        if [[ "$code" == "200" ]]; then
            log_warn "API8: Debug/dev endpoint accessible: $endpoint"
        fi
    done

    # Check TLS configuration
    log_info "API8: Checking TLS configuration"
    local domain
    domain=$(echo "$TARGET_URL" | sed 's|https\?://||' | cut -d/ -f1 | cut -d: -f1)

    local tls_info
    tls_info=$(echo | openssl s_client -connect "$domain:443" -servername "$domain" 2>/dev/null || true)

    if echo "$tls_info" | grep -q "TLSv1.3"; then
        log_pass "API8: TLS 1.3 supported"
    elif echo "$tls_info" | grep -q "TLSv1.2"; then
        log_pass "API8: TLS 1.2 supported"
    else
        log_fail "API8: Outdated TLS version"
    fi

    # Check for HTTP (non-TLS) access
    local http_url
    http_url=$(echo "$TARGET_URL" | sed 's|https://|http://|')
    local http_resp
    http_resp=$(curl -sI --max-time "$TIMEOUT" -o /dev/null -w "%{http_code}" "$http_url" 2>/dev/null || true)
    if [[ "$http_resp" == "301" || "$http_resp" == "308" ]]; then
        log_pass "API8: HTTP redirects to HTTPS ($http_resp)"
    elif [[ "$http_resp" == "200" ]]; then
        log_fail "API8: HTTP (non-TLS) accessible without redirect"
    fi
}

# ---------------------------------------------------------------------------
# API9: Improper Inventory Management
# ---------------------------------------------------------------------------
check_api9_inventory() {
    echo ""
    echo "================================================================"
    echo "API9: Improper Inventory Management"
    echo "================================================================"

    # Check for old API versions
    local versions=("v1" "v2" "v3" "v0" "beta" "staging" "dev" "test")
    for ver in "${versions[@]}"; do
        local resp
        resp=$(api_request "GET" "/api/$ver/" 2>/dev/null || true)
        local code
        code=$(echo "$resp" | tail -1)
        if [[ "$code" == "200" || "$code" == "301" ]]; then
            log_warn "API9: API version '$ver' accessible"
        fi
    done

    # Check for undocumented endpoints
    local common_endpoints=(
        "/api/v1/swagger.json"
        "/api/v1/openapi.json"
        "/api/v1/schema"
        "/api/v1/docs"
        "/api/v1/export"
        "/api/v1/import"
        "/api/v1/backup"
        "/api/v1/migrate"
    )

    for endpoint in "${common_endpoints[@]}"; do
        local resp
        resp=$(api_request "GET" "$endpoint" 2>/dev/null || true)
        local code
        code=$(echo "$resp" | tail -1)
        if [[ "$code" == "200" ]]; then
            log_warn "API9: Undocumented endpoint found: $endpoint"
        fi
    done

    # Check for exposed documentation
    if [[ -n "$OPENAPI_SPEC" ]]; then
        log_info "API9: OpenAPI spec provided - checking for deprecated endpoints"
        if command -v jq &>/dev/null && [[ -f "$OPENAPI_SPEC" ]]; then
            local deprecated
            deprecated=$(jq -r '.. | .deprecated? // empty' "$OPENAPI_SPEC" 2>/dev/null | grep -c "true" || echo "0")
            if [[ "$deprecated" -gt 0 ]]; then
                log_warn "API9: $deprecated deprecated endpoints found in OpenAPI spec"
            fi
        fi
    fi
}

# ---------------------------------------------------------------------------
# API10: Unsafe Consumption of APIs
# ---------------------------------------------------------------------------
check_api10_unsafe_consumption() {
    echo ""
    echo "================================================================"
    echo "API10: Unsafe Consumption of APIs"
    echo "================================================================"

    log_info "API10: Testing webhook/callback URL validation"

    # Test callback URL injection
    local callback_resp
    callback_resp=$(api_request "POST" "/api/v1/webhooks" \
        '{"url":"http://internal-service:8080/admin","events":["bet.placed"]}' 2>/dev/null || true)
    local cb_code
    cb_code=$(echo "$callback_resp" | tail -1)

    if [[ "$cb_code" == "200" || "$cb_code" == "201" ]]; then
        log_warn "API10: Webhook with internal URL accepted - verify URL validation"
    elif [[ "$cb_code" == "400" || "$cb_code" == "422" ]]; then
        log_pass "API10: Internal webhook URL properly rejected"
    fi

    # Test payment callback manipulation
    log_info "API10: Testing payment gateway callback security"
    local pay_resp
    pay_resp=$(api_request "POST" "/api/v1/payments/callback" \
        '{"status":"completed","amount":99999,"transaction_id":"fake-txn-001"}' 2>/dev/null || true)
    local pay_code
    pay_code=$(echo "$pay_resp" | tail -1)

    if [[ "$pay_code" == "200" ]]; then
        log_fail "API10: Payment callback accepted without signature verification"
    elif [[ "$pay_code" == "401" || "$pay_code" == "403" ]]; then
        log_pass "API10: Payment callback requires signature verification"
    fi

    log_info "API10: GLI-GSF requires all third-party API integrations to use mTLS or signed webhooks"
}

# ---------------------------------------------------------------------------
# Gambling-Specific Checks
# ---------------------------------------------------------------------------
check_gambling_specific() {
    echo ""
    echo "================================================================"
    echo "Gambling-Specific API Security (GLI-GSF)"
    echo "================================================================"

    # RNG endpoint protection
    log_info "GLI: Checking RNG endpoint access control"
    local rng_endpoints=("/api/v1/rng" "/api/v1/rng/seed" "/api/v1/rng/state" "/api/v1/random")
    for endpoint in "${rng_endpoints[@]}"; do
        local resp
        resp=$(api_request "GET" "$endpoint" 2>/dev/null || true)
        local code
        code=$(echo "$resp" | tail -1)
        if [[ "$code" == "200" ]]; then
            log_fail "GLI: RNG endpoint exposed: $endpoint"
        fi
    done

    # Game configuration tampering
    log_info "GLI: Checking game config endpoint protection"
    local game_resp
    game_resp=$(api_request "PUT" "/api/v1/games/config" \
        '{"rtp":99.9,"house_edge":0.001}' 2>/dev/null || true)
    local game_code
    game_code=$(echo "$game_resp" | tail -1)
    if [[ "$game_code" == "200" ]]; then
        log_fail "GLI: Game configuration modifiable via API"
    fi

    # Responsible gaming controls
    log_info "GLI: Checking responsible gaming API protections"
    local rg_resp
    rg_resp=$(api_request "DELETE" "/api/v1/responsible-gaming/self-exclusion" 2>/dev/null || true)
    local rg_code
    rg_code=$(echo "$rg_resp" | tail -1)
    if [[ "$rg_code" == "200" || "$rg_code" == "204" ]]; then
        log_fail "GLI: Self-exclusion can be removed via API (GLI-GSF violation)"
    fi

    # Audit log tampering
    log_info "GLI: Checking audit log integrity"
    local audit_resp
    audit_resp=$(api_request "DELETE" "/api/v1/audit-logs" 2>/dev/null || true)
    local audit_code
    audit_code=$(echo "$audit_resp" | tail -1)
    if [[ "$audit_code" == "200" || "$audit_code" == "204" ]]; then
        log_fail "GLI: Audit logs can be deleted via API (GLI-GSF-1 Section 5.2 violation)"
    fi
}

# ---------------------------------------------------------------------------
# Report Generation
# ---------------------------------------------------------------------------
generate_report() {
    echo ""
    echo "================================================================"
    echo "SCAN SUMMARY"
    echo "================================================================"
    echo -e "  ${GREEN}PASS:${NC}     $PASS_COUNT"
    echo -e "  ${RED}FAIL:${NC}     $FAIL_COUNT"
    echo -e "  ${YELLOW}WARN:${NC}     $WARN_COUNT"
    echo -e "  ${BLUE}INFO:${NC}     $INFO_COUNT"
    echo ""

    local total=$((PASS_COUNT + FAIL_COUNT + WARN_COUNT))
    if [[ $total -gt 0 ]]; then
        local pass_rate=$(( (PASS_COUNT * 100) / total ))
        echo "  Pass Rate: ${pass_rate}%"
    fi

    if [[ $FAIL_COUNT -gt 0 ]]; then
        echo -e "  Overall:   ${RED}FAIL${NC} - $FAIL_COUNT critical issues found"
    elif [[ $WARN_COUNT -gt 0 ]]; then
        echo -e "  Overall:   ${YELLOW}CONDITIONAL${NC} - $WARN_COUNT warnings need review"
    else
        echo -e "  Overall:   ${GREEN}PASS${NC}"
    fi

    echo ""
    echo "Report saved to: $OUTPUT_DIR/report.txt"
    echo "================================================================"

    # Save JSON summary
    cat > "$OUTPUT_DIR/summary.json" << ENDJSON
{
  "target": "$TARGET_URL",
  "scan_date": "$(date -u +%Y-%m-%dT%H:%M:%SZ)",
  "results": {
    "pass": $PASS_COUNT,
    "fail": $FAIL_COUNT,
    "warn": $WARN_COUNT,
    "info": $INFO_COUNT
  },
  "overall": "$([ $FAIL_COUNT -gt 0 ] && echo 'FAIL' || ([ $WARN_COUNT -gt 0 ] && echo 'CONDITIONAL' || echo 'PASS'))",
  "gli_gsf_reference": "GLI-GSF-5 Section 4.2 - API Security Controls"
}
ENDJSON
}

# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------
main() {
    parse_args "$@"
    setup

    echo "================================================================"
    echo "OWASP API Security Top 10 Scanner - iGaming Edition"
    echo "Target: $TARGET_URL"
    echo "Date:   $(date -u)"
    echo "================================================================"

    check_api1_bola
    check_api2_auth
    check_api3_property
    check_api4_resources
    check_api5_function_authz
    check_api6_business_flows
    check_api7_ssrf
    check_api8_misconfig
    check_api9_inventory
    check_api10_unsafe_consumption
    check_gambling_specific

    generate_report
}

main "$@"
