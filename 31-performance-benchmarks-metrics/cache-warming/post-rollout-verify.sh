#!/usr/bin/env bash
# Companion code for "The Backend of Luck" - Chapter 31, Performance Benchmarks and Metrics.
# https://thebackendofluck.com | https://github.com/thebackendofluck/book
# SPDX-License-Identifier: Apache-2.0
#
# FOR TESTING AND EVALUATION ONLY. NOT FOR PRODUCTION USE.
# Published to demonstrate the patterns explained in the book. This code is
# not certified for real-money gaming: operating a gambling platform requires
# your own licence, independent test-lab certification (GLI, eCOGRA or
# equivalent) and regulator approval.

set -euo pipefail

# Post-Rollout Cache Verification
# Verifies that critical endpoints return X-Cache: HIT after warming.
# Can be used as a Kubernetes readiness gate or CI/CD pipeline gate.
#
# Usage: ./post-rollout-verify.sh [--endpoint URL] [--gate] [--threshold N]

SCRIPT_NAME="$(basename "$0")"
readonly SCRIPT_NAME
ENDPOINT="${ENDPOINT:-https://localhost:30443}"
GATE_MODE="${GATE_MODE:-false}"
THRESHOLD="${THRESHOLD:-80}"
CURL_OPTS=("--connect-timeout" "5" "--max-time" "10" "-sk")

# Critical endpoints that MUST be cached in production
# P0 and P1 endpoints are mandatory for gate mode
P0_URLS=("/health")
P1_URLS=(
    "/api/games"
    "/lobby"
    "/api/odds"
    "/api/odds/live"
    "/api/odds/pre-match"
    "/api/odds/popular"
    "/api/games/slots"
    "/api/games/live-casino"
    "/api/games/table-games"
    "/api/games/crash"
)
P2_URLS=(
    "/static/css/main.css"
    "/static/js/app.bundle.js"
    "/static/images/logo.png"
)

usage() {
    cat <<EOF
Usage: $SCRIPT_NAME [OPTIONS]

Verifies Varnish cache is warm for all critical endpoints.

Options:
  --endpoint URL     Varnish endpoint (default: \$ENDPOINT or https://localhost:30443)
  --gate             Gate mode: exit non-zero if cache is not warm (for CI/CD or readiness probes)
  --threshold N      Minimum cache hit rate % to pass gate (default: 80)
  -h, --help         Show this help

Gate mode:
  In gate mode, the script exits with code 0 only if:
    - ALL P0 endpoints return X-Cache: HIT
    - ALL P1 endpoints return X-Cache: HIT
    - Overall hit rate >= threshold

  Use as a readiness gate:
    ./post-rollout-verify.sh --gate --endpoint http://varnish:6081

  Use in CI/CD pipeline:
    ./post-rollout-verify.sh --gate --threshold 90 || exit 1

Exit codes:
  0    Cache is warm (or non-gate mode)
  1    Cache is cold (gate mode only)
  2    Critical P0/P1 endpoints not cached (gate mode only)
EOF
    exit 0
}

log() {
    local level="$1"
    shift
    printf '%s level=%s msg="%s"\n' "$(date -u +%Y-%m-%dT%H:%M:%SZ)" "$level" "$*"
}

parse_args() {
    while [[ $# -gt 0 ]]; do
        case "$1" in
            --endpoint)   ENDPOINT="$2"; shift 2 ;;
            --gate)       GATE_MODE="true"; shift ;;
            --threshold)  THRESHOLD="$2"; shift 2 ;;
            -h|--help)    usage ;;
            *)            log "ERROR" "Unknown option: $1"; usage ;;
        esac
    done
}

check_url() {
    local url="$1"
    local priority="$2"
    local full_url="${ENDPOINT}${url}"

    local headers
    headers=$(curl "${CURL_OPTS[@]}" -I "$full_url" 2>/dev/null) || {
        printf "url=%s priority=%s cache=ERROR status=connection_failed\n" "$url" "$priority"
        return 2
    }

    local status
    status=$(echo "$headers" | head -1 | awk '{print $2}')

    local cache_header
    cache_header=$(echo "$headers" | grep -i "^X-Cache:" | tr -d '\r' || true)

    local age_header
    age_header=$(echo "$headers" | grep -i "^Age:" | awk '{print $2}' | tr -d '\r' || true)

    if echo "$cache_header" | grep -qi "HIT"; then
        printf "url=%s priority=%s cache=HIT status=%s age=%s\n" "$url" "$priority" "$status" "${age_header:-N/A}"
        return 0
    elif [[ "$status" =~ ^[45] ]]; then
        printf "url=%s priority=%s cache=N/A status=%s (endpoint not available)\n" "$url" "$priority" "$status"
        return 3  # Not an error for gate purposes -- endpoint may not exist in test env
    else
        printf "url=%s priority=%s cache=MISS status=%s\n" "$url" "$priority" "$status"
        return 1
    fi
}

main() {
    parse_args "$@"

    log "INFO" "Verifying cache state endpoint=$ENDPOINT gate=$GATE_MODE threshold=${THRESHOLD}%"

    local total=0
    local hits=0
    local misses=0
    local errors=0
    local not_available=0
    local p0_pass=true
    local p1_pass=true

    echo ""
    echo "=== P0 Endpoints (Health/Status) ==="
    for url in "${P0_URLS[@]}"; do
        ((total++)) || true
        local result rc
        result=$(check_url "$url" "P0") && rc=$? || rc=$?
        echo "  $result"

        case $rc in
            0) ((hits++)) || true ;;
            1) ((misses++)) || true; p0_pass=false ;;
            2) ((errors++)) || true; p0_pass=false ;;
            3) ((not_available++)) || true ;;
        esac
    done

    echo ""
    echo "=== P1 Endpoints (Game Lobby, Odds) ==="
    for url in "${P1_URLS[@]}"; do
        ((total++)) || true
        local result rc
        result=$(check_url "$url" "P1") && rc=$? || rc=$?
        echo "  $result"

        case $rc in
            0) ((hits++)) || true ;;
            1) ((misses++)) || true; p1_pass=false ;;
            2) ((errors++)) || true; p1_pass=false ;;
            3) ((not_available++)) || true ;;
        esac
    done

    echo ""
    echo "=== P2 Endpoints (Static Assets) ==="
    for url in "${P2_URLS[@]}"; do
        ((total++)) || true
        local result rc
        result=$(check_url "$url" "P2") && rc=$? || rc=$?
        echo "  $result"

        case $rc in
            0) ((hits++)) || true ;;
            1) ((misses++)) || true ;;
            2) ((errors++)) || true ;;
            3) ((not_available++)) || true ;;
        esac
    done

    echo ""

    local cacheable=$((total - not_available))
    local hit_rate=0
    if [[ "$cacheable" -gt 0 ]]; then
        hit_rate=$((hits * 100 / cacheable))
    fi

    log "INFO" "Results: total=$total hits=$hits misses=$misses errors=$errors not_available=$not_available hit_rate=${hit_rate}%"

    if [[ "$GATE_MODE" == "true" ]]; then
        echo ""
        echo "=== Gate Decision ==="

        if [[ "$p0_pass" == "false" ]]; then
            log "ERROR" "GATE FAILED: P0 endpoints (health/status) are not cached"
            log "ERROR" "NEVER release traffic to a Varnish pod with cold cache in production"
            exit 2
        fi

        if [[ "$p1_pass" == "false" ]]; then
            log "ERROR" "GATE FAILED: P1 endpoints (game lobby, odds) are not cached"
            log "ERROR" "Cold P1 cache at 50K+ concurrent users = instant incident"
            exit 2
        fi

        if [[ "$hit_rate" -lt "$THRESHOLD" ]]; then
            log "ERROR" "GATE FAILED: Cache hit rate ${hit_rate}% below threshold ${THRESHOLD}%"
            exit 1
        fi

        log "INFO" "GATE PASSED: All critical endpoints cached, hit_rate=${hit_rate}%"
        exit 0
    fi
}

main "$@"
