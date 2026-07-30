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

# Pre-Rollout Cache State Capture
# Captures Varnish cache metrics before a rollout so the warmer
# knows the baseline to restore.
#
# Usage: ./pre-rollout-cache-save.sh [--namespace NS] [--output FILE]

SCRIPT_NAME="$(basename "$0")"
readonly SCRIPT_NAME
NAMESPACE="${NAMESPACE:-casino-prod}"
OUTPUT="${OUTPUT:-/tmp/varnish-cache-state.json}"
KUBECTL="${KUBECTL:-k3s kubectl}"

usage() {
    cat <<EOF
Usage: $SCRIPT_NAME [OPTIONS]

Captures Varnish cache state before rollout for post-rollout comparison.

Options:
  --namespace NS     Kubernetes namespace (default: casino-prod)
  --output FILE      Output file path (default: /tmp/varnish-cache-state.json)
  --kubectl CMD      kubectl command (default: k3s kubectl)
  -h, --help         Show this help

Environment:
  NAMESPACE          Default namespace
  KUBECTL            kubectl command to use
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
            --namespace)  NAMESPACE="$2"; shift 2 ;;
            --output)     OUTPUT="$2"; shift 2 ;;
            --kubectl)    KUBECTL="$2"; shift 2 ;;
            -h|--help)    usage ;;
            *)            log "ERROR" "Unknown option: $1"; usage ;;
        esac
    done
}

get_varnish_pods() {
    $KUBECTL get pods -n "$NAMESPACE" -l app=varnish \
        -o jsonpath='{.items[*].metadata.name}' 2>/dev/null
}

capture_pod_stats() {
    local pod="$1"
    local stats

    stats=$($KUBECTL exec -n "$NAMESPACE" "$pod" -- \
        varnishstat -1 -f MAIN.cache_hit,MAIN.cache_miss,MAIN.n_object,MAIN.n_expired,MAIN.s_resp_bodybytes,MAIN.uptime 2>/dev/null) || {
        log "WARN" "Failed to get stats from pod $pod"
        echo "{}"
        return
    }

    local cache_hit cache_miss n_object n_expired uptime
    cache_hit=$(echo "$stats" | awk '/MAIN.cache_hit / {print $2}' || echo "0")
    cache_miss=$(echo "$stats" | awk '/MAIN.cache_miss / {print $2}' || echo "0")
    n_object=$(echo "$stats" | awk '/MAIN.n_object / {print $2}' || echo "0")
    n_expired=$(echo "$stats" | awk '/MAIN.n_expired / {print $2}' || echo "0")
    uptime=$(echo "$stats" | awk '/MAIN.uptime / {print $2}' || echo "0")

    local hit_rate=0
    local total=$((cache_hit + cache_miss))
    if [[ "$total" -gt 0 ]]; then
        hit_rate=$((cache_hit * 10000 / total))
    fi

    printf '{"pod":"%s","cache_hit":%s,"cache_miss":%s,"n_object":%s,"n_expired":%s,"uptime":%s,"hit_rate_bps":%s}' \
        "$pod" "${cache_hit:-0}" "${cache_miss:-0}" "${n_object:-0}" "${n_expired:-0}" "${uptime:-0}" "$hit_rate"
}

capture_cached_urls() {
    local pod="$1"
    # Use varnishlog to capture recently accessed URLs (limited sample)
    local urls
    urls=$($KUBECTL exec -n "$NAMESPACE" "$pod" -- \
        timeout 3 varnishlog -d -c -I "ReqURL:" 2>/dev/null | \
        awk '/ReqURL/ {print $NF}' | sort -u | head -100) || true

    if [[ -n "$urls" ]]; then
        echo "$urls"
    fi
}

main() {
    parse_args "$@"

    log "INFO" "Capturing Varnish cache state namespace=$NAMESPACE output=$OUTPUT"

    local pods
    pods=$(get_varnish_pods)

    if [[ -z "$pods" ]]; then
        log "ERROR" "No Varnish pods found in namespace $NAMESPACE"
        exit 1
    fi

    local pod_count=0
    local pod_stats_array=""
    local all_urls=""

    for pod in $pods; do
        ((pod_count++)) || true
        log "INFO" "Capturing stats from pod $pod ($pod_count)"

        local stats
        stats=$(capture_pod_stats "$pod")

        if [[ -n "$pod_stats_array" ]]; then
            pod_stats_array="${pod_stats_array},$stats"
        else
            pod_stats_array="$stats"
        fi

        local urls
        urls=$(capture_cached_urls "$pod")
        if [[ -n "$urls" ]]; then
            all_urls="${all_urls}${urls}"$'\n'
        fi
    done

    # Deduplicate URLs
    local unique_urls
    unique_urls=$(echo "$all_urls" | sort -u | grep -v '^$' || true)
    local url_count
    url_count=$(echo "$unique_urls" | grep -c '' || echo "0")

    # Build URL JSON array
    local urls_json="[]"
    if [[ -n "$unique_urls" ]]; then
        urls_json="["
        local first=true
        while IFS= read -r url; do
            [[ -z "$url" ]] && continue
            if [[ "$first" == "true" ]]; then
                urls_json="${urls_json}\"$url\""
                first=false
            else
                urls_json="${urls_json},\"$url\""
            fi
        done <<< "$unique_urls"
        urls_json="${urls_json}]"
    fi

    # Write state file
    cat > "$OUTPUT" <<ENDJSON
{
  "timestamp": "$(date -u +%Y-%m-%dT%H:%M:%SZ)",
  "namespace": "$NAMESPACE",
  "pod_count": $pod_count,
  "pods": [$pod_stats_array],
  "cached_url_count": $url_count,
  "cached_urls": $urls_json
}
ENDJSON

    log "INFO" "Cache state saved: pods=$pod_count urls=$url_count output=$OUTPUT"
    log "INFO" "Use this file with: varnish-cache-warmer.sh --urls-file <(jq -r '.cached_urls[]' $OUTPUT)"
}

main "$@"
