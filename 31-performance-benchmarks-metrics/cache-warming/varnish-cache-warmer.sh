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

# Cache Warming for iGaming Varnish Cluster
# Run after: Varnish rollout, cluster restart, new deployment
# Usage: ./varnish-cache-warmer.sh [--endpoint URL] [--concurrent N] [--rounds N] [--verbose]

SCRIPT_NAME="$(basename "$0")"
readonly SCRIPT_NAME
START_TIME="$(date +%s)"
readonly START_TIME

# Defaults
ENDPOINT="${ENDPOINT:-https://localhost:30443}"
CONCURRENT="${CONCURRENT:-10}"
ROUNDS="${ROUNDS:-3}"
VERBOSE="${VERBOSE:-false}"
CURL_OPTS=("--connect-timeout" "5" "--max-time" "10" "-sk" "--keepalive-time" "30")

# Critical endpoints ordered by priority
# P0: Health/load balancer checks (TTL 2s)
# P1: Game lobby and odds feeds (TTL 2-5s)
# P2: Static assets (TTL 4h)
# P3: Game thumbnails (TTL 4h)
WARMUP_URLS=(
    # P0 - Health
    "/health"
    # P1 - Game lobby
    "/api/games"
    "/lobby"
    # P1 - Odds feeds
    "/api/odds"
    "/api/odds/live"
    "/api/odds/pre-match"
    "/api/odds/popular"
    # P1 - Game categories
    "/api/games/slots"
    "/api/games/live-casino"
    "/api/games/table-games"
    "/api/games/crash"
    # P2 - Static assets
    "/static/css/main.css"
    "/static/js/app.bundle.js"
    "/static/images/logo.png"
    # P3 - Popular game thumbnails (top 20)
    "/static/games/sweet-bonanza.webp"
    "/static/games/gates-of-olympus.webp"
    "/static/games/big-bass-bonanza.webp"
)

usage() {
    cat <<EOF
Usage: $SCRIPT_NAME [OPTIONS]

Options:
  --endpoint URL     Varnish endpoint (default: \$ENDPOINT or https://localhost:30443)
  --concurrent N     Parallel requests (default: 10)
  --rounds N         Warming rounds per URL (default: 3)
  --urls-file FILE   File with additional URLs to warm (one per line)
  --verbose          Show per-request details
  -h, --help         Show this help

Environment:
  ENDPOINT           Default endpoint URL
  CONCURRENT         Default concurrent requests
  ROUNDS             Default warming rounds

Examples:
  $SCRIPT_NAME --endpoint https://casino.example.com --rounds 5
  $SCRIPT_NAME --urls-file /tmp/varnish-cache-state-urls.txt --verbose
  ENDPOINT=http://varnish:6081 $SCRIPT_NAME
EOF
    exit 0
}

log() {
    local level="$1"
    shift
    printf '%s level=%s msg="%s"\n' "$(date -u +%Y-%m-%dT%H:%M:%SZ)" "$level" "$*"
}

log_verbose() {
    if [[ "$VERBOSE" == "true" ]]; then
        log "DEBUG" "$@"
    fi
}

parse_args() {
    while [[ $# -gt 0 ]]; do
        case "$1" in
            --endpoint)
                ENDPOINT="$2"
                shift 2
                ;;
            --concurrent)
                CONCURRENT="$2"
                shift 2
                ;;
            --rounds)
                ROUNDS="$2"
                shift 2
                ;;
            --urls-file)
                if [[ -f "$2" ]]; then
                    while IFS= read -r url; do
                        [[ -z "$url" || "$url" == \#* ]] && continue
                        WARMUP_URLS+=("$url")
                    done < "$2"
                    log "INFO" "Loaded additional URLs from $2"
                else
                    log "ERROR" "URLs file not found: $2"
                    exit 1
                fi
                shift 2
                ;;
            --verbose)
                VERBOSE="true"
                shift
                ;;
            -h|--help)
                usage
                ;;
            *)
                log "ERROR" "Unknown option: $1"
                usage
                ;;
        esac
    done
}

warm_url() {
    local url="$1"
    local round="$2"
    local full_url="${ENDPOINT}${url}"
    local http_code time_total

    local response
    response=$(curl "${CURL_OPTS[@]}" -o /dev/null \
        -w '%{http_code}|%{time_total}' \
        "$full_url" 2>/dev/null) || true

    http_code="${response%%|*}"
    time_total="${response##*|}"

    if [[ -z "$http_code" || "$http_code" == "000" ]]; then
        log_verbose "round=$round url=$url status=connection_failed"
        return 1
    elif [[ "$http_code" =~ ^[45] ]]; then
        log_verbose "round=$round url=$url status=$http_code time=${time_total}s"
        return 0  # 404s are expected in test environments
    else
        log_verbose "round=$round url=$url status=$http_code time=${time_total}s"
        return 0
    fi
}

verify_cache_hits() {
    local hits=0
    local misses=0
    local errors=0
    local total=${#WARMUP_URLS[@]}

    log "INFO" "Verifying cache hits for $total URLs..."

    for url in "${WARMUP_URLS[@]}"; do
        local full_url="${ENDPOINT}${url}"
        local headers
        headers=$(curl "${CURL_OPTS[@]}" -I "$full_url" 2>/dev/null) || true

        if [[ -z "$headers" ]]; then
            ((errors++)) || true
            continue
        fi

        local cache_header
        cache_header=$(echo "$headers" | grep -i "^X-Cache:" | tr -d '\r' || true)

        if echo "$cache_header" | grep -qi "HIT"; then
            ((hits++)) || true
            log_verbose "url=$url cache=HIT"
        elif [[ -n "$cache_header" ]]; then
            ((misses++)) || true
            log_verbose "url=$url cache=MISS header='$cache_header'"
        else
            # No X-Cache header -- might be a 404 or non-cacheable
            local status
            status=$(echo "$headers" | head -1 | awk '{print $2}')
            if [[ "$status" =~ ^[45] ]]; then
                ((errors++)) || true
                log_verbose "url=$url status=$status (not cacheable)"
            else
                ((misses++)) || true
                log_verbose "url=$url cache=NO_HEADER"
            fi
        fi
    done

    local cacheable=$((total - errors))
    local hit_rate=0
    if [[ "$cacheable" -gt 0 ]]; then
        hit_rate=$((hits * 100 / cacheable))
    fi

    log "INFO" "Cache verification: hits=$hits misses=$misses errors=$errors total=$total hit_rate=${hit_rate}%"

    # Return hit_rate via global variable to avoid stdout capture issues
    CACHE_HIT_RATE="$hit_rate"
}

run_warming() {
    local total_urls=${#WARMUP_URLS[@]}
    local total_requests=$((total_urls * ROUNDS))
    local succeeded=0
    local failed=0

    log "INFO" "Starting cache warming: endpoint=$ENDPOINT urls=$total_urls rounds=$ROUNDS concurrent=$CONCURRENT"

    for round in $(seq 1 "$ROUNDS"); do
        log "INFO" "Round $round/$ROUNDS..."

        local pids=()
        local active=0

        for url in "${WARMUP_URLS[@]}"; do
            warm_url "$url" "$round" &
            pids+=($!)
            ((active++)) || true

            if [[ "$active" -ge "$CONCURRENT" ]]; then
                for pid in "${pids[@]}"; do
                    if wait "$pid" 2>/dev/null; then
                        ((succeeded++)) || true
                    else
                        ((failed++)) || true
                    fi
                done
                pids=()
                active=0
            fi
        done

        # Wait for remaining
        for pid in "${pids[@]}"; do
            if wait "$pid" 2>/dev/null; then
                ((succeeded++)) || true
            else
                ((failed++)) || true
            fi
        done
    done

    local elapsed=$(( $(date +%s) - START_TIME ))

    log "INFO" "Warming complete: total_requests=$total_requests succeeded=$succeeded failed=$failed elapsed=${elapsed}s"

    # Verify cache state
    CACHE_HIT_RATE=0
    verify_cache_hits

    local end_elapsed=$(( $(date +%s) - START_TIME ))
    log "INFO" "Summary: urls_warmed=$total_urls rounds=$ROUNDS hit_rate=${CACHE_HIT_RATE}% total_time=${end_elapsed}s"

    if [[ "$CACHE_HIT_RATE" -lt 50 ]]; then
        log "WARN" "Cache hit rate below 50% -- check Varnish configuration and endpoint availability"
    fi
}

main() {
    parse_args "$@"
    run_warming
}

main "$@"
