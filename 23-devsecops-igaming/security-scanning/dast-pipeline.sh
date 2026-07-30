#!/usr/bin/env bash
# Companion code for "The Backend of Luck" - Chapter 23, DevSecOps for iGaming.
# https://thebackendofluck.com | https://github.com/thebackendofluck/book
# SPDX-License-Identifier: Apache-2.0
#
# FOR TESTING AND EVALUATION ONLY. NOT FOR PRODUCTION USE.
# Published to demonstrate the patterns explained in the book. This code is
# not certified for real-money gaming: operating a gambling platform requires
# your own licence, independent test-lab certification (GLI, eCOGRA or
# equivalent) and regulator approval.

# =============================================================================
# Dynamic Application Security Testing (DAST) Pipeline for iGaming Platform
# =============================================================================
# Chapter 23: DevSecOps - Security Scanning
#
# WHY: SAST finds code-level vulnerabilities, but DAST tests the running
# application. For iGaming platforms, DAST is critical because:
#   - SQL injection in player search can drain databases
#   - XSS in game lobbies can steal session tokens
#   - Authentication bypass can access player accounts and balances
#   - Missing rate limiting enables credential stuffing attacks
#   - IDOR on wallet endpoints allows balance theft
#
# This script uses OWASP ZAP as the primary DAST engine with custom
# scan policies tuned for iGaming platform attack surfaces.
#
# USAGE:
#   ./dast-pipeline.sh --target https://staging.example.com
#   ./dast-pipeline.sh --target https://staging.example.com --full
#   ./dast-pipeline.sh --target https://staging.example.com --api-spec openapi.yaml
#   ./dast-pipeline.sh --help
#
# PREREQUISITES:
#   - Docker (for OWASP ZAP container)
#   - Target application must be running and accessible
#   - API specification (OpenAPI/Swagger) for comprehensive API testing
#
# EXIT CODES:
#   0 - No high/critical findings
#   1 - High/critical findings detected
#   2 - Configuration error or tool failure
#
# =============================================================================
set -euo pipefail

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------
REPO_ROOT="$(git rev-parse --show-toplevel 2>/dev/null || pwd)"
REPORT_DIR="${REPO_ROOT}/.security-reports/dast"
TIMESTAMP="$(date -u +%Y%m%d_%H%M%S)"

# OWASP ZAP settings
ZAP_IMAGE="ghcr.io/zaproxy/zaproxy:stable"
ZAP_API_KEY="${ZAP_API_KEY:-$(openssl rand -hex 16 2>/dev/null || echo 'change-me-in-ci')}"
ZAP_PORT="${ZAP_PORT:-8090}"

# Target settings
TARGET_URL=""
API_SPEC=""
AUTH_TOKEN=""
AUTH_HEADER="Authorization"

# Colors
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
BOLD='\033[1m'
NC='\033[0m'

# ---------------------------------------------------------------------------
# Helper Functions
# ---------------------------------------------------------------------------
log_info() { echo -e "${BLUE}[INFO]${NC} $1"; }
log_success() { echo -e "${GREEN}[PASS]${NC} $1"; }
log_warn() { echo -e "${YELLOW}[WARN]${NC} $1"; }
log_error() { echo -e "${RED}[FAIL]${NC} $1" >&2; }

show_help() {
    echo "Usage: $(basename "$0") [OPTIONS]"
    echo ""
    echo "Dynamic Application Security Testing for iGaming platforms using OWASP ZAP."
    echo ""
    echo "Required:"
    echo "  --target URL          Target application URL (e.g., https://staging.casino.com)"
    echo ""
    echo "Options:"
    echo "  --baseline            Quick baseline scan (passive only, ~5 min)"
    echo "  --full                Full active scan (all attack vectors, ~30-60 min)"
    echo "  --api-spec PATH       OpenAPI/Swagger spec for API scanning"
    echo "  --auth-token TOKEN    Bearer token for authenticated scanning"
    echo "  --auth-header NAME    Auth header name (default: Authorization)"
    echo "  --ci                  CI mode: strict exit codes, no interactive"
    echo "  --output DIR          Custom report output directory"
    echo "  --help                Show this help message"
    echo ""
    echo "Environment Variables:"
    echo "  ZAP_API_KEY           ZAP API key (auto-generated if not set)"
    echo "  ZAP_PORT              ZAP proxy port (default: 8090)"
    echo "  TARGET_URL            Target URL (alternative to --target)"
    echo ""
    echo "Scan Modes:"
    echo "  baseline  - Passive scan: spiders the site, checks responses for issues"
    echo "              Good for: quick CI checks, regression testing"
    echo "  full      - Active scan: sends attack payloads to every endpoint"
    echo "              Good for: pre-release security testing, compliance audits"
    echo ""
    echo "Examples:"
    echo "  $(basename "$0") --target https://staging.casino.com --baseline"
    echo "  $(basename "$0") --target https://staging.casino.com --full --api-spec api/openapi.yaml"
    echo "  $(basename "$0") --target https://staging.casino.com --auth-token 'Bearer eyJ...'"
    echo ""
}

ensure_report_dir() {
    mkdir -p "${REPORT_DIR}"
}

check_docker() {
    if ! command -v docker &>/dev/null; then
        log_error "Docker is required for OWASP ZAP. Install Docker first."
        exit 2
    fi

    if ! docker info &>/dev/null 2>&1; then
        log_error "Docker daemon is not running. Start Docker first."
        exit 2
    fi
}

check_target_reachable() {
    local target="$1"
    log_info "Checking target is reachable: ${target}"

    local http_code
    http_code=$(curl -s -o /dev/null -w "%{http_code}" --max-time 10 "${target}" 2>/dev/null || echo "000")

    if [[ "${http_code}" == "000" ]]; then
        log_error "Target ${target} is not reachable. Ensure the application is running."
        exit 2
    fi

    log_success "Target reachable (HTTP ${http_code})"
}

# ---------------------------------------------------------------------------
# ZAP Baseline Scan
# WHY: The baseline scan is a passive-only spider that checks the target
# for common security misconfigurations without sending attack payloads.
# Safe to run against production. Catches:
#   - Missing security headers (CSP, HSTS, X-Frame-Options)
#   - Cookie security flags (Secure, HttpOnly, SameSite)
#   - Information disclosure (server version headers, error messages)
#   - Mixed content issues
# ---------------------------------------------------------------------------
run_baseline_scan() {
    local target="$1"
    local report_html="${REPORT_DIR}/zap-baseline-${TIMESTAMP}.html"
    local report_json="${REPORT_DIR}/zap-baseline-${TIMESTAMP}.json"

    log_info "Running OWASP ZAP baseline scan against ${target}..."
    log_info "This is a passive scan (no attack payloads sent)."

    local zap_args=(
        "zap-baseline.py"
        "-t" "${target}"
        "-J" "zap-baseline-${TIMESTAMP}.json"
        "-r" "zap-baseline-${TIMESTAMP}.html"
        "-l" "WARN"
    )

    # Add auth token if provided
    if [[ -n "${AUTH_TOKEN}" ]]; then
        zap_args+=("-z" "-config replacer.full_list(0).description=auth -config replacer.full_list(0).enabled=true -config replacer.full_list(0).matchtype=REQ_HEADER -config replacer.full_list(0).matchstr=${AUTH_HEADER} -config replacer.full_list(0).replacement=${AUTH_TOKEN}")
    fi

    local zap_exit=0
    docker run --rm \
        -v "${REPORT_DIR}:/zap/wrk:rw" \
        --network host \
        "${ZAP_IMAGE}" \
        "${zap_args[@]}" 2>&1 || zap_exit=$?

    # ZAP exit codes: 0=pass, 1=warn, 2=fail, 3=error
    case ${zap_exit} in
        0)
            log_success "ZAP baseline: No issues found"
            ;;
        1)
            log_warn "ZAP baseline: Warnings found (review report)"
            ;;
        2)
            log_error "ZAP baseline: Failures found (high/critical issues)"
            ;;
        *)
            log_error "ZAP baseline: Scanner error (exit code: ${zap_exit})"
            ;;
    esac

    log_info "HTML report: ${report_html}"
    log_info "JSON report: ${report_json}"
    return ${zap_exit}
}

# ---------------------------------------------------------------------------
# ZAP Full Active Scan
# WHY: Active scanning sends actual attack payloads to find exploitable
# vulnerabilities. For iGaming this includes:
#   - SQL injection on player search, transaction queries, game history
#   - XSS on game lobby, chat, player profiles
#   - Authentication bypass on login, session management
#   - IDOR on wallet/balance endpoints, player profile access
#   - Command injection on admin panel tools
# NEVER run active scans against production.
# ---------------------------------------------------------------------------
run_full_scan() {
    local target="$1"
    local report_html="${REPORT_DIR}/zap-full-${TIMESTAMP}.html"
    local report_json="${REPORT_DIR}/zap-full-${TIMESTAMP}.json"

    log_info "Running OWASP ZAP full active scan against ${target}..."
    log_warn "Active scan sends attack payloads. DO NOT run against production."

    local zap_args=(
        "zap-full-scan.py"
        "-t" "${target}"
        "-J" "zap-full-${TIMESTAMP}.json"
        "-r" "zap-full-${TIMESTAMP}.html"
        "-l" "WARN"
    )

    if [[ -n "${AUTH_TOKEN}" ]]; then
        zap_args+=("-z" "-config replacer.full_list(0).description=auth -config replacer.full_list(0).enabled=true -config replacer.full_list(0).matchtype=REQ_HEADER -config replacer.full_list(0).matchstr=${AUTH_HEADER} -config replacer.full_list(0).replacement=${AUTH_TOKEN}")
    fi

    local zap_exit=0
    docker run --rm \
        -v "${REPORT_DIR}:/zap/wrk:rw" \
        --network host \
        "${ZAP_IMAGE}" \
        "${zap_args[@]}" 2>&1 || zap_exit=$?

    case ${zap_exit} in
        0)  log_success "ZAP full scan: No issues found" ;;
        1)  log_warn "ZAP full scan: Warnings found" ;;
        2)  log_error "ZAP full scan: Failures found" ;;
        *)  log_error "ZAP full scan: Error (exit: ${zap_exit})" ;;
    esac

    log_info "HTML report: ${report_html}"
    log_info "JSON report: ${report_json}"
    return ${zap_exit}
}

# ---------------------------------------------------------------------------
# API Security Testing
# WHY: iGaming platforms expose REST APIs for game integration (GAL),
# player management (PAM), and wallet operations. API testing with an
# OpenAPI spec ensures every endpoint is tested, including those not
# linked from the UI (admin endpoints, internal service APIs).
# ---------------------------------------------------------------------------
run_api_scan() {
    local target="$1"
    local spec="$2"
    local report_json="${REPORT_DIR}/zap-api-${TIMESTAMP}.json"
    local report_html="${REPORT_DIR}/zap-api-${TIMESTAMP}.html"

    log_info "Running ZAP API scan with spec: ${spec}"

    local spec_mount=""
    local spec_path=""

    if [[ -f "${spec}" ]]; then
        spec_mount="-v $(cd "$(dirname "${spec}")" && pwd)/$(basename "${spec}"):/zap/wrk/openapi-spec.yaml:ro"
        spec_path="/zap/wrk/openapi-spec.yaml"
    else
        # Assume it's a URL
        spec_path="${spec}"
    fi

    local zap_exit=0
    # shellcheck disable=SC2086
    docker run --rm \
        -v "${REPORT_DIR}:/zap/wrk:rw" \
        ${spec_mount} \
        --network host \
        "${ZAP_IMAGE}" \
        zap-api-scan.py \
        -t "${target}" \
        -f openapi \
        -J "zap-api-${TIMESTAMP}.json" \
        -r "zap-api-${TIMESTAMP}.html" \
        -l WARN \
        ${spec_path:+-O "${spec_path}"} \
        2>&1 || zap_exit=$?

    case ${zap_exit} in
        0)  log_success "ZAP API scan: No issues found" ;;
        1)  log_warn "ZAP API scan: Warnings found" ;;
        2)  log_error "ZAP API scan: Failures found" ;;
        *)  log_error "ZAP API scan: Error (exit: ${zap_exit})" ;;
    esac

    log_info "API report: ${report_html}"
    return ${zap_exit}
}

# ---------------------------------------------------------------------------
# Casino-Specific Endpoint Tests
# WHY: Generic DAST tools miss domain-specific vulnerabilities. These tests
# target the unique attack surface of iGaming platforms:
#   - Payment endpoints: fund manipulation, withdrawal bypass
#   - Game endpoints: RNG prediction, outcome manipulation
#   - Admin panel: privilege escalation, player impersonation
# ---------------------------------------------------------------------------
run_casino_specific_tests() {
    local target="$1"

    log_info "Running iGaming-specific security tests..."

    local findings=0
    local test_report="${REPORT_DIR}/casino-tests-${TIMESTAMP}.json"

    echo '{"tests": [' > "${test_report}"
    local first_test=true

    # Helper to add test result
    add_result() {
        local name="$1" status="$2" detail="$3"
        if [[ "${first_test}" == "true" ]]; then
            first_test=false
        else
            echo "," >> "${test_report}"
        fi
        printf '  {"test": "%s", "status": "%s", "detail": "%s"}' \
            "${name}" "${status}" "${detail}" >> "${test_report}"
    }

    # Test 1: Authentication bypass
    # WHY: Unauthenticated access to player accounts is a critical finding
    # that would result in immediate license suspension.
    log_info "  Testing: Authentication bypass on protected endpoints..."
    local auth_endpoints=(
        "/api/v1/player/profile"
        "/api/v1/wallet/balance"
        "/api/v1/wallet/withdraw"
        "/api/v1/admin/players"
        "/api/v1/games/rng/seed"
    )
    for endpoint in "${auth_endpoints[@]}"; do
        local http_code
        http_code=$(curl -s -o /dev/null -w "%{http_code}" --max-time 5 \
            "${target}${endpoint}" 2>/dev/null || echo "000")
        if [[ "${http_code}" == "200" ]]; then
            log_error "  FAIL: ${endpoint} accessible without auth (HTTP ${http_code})"
            add_result "auth-bypass:${endpoint}" "FAIL" "Accessible without authentication"
            findings=$((findings + 1))
        elif [[ "${http_code}" =~ ^(401|403)$ ]]; then
            log_success "  PASS: ${endpoint} requires authentication (HTTP ${http_code})"
            add_result "auth-bypass:${endpoint}" "PASS" "Properly protected"
        else
            log_info "  SKIP: ${endpoint} returned HTTP ${http_code}"
            add_result "auth-bypass:${endpoint}" "SKIP" "HTTP ${http_code}"
        fi
    done

    # Test 2: SQL injection on player search
    # WHY: Player search is a common injection point because it often uses
    # LIKE queries with user input. Successful SQLi can dump the entire
    # player database including payment info.
    log_info "  Testing: SQL injection on search endpoints..."
    local sqli_payloads=(
        "' OR '1'='1"
        "'; DROP TABLE players;--"
        "1 UNION SELECT username,password FROM users--"
        "admin'--"
    )
    local search_endpoints=(
        "/api/v1/players/search?q="
        "/api/v1/games/search?name="
        "/api/v1/transactions/search?player="
    )
    for endpoint in "${search_endpoints[@]}"; do
        for payload in "${sqli_payloads[@]}"; do
            local encoded_payload
            encoded_payload=$(python3 -c "import urllib.parse; print(urllib.parse.quote('${payload}'))" 2>/dev/null || echo "${payload}")
            local response
            response=$(curl -s --max-time 5 \
                -H "${AUTH_HEADER}: ${AUTH_TOKEN}" \
                "${target}${endpoint}${encoded_payload}" 2>/dev/null || echo "")
            # Check for SQL error indicators in response
            if echo "${response}" | grep -qi "sql\|syntax error\|postgresql\|ORA-\|mysql\|sqlite"; then
                log_error "  FAIL: Possible SQLi on ${endpoint} (SQL error in response)"
                add_result "sqli:${endpoint}" "FAIL" "SQL error leaked in response"
                findings=$((findings + 1))
                break
            fi
        done
    done
    log_success "  SQL injection tests completed"

    # Test 3: XSS on game lobby
    # WHY: XSS in the game lobby can steal player session tokens, redirect
    # to phishing pages, or inject malicious game iframes.
    log_info "  Testing: XSS on user-facing endpoints..."
    local xss_payloads=(
        "<script>alert(1)</script>"
        "\"><img src=x onerror=alert(1)>"
        "javascript:alert(1)"
    )
    for payload in "${xss_payloads[@]}"; do
        local encoded
        encoded=$(python3 -c "import urllib.parse; print(urllib.parse.quote('''${payload}'''))" 2>/dev/null || echo "${payload}")
        local response
        response=$(curl -s --max-time 5 \
            "${target}/lobby?search=${encoded}" 2>/dev/null || echo "")
        if echo "${response}" | grep -q "<script>alert(1)</script>"; then
            log_error "  FAIL: Reflected XSS found (payload echoed unescaped)"
            add_result "xss:lobby" "FAIL" "Reflected XSS in search parameter"
            findings=$((findings + 1))
            break
        fi
    done
    log_success "  XSS tests completed"

    # Test 4: Rate limiting verification
    # WHY: Without rate limiting, attackers can brute-force player passwords,
    # enumerate valid accounts, and abuse bonus/promotion endpoints.
    log_info "  Testing: Rate limiting on authentication..."
    local rate_limit_hit=false
    for i in $(seq 1 20); do
        local http_code
        http_code=$(curl -s -o /dev/null -w "%{http_code}" --max-time 5 \
            -X POST \
            -H "Content-Type: application/json" \
            -d '{"username":"test@test.com","password":"wrong"}' \
            "${target}/api/v1/auth/login" 2>/dev/null || echo "000")
        if [[ "${http_code}" == "429" ]]; then
            log_success "  PASS: Rate limiting active after ${i} attempts (HTTP 429)"
            add_result "rate-limit:login" "PASS" "Rate limited after ${i} attempts"
            rate_limit_hit=true
            break
        fi
    done
    if [[ "${rate_limit_hit}" == "false" ]]; then
        log_warn "  WARN: No rate limiting detected after 20 login attempts"
        add_result "rate-limit:login" "WARN" "No rate limiting after 20 attempts"
    fi

    # Close JSON report
    echo "" >> "${test_report}"
    echo '], "summary": {"findings": '"${findings}"', "timestamp": "'"$(date -u +%Y-%m-%dT%H:%M:%SZ)"'"}}' >> "${test_report}"

    log_info "Casino-specific test report: ${test_report}"
    return "${findings}"
}

# ---------------------------------------------------------------------------
# Results Summary
# ---------------------------------------------------------------------------
display_summary() {
    local zap_exit="$1"
    local casino_findings="$2"

    echo ""
    echo -e "${BOLD}=====================================================================${NC}"
    echo -e "${BOLD}  DAST Pipeline Results${NC}"
    echo -e "${BOLD}=====================================================================${NC}"
    echo ""
    printf "  %-25s %s\n" "ZAP Scan Exit:" "${zap_exit}"
    printf "  %-25s %s\n" "Casino-Specific Findings:" "${casino_findings}"
    echo ""
    echo "  Reports: ${REPORT_DIR}/"
    echo ""

    if [[ ${zap_exit} -gt 1 ]] || [[ ${casino_findings} -gt 0 ]]; then
        echo -e "  ${RED}${BOLD}GATE: FAILED${NC}"
        echo -e "  ${RED}Review reports and remediate before deployment.${NC}"
    else
        echo -e "  ${GREEN}${BOLD}GATE: PASSED${NC}"
    fi

    echo -e "${BOLD}=====================================================================${NC}"
    echo ""
}

# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------
main() {
    local scan_mode="baseline"
    local ci_mode=false

    while [[ $# -gt 0 ]]; do
        case "$1" in
            --target)
                TARGET_URL="${2:?'--target requires a URL'}"
                shift 2
                ;;
            --baseline)
                scan_mode="baseline"
                shift
                ;;
            --full)
                scan_mode="full"
                shift
                ;;
            --api-spec)
                API_SPEC="${2:?'--api-spec requires a path or URL'}"
                shift 2
                ;;
            --auth-token)
                AUTH_TOKEN="${2:?'--auth-token requires a token value'}"
                shift 2
                ;;
            --auth-header)
                AUTH_HEADER="${2:?'--auth-header requires a header name'}"
                shift 2
                ;;
            --ci)
                ci_mode=true  # Used to control output formatting
                export ci_mode
                shift
                ;;
            --output)
                REPORT_DIR="${2:?'--output requires a directory'}"
                shift 2
                ;;
            --help|-h)
                show_help
                exit 0
                ;;
            *)
                log_error "Unknown option: $1"
                show_help
                exit 2
                ;;
        esac
    done

    # Validate required parameters
    TARGET_URL="${TARGET_URL:-${TARGET_URL:-}}"
    if [[ -z "${TARGET_URL}" ]]; then
        log_error "--target URL is required"
        show_help
        exit 2
    fi

    echo ""
    echo -e "${BOLD}=====================================================================${NC}"
    echo -e "${BOLD}  iGaming DAST Pipeline${NC}"
    echo -e "${BOLD}=====================================================================${NC}"
    echo ""
    echo "  Target: ${TARGET_URL}"
    echo "  Mode:   ${scan_mode}"
    echo ""

    # Prerequisites
    check_docker
    check_target_reachable "${TARGET_URL}"
    ensure_report_dir

    local start_time
    start_time=$(date +%s)

    # Run ZAP scan
    local zap_exit=0
    case "${scan_mode}" in
        baseline)
            run_baseline_scan "${TARGET_URL}" || zap_exit=$?
            ;;
        full)
            run_full_scan "${TARGET_URL}" || zap_exit=$?
            ;;
    esac

    # Run API scan if spec provided
    if [[ -n "${API_SPEC}" ]]; then
        run_api_scan "${TARGET_URL}" "${API_SPEC}" || true
    fi

    # Run casino-specific tests
    local casino_findings=0
    run_casino_specific_tests "${TARGET_URL}" || casino_findings=$?

    local end_time
    end_time=$(date +%s)
    local duration=$((end_time - start_time))
    log_info "DAST pipeline completed in ${duration} seconds"

    # Display results
    display_summary "${zap_exit}" "${casino_findings}"

    # Exit code for CI
    if [[ ${zap_exit} -gt 1 ]] || [[ ${casino_findings} -gt 0 ]]; then
        exit 1
    fi
    exit 0
}

main "$@"
