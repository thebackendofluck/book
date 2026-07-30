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
# test-coraza.sh — Functional test suite for the Coraza WAF nginx module
# =============================================================================
# Tests that:
#   - Legitimate requests are NOT blocked (pass-through)
#   - SQL injection attacks ARE blocked (HTTP 403)
#   - XSS attacks ARE blocked (HTTP 403)
#   - Path traversal attacks ARE blocked (HTTP 403)
#   - iGaming API whitelist rules work (game API, WebSocket, Prometheus)
#   - Scanner user agents are blocked
#
# Works with both deployment modes:
#   - Docker (nginx-coraza image):  ./test-coraza.sh localhost 80
#   - Bare-metal nginx module:      ./test-coraza.sh localhost 80
#   - Remote server:                ./test-coraza.sh 203.0.113.1 80
#
# Usage:
#   ./test-coraza.sh [host] [port]
#   ./test-coraza.sh localhost 80     # default
#   ./test-coraza.sh 203.0.113.1 80
#
# Prerequisites:
#   - nginx with the WAF module must be running and listening on [host]:[port]
#   - curl must be installed
#
# Exit code: 0 = all tests passed, 1 = one or more tests failed
# =============================================================================
set -uo pipefail

WAF_HOST="${1:-localhost}"
WAF_PORT="${2:-80}"
WAF_BASE="http://${WAF_HOST}:${WAF_PORT}"

PASS=0
FAIL=0
SKIP=0

# ANSI colours
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m'

# ---------------------------------------------------------------------------
# Helper functions
# ---------------------------------------------------------------------------
log()  { echo "[$(date '+%H:%M:%S')] $*"; }
pass() { echo -e "${GREEN}[PASS]${NC} $*"; ((PASS++)); }
fail() { echo -e "${RED}[FAIL]${NC} $*"; ((FAIL++)); }
info() { echo -e "${YELLOW}[INFO]${NC} $*"; }

# Send a request and return the HTTP status code
http_status() {
    curl -s -o /dev/null -w "%{http_code}" \
        --max-time 10 \
        --connect-timeout 5 \
        "$@"
}

# Assert that a request returns the expected status
# Usage: assert_status "description" "200" [curl options] url
# shellcheck disable=SC2329  # invoked indirectly from caller-supplied test cases
assert_status() {
    local desc="$1"
    local expected="$2"
    shift 2
    local actual
    actual="$(http_status "$@")"
    if [[ "${actual}" == "${expected}" ]]; then
        pass "${desc} (got ${actual})"
    else
        fail "${desc} (expected ${expected}, got ${actual})"
    fi
}

# Assert that a request does NOT return 403/400
assert_allowed() {
    local desc="$1"
    shift
    local actual
    actual="$(http_status "$@")"
    if [[ "${actual}" == "403" || "${actual}" == "400" ]]; then
        fail "${desc} — unexpectedly blocked (${actual})"
    else
        pass "${desc} — passed through (${actual})"
    fi
}

# Assert that a request returns 403
assert_blocked() {
    local desc="$1"
    shift
    local actual
    actual="$(http_status "$@")"
    if [[ "${actual}" == "403" ]]; then
        pass "${desc} — correctly blocked (403)"
    else
        fail "${desc} — NOT blocked (got ${actual}, expected 403)"
    fi
}

# ---------------------------------------------------------------------------
# Pre-flight: verify nginx is reachable and WAF is active
# ---------------------------------------------------------------------------
log "Connecting to nginx+WAF at ${WAF_BASE}..."

HEALTH=$(curl -s -o /dev/null -w "%{http_code}" --max-time 5 "${WAF_BASE}/health" 2>/dev/null || echo "000")
if [[ "${HEALTH}" == "000" ]]; then
    echo -e "${RED}ERROR:${NC} Cannot reach nginx at ${WAF_BASE}."
    echo "  Docker mode:     docker compose -f docker-compose.yml ps"
    echo "  Bare-metal:      systemctl status nginx"
    exit 1
fi
log "nginx reachable (health endpoint: ${HEALTH})"

# Verify WAF is enforcing (not just running)
# A known-bad request must return 403, not 200 or 404
WAF_CHECK=$(curl -s -o /dev/null -w "%{http_code}" --max-time 5 \
    "${WAF_BASE}/?test=1+OR+1=1" 2>/dev/null || echo "000")
if [[ "${WAF_CHECK}" == "403" ]]; then
    log "WAF enforcement: active (SQLi blocked)"
elif [[ "${WAF_CHECK}" == "000" ]]; then
    info "WAF reachability check inconclusive (000) — proceeding with tests"
else
    info "WAF may be in DetectionOnly mode (SQLi returned ${WAF_CHECK} instead of 403)"
    info "Check: grep SecRuleEngine /etc/nginx/coraza/coraza.conf"
fi
echo

# ---------------------------------------------------------------------------
# Test Group 1: Legitimate requests must pass through
# ---------------------------------------------------------------------------
info "=== Group 1: Legitimate requests (must NOT be blocked) ==="

assert_allowed "GET /" \
    "${WAF_BASE}/"

assert_allowed "GET /api/v2/user/profile" \
    -H "Authorization: Bearer REDACTED_TOKEN" \
    "${WAF_BASE}/api/v2/user/profile"

assert_allowed "POST /api/v2/auth/login (valid JSON)" \
    -X POST \
    -H "Content-Type: application/json" \
    -d '{"username":"player@example.com","password":"SecurePass123!"}' \
    "${WAF_BASE}/api/v2/auth/login"

assert_allowed "Player search with apostrophe in name" \
    "${WAF_BASE}/api/v2/admin/players?player_name=O%27Brien"

assert_allowed "Bonus code with hyphen" \
    "${WAF_BASE}/api/v2/bonuses/claim?bonus_code=WELCOME-100"

echo

# ---------------------------------------------------------------------------
# Test Group 2: Attack payloads must be blocked
# ---------------------------------------------------------------------------
info "=== Group 2: Attack payloads (must return 403) ==="

assert_blocked "SQLi: classic OR 1=1" \
    "${WAF_BASE}/api/v2/user/profile?id=1+OR+1=1"

assert_blocked "SQLi: UNION SELECT" \
    "${WAF_BASE}/api/v2/search?q=test'+UNION+SELECT+1,2,3--"

assert_blocked "SQLi: stacked queries" \
    "${WAF_BASE}/api/v2/user/profile?id=1;DROP+TABLE+users--"

assert_blocked "XSS: script tag in query" \
    "${WAF_BASE}/api/v2/search?q=<script>alert(1)</script>"

assert_blocked "XSS: event handler" \
    "${WAF_BASE}/api/v2/search?q=<img+src=x+onerror=alert(1)>"

assert_blocked "XSS: javascript: URL" \
    "${WAF_BASE}/api/v2/redirect?url=javascript:alert(document.cookie)"

assert_blocked "Path traversal: ../../etc/passwd" \
    "${WAF_BASE}/api/v2/files?path=../../etc/passwd"

assert_blocked "Path traversal: URL-encoded" \
    "${WAF_BASE}/api/v2/files?path=..%2F..%2Fetc%2Fpasswd"

assert_blocked "RCE: shell command injection" \
    "${WAF_BASE}/api/v2/search?q=;cat+/etc/passwd"

assert_blocked "Scanner user agent: sqlmap" \
    -H "User-Agent: sqlmap/1.7.8" \
    "${WAF_BASE}/"

assert_blocked "Scanner user agent: nikto" \
    -H "User-Agent: Nikto/2.1.6" \
    "${WAF_BASE}/"

echo

# ---------------------------------------------------------------------------
# Test Group 3: iGaming whitelist rules
# ---------------------------------------------------------------------------
info "=== Group 3: iGaming whitelist rules (must NOT be blocked) ==="

GAL_PAYLOAD='{"round_id":"1234567890","game_state":"eyJiYXNlNjQiOiJ0ZXN0In0=","bet":5.00,"win":0}'
assert_allowed "GAL API: game state JSON (would trigger SQLi rules without exclusion)" \
    -X POST \
    -H "Content-Type: application/json" \
    -d "${GAL_PAYLOAD}" \
    "${WAF_BASE}/api/v2/gal/round/start"

assert_allowed "Prometheus metrics endpoint" \
    "${WAF_BASE}/prometheus/metrics"

WALLET_PAYLOAD="$(python3 -c "import json; print(json.dumps({'type':'deposit','amount':100.00,'currency':'EUR','data':'x'*2048}))" 2>/dev/null || echo '{"type":"deposit","amount":100.00}')"
assert_allowed "Wallet API: large JSON payload" \
    -X POST \
    -H "Content-Type: application/json" \
    -d "${WALLET_PAYLOAD}" \
    "${WAF_BASE}/api/v2/wallet/deposit"

LONG_JWT="Bearer $(python3 -c "print('A'*512)" 2>/dev/null || echo 'AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA')"
assert_allowed "Long JWT in Authorization header" \
    -H "Authorization: ${LONG_JWT}" \
    "${WAF_BASE}/api/v2/user/profile"

echo

# ---------------------------------------------------------------------------
# Test Group 4: WebSocket upgrade
# ---------------------------------------------------------------------------
info "=== Group 4: WebSocket upgrade ==="

WS_STATUS=$(curl -s -o /dev/null -w "%{http_code}" \
    --max-time 5 \
    -H "Upgrade: websocket" \
    -H "Connection: Upgrade" \
    -H "Sec-WebSocket-Key: dGhlIHNhbXBsZSBub25jZQ==" \
    -H "Sec-WebSocket-Version: 13" \
    "${WAF_BASE}/ws/live-casino" 2>/dev/null || echo "000")

if [[ "${WS_STATUS}" == "403" ]]; then
    fail "WebSocket upgrade blocked by WAF (got 403)"
elif [[ "${WS_STATUS}" == "000" ]]; then
    info "WebSocket test skipped (endpoint not available in test env)"
    ((SKIP++))
else
    pass "WebSocket upgrade not blocked by WAF (got ${WS_STATUS})"
fi

echo

# ---------------------------------------------------------------------------
# Test Group 5: Header injection and encoding attacks
# ---------------------------------------------------------------------------
info "=== Group 5: Header injection and encoding attacks ==="

assert_blocked "HTTP header injection via newline" \
    "${WAF_BASE}/api/v2/search?q=test%0d%0aX-Injected-Header:malicious"

assert_blocked "Null byte injection" \
    "${WAF_BASE}/api/v2/files?path=file.txt%00.php"

assert_blocked "Double URL encoding" \
    "${WAF_BASE}/api/v2/files?path=..%252F..%252Fetc%252Fpasswd"

echo

# ---------------------------------------------------------------------------
# Summary
# ---------------------------------------------------------------------------
TOTAL=$((PASS + FAIL + SKIP))
echo "============================================"
echo "  Coraza WAF Test Results"
echo "============================================"
echo -e "  ${GREEN}PASS${NC}  : ${PASS}"
echo -e "  ${RED}FAIL${NC}  : ${FAIL}"
echo -e "  ${YELLOW}SKIP${NC}  : ${SKIP}"
echo "  TOTAL : ${TOTAL}"
echo "============================================"

if [[ ${FAIL} -gt 0 ]]; then
    echo
    echo -e "${RED}FAILED: ${FAIL} test(s) failed. Review WAF logs:${NC}"
    echo "  Docker mode:"
    echo "    docker logs nginx-coraza"
    echo "    docker exec nginx-coraza tail -50 /var/log/coraza/audit.log"
    echo "  Bare-metal mode:"
    echo "    tail -50 /var/log/coraza/audit.log"
    echo "    tail -20 /var/log/nginx/error.log"
    exit 1
fi

echo
echo -e "${GREEN}All tests passed.${NC}"
echo "Next step: ./deploy-coraza.sh --env staging"
exit 0
